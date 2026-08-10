#!/usr/bin/env python3
"""Install Codebase Documentation Kit v2 without replacing unowned content."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shlex
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SKILLS = ("codebase-documentation-architect", "codebase-documentation-maintainer")
OWNER_FILE = ".codebase-documentation-kit-owner.json"
MANIFEST_FORMAT = 1
TOOLKIT_NAME = "codebase-documentation-kit-v2"


class InstallError(Exception):
    """A preflight or transactional installation error."""


@dataclass(frozen=True)
class Integration:
    provider: str
    config_path: Path
    skills_root: Path
    command: str
    owned_script: Path | None
    command_windows: str | None = None


@dataclass
class Change:
    destination: Path
    staged: Path | None
    description: str
    backup: Path | None = None
    applied: bool = False


def path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink() or path_is_reparse_point(path)


def path_is_reparse_point(path: Path) -> bool:
    """Detect Windows junctions and other reparse points on Python 3.10+."""
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and attributes & flag)


def path_is_unsafe_link(path: Path) -> bool:
    return path.is_symlink() or path_is_reparse_point(path)


def fail(message: str) -> None:
    raise InstallError(message)


def load_json(path: Path) -> dict[str, Any]:
    if not path_exists(path):
        return {}
    if path.is_symlink() or not path.is_file():
        fail(f"Cannot safely modify {path}: expected a regular file")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"Cannot safely modify {path}: {exc}")
    if not isinstance(obj, dict):
        fail(f"Cannot safely modify {path}: root must be an object")
    return obj


def get_hooks(obj: dict[str, Any], path: Path) -> dict[str, Any]:
    value = obj.get("hooks")
    if value is None:
        value = {}
        obj["hooks"] = value
    if not isinstance(value, dict):
        fail(f"Cannot safely modify {path}: top-level hooks must be an object")
    for event in ("SessionStart", "Stop"):
        if event in value and not isinstance(value[event], list):
            fail(f"Cannot safely modify {path}: hooks.{event} must be a list")
    return value


def normalized_command(command: str) -> str:
    command = command.strip()
    return command.casefold() if os.name == "nt" else command


def normalized_path_text(value: str) -> str:
    value = value.replace("\\", "/")
    return value.casefold() if os.name == "nt" else value


def command_ends_with_script(command: str, script: Path) -> bool:
    """Match the canonical user runtime script independently of its interpreter."""
    command = normalized_path_text(command.strip())
    script_text = normalized_path_text(str(script))
    for quoted_script in (script_text, f'"{script_text}"', f"'{script_text}'"):
        if not command.endswith(quoted_script):
            continue
        prefix = command[: -len(quoted_script)]
        if prefix and prefix[-1].isspace():
            return True
    return False


def handler_is_ours(handler: dict[str, Any], canonical_command: str, owned_script: Path | None = None) -> bool:
    """Recognize only the exact command generated for this integration.

    A hook's script basename and arbitrary marker text are not ownership
    identifiers: users commonly have unrelated audit scripts with both.
    """
    if handler.get("type") != "command" or not isinstance(handler.get("command"), str):
        return False
    command = handler["command"]
    if normalized_command(command) == normalized_command(canonical_command):
        return True
    return owned_script is not None and command_ends_with_script(command, owned_script)


def remove_our_hook_groups(hooks: dict[str, Any], event: str, command: str, owned_script: Path | None = None) -> None:
    groups = hooks.get(event)
    if not isinstance(groups, list):
        return
    cleaned: list[Any] = []
    for group in groups:
        if not isinstance(group, dict):
            cleaned.append(group)
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            cleaned.append(group)
            continue
        kept = [
            handler
            for handler in handlers
            if not (isinstance(handler, dict) and handler_is_ours(handler, command, owned_script))
        ]
        if kept:
            preserved = dict(group)
            preserved["hooks"] = kept
            cleaned.append(preserved)
    if cleaned:
        hooks[event] = cleaned
    else:
        hooks.pop(event, None)


def add_hook(obj: dict[str, Any], event: str, handler: dict[str, Any], matcher: str | None = None) -> None:
    hooks = obj.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        fail("Cannot safely modify hook configuration: top-level hooks must be an object")
    group: dict[str, Any] = {"hooks": [handler]}
    if matcher:
        group["matcher"] = matcher
    hooks.setdefault(event, []).append(group)


def quoted(value: str) -> str:
    if os.name == "nt":
        return f'"{value}"'
    return shlex.quote(value)


def powershell_quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def user_runtime() -> Path:
    return Path.home() / ".codebase-documentation-kit" / "runtime"


def project_runtime(repo: Path) -> Path:
    return repo / ".codebase-documentation-kit" / "runtime"


def command_for(provider: str, scope: str, runtime: Path) -> str:
    hook = f"hook_{provider}.py"
    if scope == "user":
        return f"{quoted(sys.executable)} {quoted(str(runtime / hook))}"
    if provider == "codex":
        return 'python3 "$(git rev-parse --show-toplevel)/.codebase-documentation-kit/runtime/hook_codex.py"'
    return 'python3 "${CLAUDE_PROJECT_DIR}/.codebase-documentation-kit/runtime/hook_claude.py"'


def command_windows_for(provider: str, scope: str, runtime: Path) -> str | None:
    if provider != "codex":
        return None
    if scope == "user":
        if os.name != "nt":
            return None
        hook = runtime / "hook_codex.py"
        return f"& {powershell_quoted(sys.executable)} {powershell_quoted(str(hook))}"
    if scope != "project":
        return None
    # Codex supports a Windows-specific override. Avoid shell-specific git command
    # substitution by locating the nearest Git root in Python, then executing the
    # committed project hook with the same interpreter.
    code = (
        "from pathlib import Path; import runpy; "
        "p=Path.cwd(); "
        "r=next((x for x in (p,*p.parents) if (x/'.git').exists()),p); "
        "runpy.run_path(str(r/'.codebase-documentation-kit/runtime/hook_codex.py'),run_name='__main__')"
    )
    return f'python -c "{code}"'


def integrations_for(scope: str, repo: Path | None) -> tuple[dict[str, Integration], Path, Path]:
    if scope == "user":
        home = Path.home()
        runtime = user_runtime()
        return (
            {
                "codex": Integration("codex", home / ".codex" / "hooks.json", home / ".codex" / "skills", command_for("codex", scope, runtime), runtime / "hook_codex.py", command_windows_for("codex", scope, runtime)),
                "claude": Integration("claude", home / ".claude" / "settings.json", home / ".claude" / "skills", command_for("claude", scope, runtime), runtime / "hook_claude.py", command_windows_for("claude", scope, runtime)),
            },
            runtime,
            home,
        )
    assert repo is not None
    runtime = project_runtime(repo)
    return (
        {
            "codex": Integration("codex", repo / ".codex" / "hooks.json", repo / ".codex" / "skills", command_for("codex", scope, runtime), None, command_windows_for("codex", scope, runtime)),
            "claude": Integration("claude", repo / ".claude" / "settings.json", repo / ".claude" / "skills", command_for("claude", scope, runtime), None, command_windows_for("claude", scope, runtime)),
        },
        runtime,
        repo,
    )


def prepare_config(original: dict[str, Any], integration: Integration, uninstall: bool) -> dict[str, Any]:
    obj = copy.deepcopy(original)
    hooks = get_hooks(obj, integration.config_path)
    for event in ("SessionStart", "Stop"):
        remove_our_hook_groups(hooks, event, integration.command, integration.owned_script)
    if not uninstall:
        handler = {"type": "command", "command": integration.command}
        if integration.command_windows:
            handler["commandWindows"] = integration.command_windows
        start = dict(handler)
        start["timeout"] = 5
        stop = dict(handler)
        stop["timeout"] = 10
        matcher = "startup|resume|clear" + ("|fork" if integration.provider == "claude" else "")
        add_hook(obj, "SessionStart", start, matcher)
        add_hook(obj, "Stop", stop)
    if not obj.get("hooks"):
        obj.pop("hooks", None)
    return obj


def config_has_our_hooks(obj: dict[str, Any], integration: Integration) -> bool:
    hooks = obj.get("hooks", {})
    if not isinstance(hooks, dict):
        return False
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            handlers = group.get("hooks")
            if isinstance(handlers, list) and any(
                isinstance(handler, dict)
                and handler_is_ours(handler, integration.command, integration.owned_script)
                for handler in handlers
            ):
                return True
    return False


def config_references_runtime(obj: dict[str, Any], runtime: Path) -> bool:
    """Retain an owned runtime if any non-toolkit hook may still use it."""
    hooks = obj.get("hooks", {})
    if not isinstance(hooks, dict):
        return False
    absolute = str(runtime).replace("\\", "/").casefold()
    project_fragment = ".codebase-documentation-kit/runtime/"
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            for handler in group["hooks"]:
                if not isinstance(handler, dict) or not isinstance(handler.get("command"), str):
                    continue
                command = handler["command"].replace("\\", "/").casefold()
                if absolute in command or (
                    project_fragment in command
                    and ("hook_codex.py" in command or "hook_claude.py" in command)
                ):
                    return True
    return False


def ignored_generated_path(relative: Path) -> bool:
    return "__pycache__" in relative.parts or relative.suffix in {".pyc", ".pyo"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_state(root: Path, ignore_owner_file: bool = False) -> dict[str, Any]:
    if path_is_unsafe_link(root) or not root.is_dir():
        fail(f"Ownership conflict at {root}: expected a real directory")
    files: dict[str, str] = {}
    directories: list[str] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        retained_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current_path / name
            relative = child.relative_to(root)
            if ignored_generated_path(relative):
                continue
            if path_is_unsafe_link(child) or os.path.ismount(child):
                fail(f"Ownership conflict at {child}: links, mounts, and reparse points are not supported")
            retained_dirs.append(name)
            directories.append(relative.as_posix())
        dirnames[:] = retained_dirs
        for name in sorted(filenames):
            child = current_path / name
            relative = child.relative_to(root)
            if ignored_generated_path(relative):
                continue
            if path_is_unsafe_link(child) or not child.is_file():
                fail(f"Ownership conflict at {child}: expected a regular file")
            if ignore_owner_file and relative == Path(OWNER_FILE):
                continue
            files[relative.as_posix()] = sha256_file(child)
    return {"directories": sorted(directories), "files": files}


def make_manifest(kind: str, name: str, source: Path) -> dict[str, Any]:
    state = tree_state(source)
    return {
        "format": MANIFEST_FORMAT,
        "toolkit": TOOLKIT_NAME,
        "kind": kind,
        "name": name,
        "directories": state["directories"],
        "files": state["files"],
    }


def assert_owned_tree(path: Path, kind: str, name: str) -> None:
    if not path_exists(path):
        return
    if path_is_unsafe_link(path) or not path.is_dir():
        fail(f"Ownership conflict at {path}: not a toolkit-owned directory")
    owner_path = path / OWNER_FILE
    if path_is_unsafe_link(owner_path) or not owner_path.is_file():
        fail(f"Ownership conflict at {path}: ownership manifest is missing")
    try:
        manifest = json.loads(owner_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"Ownership conflict at {path}: invalid ownership manifest ({exc})")
    if not isinstance(manifest, dict) or any(
        manifest.get(key) != value
        for key, value in {
            "format": MANIFEST_FORMAT,
            "toolkit": TOOLKIT_NAME,
            "kind": kind,
            "name": name,
        }.items()
    ):
        fail(f"Ownership conflict at {path}: manifest does not belong to this toolkit")
    expected = {"directories": manifest.get("directories"), "files": manifest.get("files")}
    if not isinstance(expected["directories"], list) or not isinstance(expected["files"], dict):
        fail(f"Ownership conflict at {path}: malformed ownership manifest")
    actual = tree_state(path, ignore_owner_file=True)
    if actual != expected:
        fail(f"Ownership conflict at {path}: installed files have been modified")


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def assert_safe_destination(destination: Path, scope_root: Path) -> None:
    """Reject destination escape and link-like components below the scope root."""
    lexical_root = scope_root.absolute()
    lexical_destination = destination.absolute()
    if not is_relative_to(lexical_destination, lexical_root):
        fail(f"Cannot safely modify {destination}: destination is outside installation scope {scope_root}")

    try:
        resolved_root = scope_root.resolve(strict=True)
        resolved_destination = destination.resolve(strict=False)
    except OSError as exc:
        fail(f"Cannot safely resolve {destination}: {exc}")
    if not is_relative_to(resolved_destination, resolved_root):
        fail(f"Cannot safely modify {destination}: resolved destination escapes {resolved_root}")

    current = scope_root
    relative = lexical_destination.relative_to(lexical_root)
    for part in relative.parts:
        current = current / part
        if not path_exists(current):
            continue
        if path_is_unsafe_link(current) or os.path.ismount(current):
            fail(f"Cannot safely modify {destination}: unsafe destination component {current}")


def preflight_parent(path: Path, scope_root: Path) -> None:
    assert_safe_destination(path, scope_root)
    current = path
    while not path_exists(current):
        parent = current.parent
        if parent == current:
            fail(f"Cannot safely create {path}: no existing parent directory")
        current = parent
    if path_is_unsafe_link(current) or not current.is_dir():
        fail(f"Cannot safely create {path}: parent {current} is not a real directory")


def preflight_config_destination(path: Path, scope_root: Path) -> None:
    preflight_parent(path.parent, scope_root)
    assert_safe_destination(path, scope_root)
    if path_exists(path) and (path_is_unsafe_link(path) or not path.is_file()):
        fail(f"Cannot safely modify {path}: expected a regular file")


def preflight_tree_destination(path: Path, kind: str, name: str, destructive: bool, scope_root: Path) -> None:
    preflight_parent(path.parent, scope_root)
    assert_safe_destination(path, scope_root)
    if path_exists(path) and destructive:
        assert_owned_tree(path, kind, name)


def runtime_removal_target(runtime: Path) -> Path:
    """Remove the containing toolkit directory only when it has no other content."""
    container = runtime.parent
    if path_is_unsafe_link(container) or not container.is_dir():
        return runtime
    try:
        children = list(container.iterdir())
    except OSError:
        return runtime
    return container if children == [runtime] else runtime


def remove_path(path: Path) -> None:
    if not path_exists(path):
        return
    if path_is_unsafe_link(path) or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        fail(f"Cannot safely replace {path}: unsupported destination type")


def _read_regular_text(path: Path, label: str) -> str:
    if path_is_unsafe_link(path) or not path.is_file():
        fail(f"Cannot safely inspect {label} at {path}: expected a regular file")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        fail(f"Cannot safely inspect {label} at {path}: {exc}")


def _is_known_v1_architect_tree(path: Path) -> bool:
    """Recognize the exact legacy skill family shipped before the V2 kit.

    V1 predates ownership manifests, so migration uses multiple stable markers
    instead of claiming any directory that merely has the same basename.
    """
    skill_file = path / "SKILL.md"
    checklist = path / "references" / "bootstrap-checklist.md"
    validator = path / "scripts" / "validate_docs_model.py"
    if not all(path_exists(item) for item in (skill_file, checklist, validator)):
        return False
    try:
        skill_text = _read_regular_text(skill_file, "legacy SKILL.md")
        checklist_text = _read_regular_text(checklist, "legacy bootstrap checklist")
        validator_text = _read_regular_text(validator, "legacy validator")
    except InstallError:
        raise
    return all(
        marker in skill_text
        for marker in (
            "name: codebase-documentation-architect",
            "# Codebase Documentation Architect",
            "references/bootstrap-checklist.md",
            "completion maintenance",
        )
    ) and all(
        marker in checklist_text
        for marker in (
            "# Bootstrap Checklist",
            "completion maintenance",
        )
    ) and "Validate the documentation model produced by codebase-documentation-architect" in validator_text


def _skill_declares_name(path: Path, expected_name: str) -> bool:
    """Return whether a legacy SKILL.md identifies itself as the reserved product skill.

    The pre-kit V1 skill and some manually copied kit installations predate the
    ownership manifest. For the two exact product skill basenames, the SKILL.md
    frontmatter name is the migration identity. This deliberately allows local
    edits to an old installation without leaving a duplicate active copy in
    `.agents/skills`.
    """
    skill_file = path / "SKILL.md"
    if not path_exists(skill_file):
        return False
    text = _read_regular_text(skill_file, "legacy SKILL.md")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if stripped.startswith("name:"):
            value = stripped.split(":", 1)[1].strip().strip('\"\'')
            return value == expected_name
    return False


def _legacy_codex_skill_is_removable(path: Path, skill: str) -> bool:
    """Return True for a legacy copy of one of this product's reserved skills.

    A successful Codex install must never leave the V1 architect or an older
    kit architect/maintainer active in `.agents/skills`. Manifest-owned trees
    are verified before removal. Pre-manifest/manual copies are removable when
    their SKILL.md frontmatter declares the exact reserved skill name. A path
    collision that does not identify itself as that skill still blocks preflight.
    """
    if path_is_unsafe_link(path) or not path.is_dir():
        fail(f"Legacy Codex skill collision at {path}: expected a real directory")

    owner_path = path / OWNER_FILE
    if path_exists(owner_path):
        try:
            assert_owned_tree(path, "skill", skill)
        except InstallError as exc:
            fail(
                f"Legacy Codex skill at {path} appears toolkit-owned but cannot be removed safely: {exc}"
            )
        return True

    if _skill_declares_name(path, skill):
        return True

    # Preserve recognition of the exact V1 package even if an unusual legacy
    # copy lost normal frontmatter formatting.
    if skill == "codebase-documentation-architect" and _is_known_v1_architect_tree(path):
        return True

    fail(
        f"Legacy Codex skill collision at {path}: this reserved product path does not "
        f"contain a SKILL.md declaring name: {skill}. Move or remove it explicitly, "
        "then rerun the installer."
    )


def legacy_codex_skill_removals(scope: str, repo: Path | None, scope_root: Path) -> list[Path]:
    """Plan mandatory migration of this toolkit from `.agents` to `.codex`.

    When Codex is selected, a successful install/uninstall removes recognized
    legacy toolkit skills from `.agents/skills`. Unrelated `.agents` content is
    preserved. Ambiguous same-name collisions fail preflight so duplicate
    toolkit skills are never left active silently.
    """
    base = Path.home() if scope == "user" else repo
    assert base is not None
    agents_root = base / ".agents"
    skills_root = agents_root / "skills"
    if not path_exists(skills_root):
        return []
    if path_is_unsafe_link(skills_root) or not skills_root.is_dir():
        fail(f"Cannot safely migrate legacy Codex skills: {skills_root} is not a real directory")

    removable: list[Path] = []
    for skill in SKILLS:
        candidate = skills_root / skill
        if not path_exists(candidate):
            continue
        if _legacy_codex_skill_is_removable(candidate, skill):
            removable.append(candidate)

    if not removable:
        return []

    try:
        skill_children = list(skills_root.iterdir())
        agent_children = list(agents_root.iterdir())
    except OSError as exc:
        fail(f"Cannot inspect legacy Codex skill root {agents_root}: {exc}")

    removable_names = {path.name for path in removable}
    # Prune `.agents` only when it contains nothing except the toolkit skills
    # being removed. Otherwise remove only those skill directories.
    if (
        len(agent_children) == 1
        and agent_children[0] == skills_root
        and all(child.name in removable_names for child in skill_children)
        and len(skill_children) == len(removable)
    ):
        assert_safe_destination(agents_root, scope_root)
        return [agents_root]
    return removable


class Transaction:
    """Stage all output away from destinations and restore every prior path on error."""

    def __init__(self, scope_root: Path) -> None:
        self.scope_root = scope_root
        self.workspace = Path(tempfile.mkdtemp(prefix=".codebase-documentation-kit-install-", dir=scope_root))
        self.backups = self.workspace / "backups"
        self.backups.mkdir()
        self.changes: list[Change] = []
        self.created_directories: list[Path] = []
        self.recovery_needed = False

    def stage_tree(self, source: Path, destination: Path, manifest: dict[str, Any], description: str) -> None:
        staged = self.workspace / f"tree-{len(self.changes)}"
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        (staged / OWNER_FILE).write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        self.changes.append(Change(destination, staged, description))

    def stage_json(self, destination: Path, obj: dict[str, Any], description: str) -> None:
        staged = self.workspace / f"config-{len(self.changes)}.json"
        staged.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.changes.append(Change(destination, staged, description))

    def stage_text(self, destination: Path, text: str, description: str) -> None:
        staged = self.workspace / f"config-{len(self.changes)}.txt"
        staged.write_text(text, encoding="utf-8")
        self.changes.append(Change(destination, staged, description))

    def stage_removal(self, destination: Path, description: str) -> None:
        self.changes.append(Change(destination, None, description))

    def ensure_parent(self, parent: Path) -> None:
        assert_safe_destination(parent, self.scope_root)
        missing: list[Path] = []
        current = parent
        while not path_exists(current):
            missing.append(current)
            current = current.parent
        if path_is_unsafe_link(current) or not current.is_dir():
            fail(f"Cannot safely create {parent}: parent {current} is not a real directory")
        for directory in reversed(missing):
            assert_safe_destination(directory.parent, self.scope_root)
            directory.mkdir()
            self.created_directories.append(directory)
            assert_safe_destination(directory, self.scope_root)

    def commit(self) -> None:
        try:
            for index, change in enumerate(self.changes):
                self.ensure_parent(change.destination.parent)
                # Re-resolve after creating parents and immediately before the
                # first mutation to close the preflight-to-commit escape window.
                assert_safe_destination(change.destination, self.scope_root)
                if path_exists(change.destination):
                    backup = self.backups / str(index)
                    os.replace(change.destination, backup)
                    change.backup = backup
                if change.staged is not None:
                    os.replace(change.staged, change.destination)
                change.applied = True
        except BaseException as exc:
            self.rollback()
            if not isinstance(exc, Exception):
                raise
            if isinstance(exc, InstallError):
                if self.recovery_needed:
                    fail(f"Installation failed; recovery backups remain at {self.workspace}")
                raise
            if self.recovery_needed:
                fail(f"Installation failed; recovery backups remain at {self.workspace}: {exc}")
            if isinstance(exc, OSError):
                fail(f"Installation failed and prior state was restored: {exc}")
            raise
        for change in self.changes:
            if change.backup is not None:
                try:
                    remove_path(change.backup)
                except OSError:
                    # The completed destination is authoritative; retain a private
                    # backup if cleanup cannot finish immediately.
                    pass

    def rollback(self) -> None:
        for change in reversed(self.changes):
            if not change.applied and change.backup is None:
                continue
            try:
                remove_path(change.destination)
                if change.backup is not None and path_exists(change.backup):
                    self.ensure_parent(change.destination.parent)
                    os.replace(change.backup, change.destination)
            except BaseException:
                # Keep the private backup workspace for a subsequent recovery attempt.
                self.recovery_needed = True
                continue
        for directory in reversed(self.created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass

    def close(self) -> None:
        if self.recovery_needed:
            return
        if path_exists(self.workspace):
            shutil.rmtree(self.workspace, ignore_errors=True)


def action_for_install(path: Path) -> str:
    return f"install {path}"


def plan(
    scope: str,
    repo: Path | None,
    targets: list[str],
    uninstall: bool,
    dry_run: bool,
) -> tuple[list[str], Transaction | None]:
    integrations, runtime, scope_root = integrations_for(scope, repo)
    selected = [integrations[target] for target in targets]
    originals: dict[str, dict[str, Any]] = {}
    desired: dict[str, dict[str, Any]] = {}
    # Always inspect both configs for an uninstall. An unreadable unselected
    # config must not make us delete a runtime that it may still reference.
    inspected = integrations.values() if uninstall else selected
    for integration in inspected:
        preflight_config_destination(integration.config_path, scope_root)
        original = load_json(integration.config_path)
        get_hooks(copy.deepcopy(original), integration.config_path)
        originals[integration.provider] = original
    for integration in selected:
        desired[integration.provider] = prepare_config(originals[integration.provider], integration, uninstall)

    post_configs = dict(originals)
    post_configs.update(desired)
    runtime_needed = any(
        config_has_our_hooks(post_configs[integration.provider], integration)
        for integration in integrations.values()
        if integration.provider in post_configs
    )
    runtime_referenced = any(config_references_runtime(config, runtime) for config in post_configs.values())

    runtime_source = ROOT / "runtime"
    runtime_manifest = make_manifest("runtime", "runtime", runtime_source)
    runtime_will_change = not uninstall and runtime_needed
    runtime_remove_path: Path | None = None
    if runtime_will_change:
        preflight_tree_destination(runtime, "runtime", "runtime", destructive=True, scope_root=scope_root)
    elif uninstall and not runtime_needed and not runtime_referenced and path_exists(runtime):
        # A foreign or modified runtime is retained rather than removed.
        try:
            assert_owned_tree(runtime, "runtime", "runtime")
            runtime_will_change = True
            runtime_remove_path = runtime_removal_target(runtime)
        except InstallError:
            runtime_will_change = False

    skill_changes: list[tuple[Path, Path, dict[str, Any], bool]] = []
    for integration in selected:
        for skill in SKILLS:
            source = ROOT / "skills" / skill
            destination = integration.skills_root / skill
            manifest = make_manifest("skill", skill, source)
            exists = path_exists(destination)
            if not uninstall or exists:
                preflight_tree_destination(destination, "skill", skill, destructive=exists, scope_root=scope_root)
            if uninstall:
                if exists:
                    skill_changes.append((source, destination, manifest, True))
            else:
                skill_changes.append((source, destination, manifest, False))

    legacy_skill_removals: list[Path] = []
    if "codex" in targets:
        legacy_skill_removals = legacy_codex_skill_removals(scope, repo, scope_root)
        for legacy in legacy_skill_removals:
            preflight_parent(legacy.parent, scope_root)
            assert_safe_destination(legacy, scope_root)

    actions: list[str] = []
    for legacy in legacy_skill_removals:
        actions.append(f"remove legacy toolkit path {legacy}")
    if not uninstall and runtime_will_change:
        actions.append(f"install shared runtime {runtime}")
    if uninstall and runtime_will_change:
        assert runtime_remove_path is not None
        actions.append(f"remove {runtime_remove_path}")
    for _, destination, _, remove in skill_changes:
        actions.append(("remove " if remove else "install ") + str(destination))
    for integration in selected:
        if desired[integration.provider] != originals[integration.provider]:
            actions.append(f"configure {integration.config_path}")

    if dry_run:
        return actions, None

    transaction = Transaction(scope_root)
    try:
        for legacy in legacy_skill_removals:
            transaction.stage_removal(legacy, f"remove legacy toolkit path {legacy}")
        if not uninstall and runtime_will_change:
            transaction.stage_tree(runtime_source, runtime, runtime_manifest, action_for_install(runtime))
        elif uninstall and runtime_will_change:
            assert runtime_remove_path is not None
            transaction.stage_removal(runtime_remove_path, f"remove {runtime_remove_path}")
        for source, destination, manifest, remove in skill_changes:
            if remove:
                transaction.stage_removal(destination, f"remove {destination}")
            else:
                transaction.stage_tree(source, destination, manifest, action_for_install(destination))
        for integration in selected:
            if desired[integration.provider] != originals[integration.provider]:
                transaction.stage_json(integration.config_path, desired[integration.provider], f"configure {integration.config_path}")
    except BaseException:
        transaction.close()
        raise
    return actions, transaction


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install Codebase Documentation Kit v2")
    parser.add_argument("--target", choices=["codex", "claude", "both"], default="both")
    parser.add_argument("--scope", choices=["user", "project"], default="user")
    parser.add_argument("--repo", help="Repository root for --scope project")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)

    repo: Path | None = None
    if args.scope == "project":
        if not args.repo:
            parser.error("--repo is required for --scope project")
        repo = Path(args.repo).expanduser().resolve()
        if not repo.is_dir():
            parser.error(f"repository does not exist: {repo}")

    targets = ["codex", "claude"] if args.target == "both" else [args.target]
    transaction: Transaction | None = None
    try:
        actions, transaction = plan(args.scope, repo, targets, args.uninstall, args.dry_run)
        if transaction is not None:
            try:
                transaction.commit()
            finally:
                transaction.close()
    except (InstallError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(("DRY RUN\n" if args.dry_run else "") + "\n".join(f"- {action}" for action in actions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
