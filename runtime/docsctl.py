#!/usr/bin/env python3
"""Deterministic support CLI for Codebase Documentation Kit v2.

The CLI intentionally uses only the Python standard library. It keeps repository
writes small, emits compact machine-readable summaries, and stores session state
outside repositories.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable
from urllib.parse import unquote

VERSION = "2.3.0"
MODEL_FILE = ".docsctl.json"
MODEL_SCHEMA = 2
TOOLKIT_NAME = "codebase-documentation-kit"
LEGACY_SKILL = "codebase-documentation-architect"

IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "dist", "build",
    "coverage", ".tox", "__pycache__", ".next", ".cache", "target", "vendor",
}
DOC_ROOT_FILES = {"README.md", "AGENTS.md", "CLAUDE.md", "MEMORY.md"}
GENERIC_PROCEDURE_FILES = {
    "documentation-maintenance.md", "memory-workflow.md", "document-editing-rules.md"
}
FORBIDDEN_AGENT_PHRASES = {
    "procedure-load", "source-precedence", "source precedence", "memory rubric",
    "retention rubric", "document editing rules", "document-editing rules",
    "documentation update triggers", "memory update triggers", "detailed exit criteria",
}
CODE_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt",
    ".kts", ".go", ".rs", ".rb", ".php", ".cs", ".fs", ".fsx", ".swift", ".c",
    ".h", ".cc", ".cpp", ".hpp", ".scala", ".sh", ".bash", ".zsh", ".ps1", ".sql",
    ".proto", ".graphql", ".gql", ".vue", ".svelte", ".dart", ".ex", ".exs",
}
CONFIG_NAMES = {
    "dockerfile", "compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml",
    "fly.toml", "vercel.json", "netlify.toml", "railway.json", "render.yaml", "render.yml",
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "poetry.lock",
    "uv.lock", "pipfile", "pipfile.lock", "cargo.toml", "cargo.lock", "go.mod", "go.sum",
    "pom.xml", "build.gradle", "build.gradle.kts", "gradle.properties", "gemfile", "gemfile.lock",
    "composer.json", "composer.lock", "mix.exs", "pubspec.yaml", "makefile", "justfile",
    "terraform.tf", "serverless.yml", "serverless.yaml", "wrangler.toml",
}
TEST_PARTS = {"test", "tests", "spec", "specs", "__tests__"}
DOC_EXTENSIONS = {".md", ".mdx", ".rst", ".adoc"}
GENERATED_DIRS = {"dist", "build", "coverage", ".next", "out", "target", ".cache"}
GENERATED_FILES = {"package-lock.json", "pnpm-lock.yaml", "bun.lock", "bun.lockb", "go.sum"}
DATE_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

V1_CANONICAL_LINES = {
    "- Start with [docs/README.md](docs/README.md) for the project documentation map.":
        "- Use [docs/README.md](docs/README.md) when project documentation is needed.",
    "- Check [MEMORY.md](MEMORY.md) for current priorities, recent deltas, and active risks.":
        "- Use [MEMORY.md](MEMORY.md) for current cross-session project context when the task needs it.",
    "- Check [docs/state/README.md](docs/state/README.md) for durable project context, decisions, assumptions, and known issues.":
        None,
}
V1_LEGACY_INVOCATION_LINE = (
    "- At the end of any task that changes behavior, documentation, structure, or durable project knowledge, "
    "invoke `$codebase-documentation-architect` to decide whether memory or docs need updates."
)


def run_git(root: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    command = ["git", "-C", str(root), *args]
    try:
        return subprocess.run(
            command, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=check,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def resolve_repo(path: str | Path = ".") -> Path:
    p = Path(path).expanduser().resolve()
    probe = run_git(p if p.is_dir() else p.parent, "rev-parse", "--show-toplevel")
    if probe.returncode == 0 and probe.stdout.strip():
        return Path(probe.stdout.strip()).resolve()
    if p.is_dir():
        return p
    raise ValueError(f"Repository path is not a directory: {p}")


def cache_root() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"])
    elif os.environ.get("XDG_CACHE_HOME"):
        base = Path(os.environ["XDG_CACHE_HOME"])
    else:
        base = Path.home() / ".cache"
    result = base / TOOLKIT_NAME
    result.mkdir(parents=True, exist_ok=True)
    return result


def repo_key(root: Path) -> str:
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]


def session_dir(root: Path) -> Path:
    d = cache_root() / "sessions" / repo_key(root)
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_path(root: Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id)[:120] or "unknown"
    return session_dir(root) / f"{safe}.json"


def file_hash(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, PermissionError):
        return None


def porcelain(root: Path) -> dict[str, str]:
    try:
        cp = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            capture_output=True, text=False,
        )
    except OSError:
        return {}
    if cp.returncode != 0:
        return {}
    items = cp.stdout.split(b"\0")
    result: dict[str, str] = {}
    i = 0
    while i < len(items):
        raw = items[i]
        if not raw:
            i += 1
            continue
        text = raw.decode("utf-8", errors="replace")
        status = text[:2]
        path = text[3:]
        if status[0] in {"R", "C"} and i + 1 < len(items) and items[i + 1]:
            old_path = items[i + 1].decode("utf-8", errors="replace")
            result[path] = status
            result[old_path] = "D "
            i += 2
            continue
        result[path] = status
        i += 1
    return result


def head_ref(root: Path) -> str | None:
    cp = run_git(root, "rev-parse", "HEAD")
    return cp.stdout.strip() if cp.returncode == 0 else None


def git_available(root: Path) -> bool:
    cp = run_git(root, "rev-parse", "--is-inside-work-tree")
    return cp.returncode == 0 and cp.stdout.strip().lower() == "true"


def dirty_hashes(root: Path, statuses: dict[str, str]) -> dict[str, str | None]:
    hashes: dict[str, str | None] = {}
    for rel in statuses:
        p = root / rel
        if p.is_file():
            hashes[rel] = file_hash(p)
        else:
            hashes[rel] = None
    return hashes


def dirty_index_entries(root: Path, statuses: dict[str, str]) -> dict[str, str | None]:
    """Capture staged object identity for dirty paths, including conflict stages."""
    try:
        cp = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--stage", "-z"],
            capture_output=True,
            text=False,
        )
    except OSError:
        return {rel: None for rel in statuses}
    if cp.returncode != 0:
        return {rel: None for rel in statuses}
    entries: dict[str, list[str]] = {}
    for raw in cp.stdout.split(b"\0"):
        if not raw or b"\t" not in raw:
            continue
        metadata, raw_path = raw.split(b"\t", 1)
        rel = raw_path.decode("utf-8", errors="replace")
        if rel in statuses:
            entries.setdefault(rel, []).append(metadata.decode("ascii", errors="replace"))
    return {rel: "\n".join(sorted(entries.get(rel, []))) or None for rel in statuses}


def snapshot(
    root: Path,
    session_id: str,
    validation_failures: list[str] | None = None,
    validation_failure_counts: dict[str, int] | None = None,
    validation_warnings: list[str] | None = None,
    validation_warning_counts: dict[str, int] | None = None,
) -> dict:
    available = git_available(root)
    statuses = porcelain(root) if available else {}
    if validation_failures is None:
        validation = validate(root)
        validation_failures = validation["failures"]
        validation_failure_counts = validation["failure_counts"]
        if validation_warnings is None:
            validation_warnings = validation["warnings"]
            validation_warning_counts = validation["warning_counts"]
    elif validation_failure_counts is None:
        validation_failure_counts = dict(Counter(validation_failures))
    if validation_warnings is None:
        validation_warnings = []
    if validation_warning_counts is None:
        validation_warning_counts = dict(Counter(validation_warnings))
    snap = {
        "version": 1,
        # Toolkit version that produced this baseline. A Stop evaluated by a
        # different version cannot trust the stored validation deltas, because
        # the set of checks may have changed mid-session, so it re-baselines.
        "toolkit_version": VERSION,
        "session_id": session_id,
        "repo": str(root),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_available": available,
        "head": head_ref(root) if available else None,
        "status": statuses,
        "dirty_hashes": dirty_hashes(root, statuses),
        "dirty_index_entries": dirty_index_entries(root, statuses),
        # Store deterministic failures that already existed so the Stop hook
        # blocks only regressions introduced during this session.
        "validation_failures": validation_failures,
        "validation_failure_counts": validation_failure_counts,
        # Warnings are stored for the same reason: only warnings introduced by
        # this session are worth surfacing, and only where the hook already speaks.
        "validation_warnings": validation_warnings,
        "validation_warning_counts": validation_warning_counts,
    }
    p = session_path(root, session_id)
    p.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = session_dir(root) / "latest.json"
    latest.write_text(json.dumps({"session_id": session_id, "path": str(p)}, indent=2), encoding="utf-8")
    prune_sessions(root)
    return snap


def prune_sessions(root: Path, keep: int = 30) -> None:
    paths = [p for p in session_dir(root).glob("*.json") if p.name != "latest.json"]
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for p in paths[keep:]:
        try:
            p.unlink()
        except OSError:
            pass


def load_snapshot(root: Path, session_id: str | None, latest: bool = False) -> tuple[dict | None, str | None]:
    if latest or not session_id:
        meta = session_dir(root) / "latest.json"
        if not meta.exists():
            return None, None
        try:
            obj = json.loads(meta.read_text(encoding="utf-8"))
            session_id = obj.get("session_id")
        except Exception:
            return None, None
    p = session_path(root, session_id)
    if not p.exists():
        return None, session_id
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        if (
            not isinstance(obj, dict)
            or obj.get("version") != 1
            or obj.get("repo") != str(root)
            or not isinstance(obj.get("git_available"), bool)
            or not isinstance(obj.get("status"), dict)
            or not isinstance(obj.get("dirty_hashes"), dict)
            or not isinstance(obj.get("dirty_index_entries"), dict)
            or not isinstance(obj.get("validation_failures"), list)
            or any(not isinstance(failure, str) for failure in obj.get("validation_failures", []))
        ):
            return None, session_id
        counts = obj.get("validation_failure_counts")
        if counts is not None and (
            not isinstance(counts, dict)
            or any(
                not isinstance(failure, str)
                or not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
                for failure, count in counts.items()
            )
        ):
            return None, session_id
        return obj, session_id
    except Exception:
        return None, session_id


def changed_since_snapshot(root: Path, snap: dict) -> list[dict]:
    if not snap.get("git_available", False) or not git_available(root):
        return []
    before_status: dict[str, str] = snap.get("status", {})
    before_hashes: dict[str, str | None] = snap.get("dirty_hashes", {})
    before_index: dict[str, str | None] = snap.get("dirty_index_entries", {})
    after_status = porcelain(root)
    after_index = dirty_index_entries(root, after_status)
    after_head = head_ref(root)
    changed: dict[str, dict] = {}

    all_paths = set(before_status) | set(after_status)
    for rel in all_paths:
        b_status = before_status.get(rel)
        a_status = after_status.get(rel)
        if b_status != a_status:
            changed[rel] = {"path": rel, "reason": "working-tree-status", "before": b_status, "after": a_status}
            continue
        if b_status is not None and a_status is not None:
            current_hash = file_hash(root / rel) if (root / rel).is_file() else None
            if current_hash != before_hashes.get(rel):
                changed[rel] = {"path": rel, "reason": "modified-preexisting-dirty", "before": b_status, "after": a_status}
            elif after_index.get(rel) != before_index.get(rel):
                changed[rel] = {"path": rel, "reason": "modified-preexisting-index", "before": b_status, "after": a_status}

    before_head = snap.get("head")
    if before_head and after_head and before_head != after_head:
        cp = run_git(root, "diff", "--name-status", f"{before_head}..{after_head}")
        if cp.returncode == 0:
            for line in cp.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t")
                status = parts[0]
                rels = parts[1:]
                for rel in rels:
                    changed.setdefault(rel, {"path": rel, "reason": "committed-during-session", "after": status})

    return sorted(changed.values(), key=lambda x: x["path"].lower())


def classify_path(rel: str) -> str:
    p = Path(rel.replace("\\", "/"))
    lower = rel.lower().replace("\\", "/")
    name = p.name.lower()
    parts = {part for part in lower.split("/") if part}
    suffix = p.suffix.lower()
    if lower == MODEL_FILE.lower():
        return "model"
    if parts & GENERATED_DIRS or name in GENERATED_FILES or name.endswith((".min.js", ".min.css")) or suffix in {".lock", ".map"}:
        return "generated"
    if lower.startswith("docs/") or name in {x.lower() for x in DOC_ROOT_FILES} or suffix in DOC_EXTENSIONS:
        return "docs"
    test_path_part = any(part in TEST_PARTS or part.endswith((".test", ".tests", "_test", "_tests")) for part in parts)
    stem = p.stem.lower()
    if test_path_part or stem.startswith("test_") or stem.endswith(("_test", "_spec")) or ".test." in name or ".spec." in name:
        return "tests"
    if lower.startswith(".github/workflows/") or "migration" in parts or "migrations" in parts:
        return "config"
    if name in CONFIG_NAMES or suffix in {".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".conf", ".tf"}:
        return "config"
    if suffix in CODE_EXTENSIONS:
        return "source"
    return "other"


GENERIC_DOC_STEMS = {"readme", "index", "changelog", "contributing", "license", "memory"}
REVERSE_INDEX_CAP = 5


def mentioning_docs(root: Path, changed: list[dict]) -> list[str]:
    """Documents that mention a changed document by path.

    The other half of `candidate_docs`: when documentation itself changes, what
    needs review is whatever asserts something *about* it -- an index describing
    it, a memory pointing at it. One batched `git grep` for every path at once,
    because spawning a process per changed file blows the Stop budget on a large
    session.

    Renames need no special handling here: `porcelain` already records the old
    path as its own deleted entry, and the old path is the valuable needle --
    whoever cites the new location is already correct, while the stale reference
    names where the document used to be.
    """
    needles: list[str] = []
    for item in changed:
        rel = str(item.get("path") or "").replace("\\", "/")
        if not rel or classify_path(rel) != "docs":
            continue
        # A bare generic name matches half the repository; only path-qualified.
        if Path(rel).stem.lower() in GENERIC_DOC_STEMS and "/" not in rel:
            continue
        if rel not in needles:
            needles.append(rel)
    if not needles:
        return []
    args = ["git", "-C", str(root), "grep", "-l", "-F", "-I"]
    for needle in needles[:20]:
        args += ["-e", needle]
    args.append("--")
    try:
        # Two seconds, not ten: the whole Stop hook has a ten-second budget, and this
        # runs before validate. Degrading to [] beats losing the entire message.
        out = subprocess.run(args, text=True, capture_output=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode not in {0, 1}:
        return []
    changed_paths = {str(item.get("path") or "").replace("\\", "/") for item in changed}
    hits: list[str] = []
    for line in out.stdout.splitlines():
        rel = line.strip().replace("\\", "/")
        if not rel or rel in changed_paths or classify_path(rel) != "docs":
            continue
        hits.append(rel)
        if len(hits) >= REVERSE_INDEX_CAP:
            break
    return sorted(hits)


def candidate_docs(root: Path, changed: list[dict]) -> list[str]:
    candidates: set[str] = set()
    for item in changed:
        rel = item["path"].replace("\\", "/")
        p = Path(rel)
        stem = p.stem.lower()
        category = classify_path(rel)
        if category in {"source", "config"}:
            for pattern in [f"docs/**/{stem}.md", f"docs/**/{stem}.mdx"]:
                for match in root.glob(pattern):
                    if match.is_file():
                        candidates.add(match.relative_to(root).as_posix())
            if "api" in {x.lower() for x in p.parts}:
                for q in [root / "docs" / "api.md", root / "docs" / "api" / "README.md"]:
                    if q.exists():
                        candidates.add(q.relative_to(root).as_posix())
            if category == "config":
                for q in [root / "docs" / "deployment.md", root / "docs" / "configuration.md", root / "README.md"]:
                    if q.exists():
                        candidates.add(q.relative_to(root).as_posix())
    return sorted(candidates)[:12]


def impact_report(root: Path, snap: dict) -> dict:
    if not snap.get("git_available", False) or not git_available(root):
        return {
            "repo": str(root),
            "git_available": False,
            "impact_indeterminate": True,
            "reason": "git-unavailable",
            "changed_count": 0,
            "categories": {},
            "needs_documentation_review": False,
            "changed": [],
            "changed_truncated": False,
            "candidate_docs": [],
        }
    changed = changed_since_snapshot(root, snap)
    categories: dict[str, int] = {}
    for item in changed:
        cat = classify_path(item["path"])
        item["category"] = cat
        categories[cat] = categories.get(cat, 0) + 1

    substantive = any(item["category"] in {"source", "config"} for item in changed)
    unknown_change = any(item["category"] == "other" for item in changed)
    def is_doc_structure_change(item: dict) -> bool:
        if item["category"] != "docs":
            return False
        status = str(item.get("after") or "")
        if item.get("reason") == "committed-during-session":
            return status[:1] in {"A", "D", "R", "C"}
        # Porcelain status is absent at the baseline for a clean tracked file, so
        # before=None must not be mistaken for "file did not exist". Structural
        # changes are additions, deletions, renames/copies, or untracked docs.
        return "??" in status or any(flag in status for flag in ("A", "D", "R", "C"))

    docs_structure = any(is_doc_structure_change(item) for item in changed)
    # In a repository whose product IS the documentation, editing an existing
    # tracked doc is the main activity, not a side effect of changing code. The
    # default model treats it as ordinary and stays silent, which leaves that
    # work with no review at all. Opt in per repository; never on by default,
    # because in a code repository it would speak on every prose touch-up.
    docs_are_product = bool((read_model(root) or {}).get("docs_are_product"))
    docs_edited = docs_are_product and any(item["category"] == "docs" for item in changed)
    needs_review = substantive or unknown_change or docs_structure or docs_edited
    only_tests_or_generated = bool(changed) and all(item["category"] in {"tests", "generated"} for item in changed)
    if only_tests_or_generated:
        needs_review = False

    return {
        "repo": str(root),
        "git_available": True,
        "impact_indeterminate": False,
        "changed_count": len(changed),
        "categories": categories,
        "needs_documentation_review": needs_review,
        "changed": changed[:40],
        "changed_truncated": len(changed) > 40,
        "candidate_docs": candidate_docs(root, changed),
        "mentioning_docs": mentioning_docs(root, changed) if needs_review else [],
    }


def model_diagnostics(root: Path) -> tuple[dict | None, list[str]]:
    p = root / MODEL_FILE
    try:
        status = p.lstat()
    except FileNotFoundError:
        return None, [f"Missing {MODEL_FILE}."]
    except OSError:
        return None, [f"{MODEL_FILE} must be a readable regular file."]
    file_attributes = getattr(status, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(status.st_mode) or file_attributes & reparse_flag or not stat.S_ISREG(status.st_mode):
        return None, [f"{MODEL_FILE} must be a readable regular file."]
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, [f"Malformed {MODEL_FILE}: invalid JSON."]
    except OSError:
        return None, [f"{MODEL_FILE} must be a readable regular file."]
    if not isinstance(obj, dict):
        return None, [f"Malformed {MODEL_FILE}: top-level JSON value must be an object."]

    failures: list[str] = []
    schema = obj.get("schema_version")
    if not isinstance(schema, int) or isinstance(schema, bool):
        failures.append(f"Malformed {MODEL_FILE}: schema_version must be an integer.")
    toolkit = obj.get("toolkit")
    if not isinstance(toolkit, str):
        failures.append(f"Malformed {MODEL_FILE}: toolkit must be a string.")
    agents = obj.get("agents")
    if (
        not isinstance(agents, list)
        or not agents
        or any(not isinstance(agent, str) or agent not in {"codex", "claude"} for agent in agents)
        or len(set(agents)) != len(agents)
    ):
        failures.append(
            f"Malformed {MODEL_FILE}: agents must be a non-empty list containing unique 'codex' and/or 'claude' values."
        )
    budgets = obj.get("budgets")
    if not isinstance(budgets, dict):
        failures.append(f"Malformed {MODEL_FILE}: budgets must be an object.")
    else:
        for key in ("memory_max_bytes", "docs_index_max_bytes"):
            value = budgets.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                failures.append(f"Malformed {MODEL_FILE}: budgets.{key} must be a non-negative integer.")
    return obj, failures


def read_model(root: Path) -> dict | None:
    model, _ = model_diagnostics(root)
    return model


def is_v2_model(model: dict | None, diagnostics: list[str]) -> bool:
    return bool(
        model
        and not diagnostics
        and model.get("schema_version") == MODEL_SCHEMA
        and model.get("toolkit") == TOOLKIT_NAME
    )


def legacy_signals(root: Path) -> list[str]:
    signals: list[str] = []
    for name in ["AGENTS.md", "CLAUDE.md"]:
        p = root / name
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                if LEGACY_SKILL in text:
                    signals.append(f"{name}:legacy-skill-reference")
            except OSError:
                pass
    if (root / "docs" / "operations" / "documentation-system").exists():
        signals.append("legacy-documentation-system-subtree")
    for p in [root / "docs" / n for n in GENERIC_PROCEDURE_FILES]:
        if p.exists():
            signals.append(f"generic-procedure:{p.relative_to(root).as_posix()}")
    core = [root / "MEMORY.md", root / "docs" / "README.md", root / "docs" / "state" / "README.md"]
    if all(p.exists() for p in core):
        signals.append("v1-core-shape")
    return signals


def detect_status(root: Path) -> dict:
    model, diagnostics = model_diagnostics(root)
    signals = legacy_signals(root)
    if is_v2_model(model, diagnostics):
        state = "v2"
    elif any("legacy-skill-reference" in s for s in signals) or "legacy-documentation-system-subtree" in signals:
        state = "v1-legacy"
    elif "v1-core-shape" in signals:
        state = "v1-probable"
    else:
        state = "untreated"
    return {
        "state": state,
        "model": model,
        "model_diagnostics": diagnostics,
        "legacy_signals": signals,
        "files": {
            "AGENTS.md": (root / "AGENTS.md").exists(),
            "CLAUDE.md": (root / "CLAUDE.md").exists(),
            "MEMORY.md": (root / "MEMORY.md").exists(),
            "docs/README.md": (root / "docs" / "README.md").exists(),
            "docs/state/README.md": (root / "docs" / "state" / "README.md").exists(),
        },
    }


def compact_scan(root: Path) -> dict:
    entries: list[str] = []
    try:
        for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
            if p.name in IGNORED_DIRS:
                continue
            entries.append(p.name + ("/" if p.is_dir() else ""))
    except OSError:
        pass
    manifests = []
    for name in sorted(CONFIG_NAMES):
        p = root / name
        if p.exists():
            manifests.append(name)
    deployment = []
    for rel in [".github/workflows", "Dockerfile", "docker-compose.yml", "compose.yml", "fly.toml", "vercel.json", "terraform"]:
        if (root / rel).exists():
            deployment.append(rel)
    source_dirs = []
    for name in ["src", "app", "apps", "lib", "packages", "services", "backend", "frontend", "server", "client", "cmd"]:
        if (root / name).is_dir():
            source_dirs.append(name + "/")
    test_dirs = []
    for name in ["tests", "test", "spec", "__tests__"]:
        if (root / name).is_dir():
            test_dirs.append(name + "/")
    docs = []
    if (root / "docs").exists():
        for p in sorted((root / "docs").glob("*")):
            if p.name == "_archive":
                continue
            docs.append(p.relative_to(root).as_posix() + ("/" if p.is_dir() else ""))
    return {
        "repo": str(root),
        "status": detect_status(root),
        "top_level": entries[:60],
        "source_dirs": source_dirs,
        "test_dirs": test_dirs,
        "manifests": manifests,
        "deployment": deployment,
        "docs_top_level": docs[:60],
    }


def normalize_link(raw: str) -> str | None:
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        return None
    bracketed = raw.startswith("<") and raw.endswith(">")
    if bracketed:
        raw = raw[1:-1]
    lower = raw.lower()
    if lower.startswith(("http://", "https://", "mailto:", "tel:", "javascript:")):
        return None
    if " " in raw and not bracketed:
        raw = raw.split()[0]
    path_part = raw.split("#", 1)[0]
    return unquote(path_part) if path_part else None


def mask_markdown_code(text: str, inline: bool = True) -> str:
    """Mask code while retaining offsets for a small link scanner.

    `inline=False` masks fenced blocks only. Heading text needs that: a heading
    such as ``## The `run` command`` slugs to `the-run-command`, and blanking the
    code span would compute `the-command` -- warning on the correct anchor while
    passing the wrong one, which is worse than not checking at all.
    """
    masked = list(text)
    offset = 0
    fence: tuple[str, int] | None = None
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        match = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})", body)
        if fence is not None:
            for index in range(offset, offset + len(line)):
                masked[index] = " "
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= fence[1]:
                fence = None
        elif match:
            fence = (match.group(1)[0], len(match.group(1)))
            for index in range(offset, offset + len(line)):
                masked[index] = " "
        offset += len(line)

    if not inline:
        return "".join(masked)

    index = 0
    while index < len(masked):
        if masked[index] != "`":
            index += 1
            continue
        end = index
        while end < len(masked) and masked[end] == "`":
            end += 1
        delimiter = "`" * (end - index)
        close = end
        while close < len(masked):
            if "".join(masked[close:close + len(delimiter)]) == delimiter:
                for code_index in range(index, close + len(delimiter)):
                    masked[code_index] = " "
                index = close + len(delimiter)
                break
            close += 1
        else:
            for code_index in range(index, len(masked)):
                masked[code_index] = " "
            break
    return "".join(masked)


def normalized_reference_label(label: str) -> str:
    return " ".join(label.split()).casefold()


def closing_bracket(text: str, start: int) -> int | None:
    depth = 1
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def closing_parenthesis(text: str, start: int) -> int | None:
    depth = 1
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def reference_definitions(masked: str) -> dict[str, str]:
    definitions: dict[str, str] = {}
    for line in masked.splitlines():
        match = re.match(r"^[ \t]{0,3}\[([^\]]+)\]:[ \t]*(.*)$", line)
        if not match:
            continue
        label = normalized_reference_label(match.group(1))
        remainder = match.group(2).lstrip()
        if remainder.startswith("<"):
            close = remainder.find(">", 1)
            destination = remainder[:close + 1] if close >= 0 else remainder
        else:
            destination = remainder.split(None, 1)[0] if remainder else ""
        if label and destination:
            definitions[label] = destination
    return definitions


def iter_markdown_links(text: str) -> Iterable[tuple[str, str, str]]:
    """Yield (kind, value, label) triples for active inline and reference-style links.

    The visible label travels with the destination so callers can detect a label
    that names a path the destination no longer satisfies -- the failure mode a
    plain target check cannot see, because the link still resolves.
    """
    masked = mask_markdown_code(text)
    definitions = reference_definitions(masked)
    index = 0
    while index < len(masked):
        if masked[index] != "[":
            index += 1
            continue
        label_end = closing_bracket(masked, index)
        if label_end is None:
            index += 1
            continue
        label = masked[index + 1:label_end]
        # Detection runs on the masked copy, but the *visible* label must come
        # from the original: masking blanks inline code, and a label written as
        # `docs/topic.md` in backticks -- the dominant convention in the
        # repositories this check was built for -- would otherwise arrive empty
        # and silently disable the label check.
        visible = text[index + 1:label_end]
        next_index = label_end + 1
        if next_index < len(masked) and masked[next_index] == "(":
            destination_end = closing_parenthesis(masked, next_index)
            if destination_end is not None:
                yield "link", masked[next_index + 1:destination_end], visible
                index = destination_end + 1
                continue
        if next_index < len(masked) and masked[next_index] == "[":
            reference_end = closing_bracket(masked, next_index)
            if reference_end is not None:
                reference = masked[next_index + 1:reference_end] or label
                key = normalized_reference_label(reference)
                if key in definitions:
                    yield "link", definitions[key], visible
                else:
                    yield "missing-reference", reference, visible
                index = reference_end + 1
                continue
        if next_index >= len(masked) or masked[next_index] != ":":
            key = normalized_reference_label(label)
            if key in definitions:
                yield "link", definitions[key], visible
        index = label_end + 1


HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
SETEXT_HEADING_RE = re.compile(
    r"^(?![ \t]*$)(?P<t>[^\r\n]+)\r?\n[ \t]{0,3}(?:=+|-{2,})[ \t]*$", re.MULTILINE
)
EXPLICIT_ANCHOR_RE = re.compile(r"""(?:id|name)\s*=\s*["']([^"']+)["']""")
SLUG_DROP_RE = re.compile(r"[^\w\- ]", re.UNICODE)


def github_slug(title: str) -> str:
    """Approximate GitHub's heading slugger: lowercase, drop punctuation, spaces to hyphens."""
    text = re.sub(r"<[^>]+>", "", title)
    text = re.sub(r"[*_`~]", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = SLUG_DROP_RE.sub("", text.strip().lower())
    return re.sub(r"\s+", "-", text).strip("-")


def anchor_targets(text: str) -> set[str]:
    """Fragments a document offers: heading slugs (deduped as GitHub does) plus explicit ids."""
    found: set[str] = set()
    seen: Counter[str] = Counter()
    # Fences only: an inline code span is part of the heading text and therefore
    # part of the slug.
    masked = mask_markdown_code(text, inline=False)
    titles: list[tuple[int, str]] = [
        (m.start(), m.group(1)) for m in HEADING_RE.finditer(masked)
    ]
    titles += [(m.start(), m.group(1)) for m in SETEXT_HEADING_RE.finditer(masked)]
    for _, title in sorted(titles):
        slug = github_slug(title)
        if not slug:
            continue
        count = seen[slug]
        seen[slug] += 1
        found.add(slug if count == 0 else f"{slug}-{count}")
    for match in EXPLICIT_ANCHOR_RE.finditer(masked):
        found.add(match.group(1).strip().lower())
    return found


def label_claims_path(label: str) -> str | None:
    """The path a link label appears to name, if it names one at all."""
    cleaned = label.strip().strip("`").strip()
    if not cleaned or " " in cleaned or "\n" in cleaned:
        return None
    if not cleaned.lower().endswith(".md"):
        return None
    cleaned = cleaned.replace("\\", "/")
    # Strip a leading "./" prefix only. Never lstrip("./"), which eats the dot of
    # a legitimate dotted directory such as ".scratch/README.md".
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned or None


def configured_doc_dirs(root: Path) -> list[str]:
    """Extra directories to treat as documentation, from `doc_dirs` in the model.

    Absent by default, which preserves the historical scope of `docs/` plus the
    root files. An explicit list -- never a "scan everything" switch -- keeps a
    code repository from pulling in vendored markdown, changelogs and templates,
    which would add noise and latency to a hook that runs on every session.
    """
    model = read_model(root)
    if not isinstance(model, dict):
        return []
    raw = model.get("doc_dirs")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        candidate = entry.strip().replace("\\", "/")
        # Reject before normalising: stripping separators first would turn an
        # absolute "/etc" into a relative "etc" and quietly accept it.
        if not candidate or candidate.startswith("/") or Path(candidate).is_absolute():
            continue
        # ":" catches Windows drive-relative forms such as "c:" or "C:x", which
        # are not absolute yet still escape the join.
        if ":" in candidate:
            continue
        cleaned = candidate.strip("/").strip()
        # "." would be the scan-everything switch this function exists to avoid.
        if not cleaned or cleaned == "." or cleaned == ".." or cleaned.startswith("../") or "/../" in cleaned:
            continue
        if cleaned not in out:
            out.append(cleaned)
    return out


def iter_doc_markdown(root: Path) -> Iterable[Path]:
    seen: set[Path] = set()
    for name in DOC_ROOT_FILES:
        p = root / name
        if p.is_file():
            seen.add(p)
            yield p
    for rel_dir in ["docs", *configured_doc_dirs(root)]:
        base = root / rel_dir
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.md")):
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if "_archive" in rel.parts or any(part in IGNORED_DIRS for part in rel.parts):
                continue
            if p in seen:
                continue
            seen.add(p)
            yield p


def extract_recent_delta_rows(text: str) -> list[list[str]]:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().lower() == "## recent deltas":
            start = i + 1
            break
    if start is None:
        return []
    rows: list[list[str]] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in s.strip("|").split("|")]
        if cells and all(cell and set(cell) <= set("-:") for cell in cells):
            continue
        if cells and cells[0].lower() == "date time":
            continue
        if len(cells) >= 4:
            rows.append(cells)
    return rows


def validate(root: Path) -> dict:
    failures: list[str] = []
    warnings: list[str] = []
    text_cache: dict[Path, tuple[bool, str | None]] = {}
    anchor_cache: dict[Path, set[str]] = {}

    def anchors_of(path: Path, content: str) -> set[str]:
        if path not in anchor_cache:
            anchor_cache[path] = anchor_targets(content)
        return anchor_cache[path]

    def inspect_text_file(path: Path) -> tuple[bool, str | None]:
        if path in text_cache:
            return text_cache[path]
        rel = path.relative_to(root).as_posix()
        try:
            status = path.lstat()
        except FileNotFoundError:
            result = (False, None)
        except OSError:
            failures.append(f"{rel} must be a readable regular file.")
            result = (True, None)
        else:
            file_attributes = getattr(status, "st_file_attributes", 0)
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if stat.S_ISLNK(status.st_mode) or file_attributes & reparse_flag or not stat.S_ISREG(status.st_mode):
                failures.append(f"{rel} must be a readable regular file.")
                result = (True, None)
            else:
                try:
                    result = (True, path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    failures.append(f"{rel} must be a readable regular file.")
                    result = (True, None)
        text_cache[path] = result
        return result

    model, model_failures = model_diagnostics(root)
    failures.extend(model_failures)
    if model and isinstance(model.get("schema_version"), int) and model.get("schema_version") != MODEL_SCHEMA:
        failures.append(f"{MODEL_FILE} schema_version must be {MODEL_SCHEMA}.")
    if model and isinstance(model.get("toolkit"), str) and model.get("toolkit") != TOOLKIT_NAME:
        failures.append(f"{MODEL_FILE} toolkit must be {TOOLKIT_NAME!r}.")

    agents_mode = model.get("agents") if model and isinstance(model.get("agents"), list) else []
    agents = root / "AGENTS.md"
    claude = root / "CLAUDE.md"
    agents_present, agents_text = inspect_text_file(agents)
    claude_present, claude_text = inspect_text_file(claude)
    if "codex" in agents_mode and not agents_present:
        failures.append("AGENTS.md is required when Codex support is enabled.")
    if "claude" in agents_mode and not claude_present:
        failures.append("CLAUDE.md is required when Claude Code support is enabled.")
    if agents_text is not None and claude_text is not None and "claude" in agents_mode:
        if "@AGENTS.md" not in claude_text:
            warnings.append("CLAUDE.md does not import @AGENTS.md; shared instructions may drift.")

    for required in [root / "MEMORY.md", root / "docs" / "README.md"]:
        present, _ = inspect_text_file(required)
        if not present:
            failures.append(f"Missing {required.relative_to(root).as_posix()}.")

    state_dir = root / "docs" / "state"
    if state_dir.exists():
        try:
            child_pages = [p for p in state_dir.glob("*.md") if p.name.lower() != "readme.md"]
        except OSError:
            child_pages = []
            failures.append("docs/state must be a readable directory.")
        if child_pages and not (state_dir / "README.md").exists():
            failures.append("docs/state contains durable pages but docs/state/README.md is missing.")

    for name, text in [("AGENTS.md", agents_text), ("CLAUDE.md", claude_text)]:
        if text is None:
            continue
        lower = text.lower()
        if LEGACY_SKILL in lower:
            failures.append(f"{name} still references legacy ${LEGACY_SKILL} maintenance.")
        for phrase in sorted(FORBIDDEN_AGENT_PHRASES):
            if phrase in lower:
                warnings.append(f"{name} may embed documentation procedure detail: {phrase}.")

    doc_system = root / "docs" / "operations" / "documentation-system"
    if doc_system.exists():
        failures.append("Remove legacy docs/operations/documentation-system/ after project facts are extracted.")
    for p in iter_doc_markdown(root):
        if p.name.lower() in GENERIC_PROCEDURE_FILES:
            failures.append(f"Legacy generic procedure remains: {p.relative_to(root).as_posix()}.")
        _, text = inspect_text_file(p)
        if text is None:
            continue
        for kind, raw_link, label in iter_markdown_links(text):
            rel_source = p.relative_to(root).as_posix()
            if kind == "missing-reference":
                failures.append(f"{rel_source} has missing reference definition: {raw_link}")
                continue
            # A same-file anchor is discarded by normalize_link, so validate it
            # here: an intra-document link that rots is the very failure class
            # these checks exist for.
            if raw_link.strip().startswith("#"):
                own = unquote(raw_link.strip()[1:]).strip()
                if own:
                    available = anchors_of(p, text)
                    if available and own.lower() not in available:
                        warnings.append(
                            f"{rel_source} links to a section that does not exist in itself: #{own}"
                        )
                continue
            link = normalize_link(raw_link)
            if link is None:
                continue
            try:
                target = (p.parent / link).resolve()
            except (OSError, ValueError):
                # A NUL byte, via %00 through unquote, raises ValueError here.
                # Left uncaught it would abort validate() for the whole repo.
                failures.append(f"{rel_source} has an unusable link target: {raw_link}")
                continue
            try:
                target.relative_to(root.resolve())
            except ValueError:
                failures.append(f"{rel_source} links outside repo: {raw_link}")
                continue
            if not target.exists():
                failures.append(f"{rel_source} has missing link target: {raw_link}")
                continue

            rel_target = target.relative_to(root.resolve()).as_posix()

            # A label that spells out a path stops being true when the target
            # moves, and the link still resolves, so nothing else catches it.
            # Abbreviating is legitimate ("ops/guide.md" for "../a/ops/guide.md");
            # naming a path the target does not end with is not.
            claimed = label_claims_path(label)
            if claimed is not None:
                # A label may spell the path either way and both are honest: as a
                # path relative to this file ("../product/roadmap.md"), or as a
                # repo-relative path, possibly abbreviated to a trailing portion.
                try:
                    claimed_from_here = (p.parent / claimed).resolve() == target
                except (OSError, ValueError):
                    claimed_from_here = False
                if not (
                    claimed_from_here
                    or rel_target == claimed
                    or rel_target.endswith("/" + claimed)
                ):
                    failures.append(
                        f"{rel_source} has a link label naming a path the target does not satisfy: "
                        f"{label!r} -> {rel_target}"
                    )

            # Anchors are the ordinary way to cite a section, and a stale one
            # fails silently: the file resolves and the reader lands at the top.
            fragment = unquote(raw_link.split("#", 1)[1]).strip() if "#" in raw_link else ""
            if fragment and target.is_file() and target.suffix.lower() == ".md":
                _, target_text = inspect_text_file(target)
                if target_text is not None:
                    available = anchors_of(target, target_text)
                    if available and fragment.lower() not in available:
                        warnings.append(
                            f"{rel_source} links to a section that does not exist in {rel_target}: #{fragment}"
                        )

    memory = root / "MEMORY.md"
    _, memory_text = inspect_text_file(memory)
    if memory_text is not None:
        rows = extract_recent_delta_rows(memory_text)
        if rows:
            if len(rows) > 5:
                failures.append(f"MEMORY.md Recent Deltas has {len(rows)} rows; keep at most 5.")
            for row in rows:
                if DATE_TIME_RE.match(row[0]) is None:
                    failures.append(f"Recent Deltas row has non-canonical Date Time: {row[0]!r}.")
        budgets = model.get("budgets") if model and isinstance(model.get("budgets"), dict) else {}
        memory_budget = budgets.get("memory_max_bytes", 12000)
        if not isinstance(memory_budget, int) or isinstance(memory_budget, bool):
            memory_budget = 12000
        try:
            if memory.stat().st_size > memory_budget:
                warnings.append("MEMORY.md exceeds the configured context budget.")
        except OSError:
            failures.append("MEMORY.md must be a readable regular file.")

    docs_readme = root / "docs" / "README.md"
    _, docs_readme_text = inspect_text_file(docs_readme)
    if docs_readme_text is not None:
        budgets = model.get("budgets") if model and isinstance(model.get("budgets"), dict) else {}
        docs_budget = budgets.get("docs_index_max_bytes", 20000)
        if not isinstance(docs_budget, int) or isinstance(docs_budget, bool):
            docs_budget = 20000
        try:
            if docs_readme.stat().st_size > docs_budget:
                warnings.append("docs/README.md exceeds the configured context budget.")
        except OSError:
            failures.append("docs/README.md must be a readable regular file.")

    failure_counts = dict(sorted(Counter(failures).items()))
    warning_counts = dict(sorted(Counter(warnings).items()))
    return {
        "ok": not failures,
        "failures": sorted(failure_counts),
        "failure_counts": failure_counts,
        "warnings": sorted(warning_counts),
        "warning_counts": warning_counts,
    }


def default_model(agents: list[str]) -> dict:
    return {
        "schema_version": MODEL_SCHEMA,
        "toolkit": TOOLKIT_NAME,
        "toolkit_version": VERSION,
        "agents": agents,
        "canonical_instructions": "AGENTS.md" if "codex" in agents else "CLAUDE.md",
        "maintenance": "hook-gated",
        "docs_index": "docs/README.md",
        "memory": "MEMORY.md",
        "durable_state_dir": "docs/state",
        "budgets": {"memory_max_bytes": 12000, "docs_index_max_bytes": 20000},
    }


def normalized_backup_path(rel: object) -> str:
    if not isinstance(rel, str) or not rel or "\0" in rel:
        raise ValueError("backup path must be a non-empty relative string")
    windows = PureWindowsPath(rel)
    posix = PurePosixPath(rel)
    if windows.is_absolute() or windows.drive or windows.root or posix.is_absolute():
        raise ValueError(f"backup path is absolute: {rel!r}")
    parts = re.split(r"[\\/]", rel)
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"backup path is not normalized: {rel!r}")
    return "/".join(parts)


def backup_target(root: Path, rel: object) -> tuple[str, Path]:
    normalized = normalized_backup_path(rel)
    target = root.joinpath(*normalized.split("/"))
    try:
        target.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"backup path escapes repository: {rel!r}") from exc
    return normalized, target


def reject_aliased_destination(path: Path, operation: str) -> None:
    """Reject writes that could mutate another filesystem name."""
    try:
        status = path.lstat()
    except FileNotFoundError:
        return
    file_attributes = getattr(status, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(status.st_mode) or file_attributes & reparse_flag:
        raise ValueError(f"{operation} destination is a symlink or reparse point: {path.name}")
    if not stat.S_ISREG(status.st_mode):
        raise ValueError(f"{operation} destination is not a regular file: {path.name}")
    link_count = getattr(status, "st_nlink", None)
    if isinstance(link_count, int) and link_count > 1:
        raise ValueError(f"{operation} destination has multiple hard links: {path.name}")


def reject_aliased_migration_destination(path: Path) -> None:
    reject_aliased_destination(path, "migration")


def reject_aliased_rollback_destination(path: Path) -> None:
    reject_aliased_destination(path, "rollback")


def migration_destination(root: Path, rel: object) -> tuple[str, Path]:
    normalized, target = backup_target(root, rel)
    reject_aliased_migration_destination(target)
    return normalized, target


def stage_sibling_bytes(path: Path, content: bytes, mode: int | None = None) -> Path:
    """Write and sync a fresh regular sibling without changing the destination."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        return temporary
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def commit_staged_file(staged: Path, destination: Path) -> None:
    os.replace(staged, destination)


def atomic_replace_bytes(path: Path, content: bytes, operation: str) -> None:
    """Replace a destination through a fresh regular sibling file."""
    reject_aliased_destination(path, operation)
    existing_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    temporary = stage_sibling_bytes(path, content, existing_mode)
    try:
        commit_staged_file(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_migration_text(path: Path, content: str | bytes) -> None:
    """Atomically write migration bytes without following an existing alias."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    atomic_replace_bytes(path, data, "migration")


def backup_files(root: Path, rels: list[str]) -> Path:
    """Back up every path a migration may touch, including originally absent paths."""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out_dir = cache_root() / "backups" / repo_key(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"migration-{stamp}.zip"
    unique_rels = sorted(set(rels))
    entries = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in unique_rels:
            normalized, target = backup_target(root, rel)
            existed = target.is_file()
            entries.append({"path": normalized, "existed": existed})
            if existed:
                zf.write(target, arcname=f"files/{normalized}")
        meta = {
            "repo": str(root),
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "entries": entries,
        }
        zf.writestr("_migration.json", json.dumps(meta, ensure_ascii=False, indent=2))
    return out


def latest_backup(root: Path) -> Path | None:
    out_dir = cache_root() / "backups" / repo_key(root)
    if not out_dir.exists():
        return None
    backups = sorted(out_dir.glob("migration-*.zip"), key=lambda x: x.stat().st_mtime, reverse=True)
    return backups[0] if backups else None


def record_post_migration_digests(root: Path, backup: Path) -> None:
    """Attach post-migration hashes so rollback cannot overwrite later edits."""
    fd, temporary_name = tempfile.mkstemp(prefix=f".{backup.name}.", suffix=".zip", dir=backup.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(backup, "r") as source:
            meta = json.loads(source.read("_migration.json").decode("utf-8"))
            entries = meta.get("entries")
            if not isinstance(entries, list):
                raise ValueError("backup entries must be a list")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("backup entry must be an object")
                _, target = backup_target(root, entry.get("path"))
                entry["post_digest"] = file_hash(target) if target.is_file() else None
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as destination:
                for info in source.infolist():
                    if info.filename != "_migration.json":
                        destination.writestr(info, source.read(info.filename))
                destination.writestr("_migration.json", json.dumps(meta, ensure_ascii=False, indent=2))
        os.replace(temporary, backup)
    finally:
        if temporary.exists():
            temporary.unlink()


def make_failed_rollback_retryable(root: Path, backup: Path | None, rollback: dict | None) -> None:
    """Record the residual tree after recovery fails so an unchanged retry is safe."""
    if backup is None or not isinstance(rollback, dict) or rollback.get("ok"):
        return
    try:
        record_post_migration_digests(root, backup)
        rollback["retryable"] = True
    except Exception as exc:
        rollback["retryable"] = False
        rollback["retry_baseline_error"] = str(exc)


def rollback_migration(root: Path, backup: Path | None = None, *, _force_recovery: bool = False) -> dict:
    backup = backup or latest_backup(root)
    if backup is None or not backup.exists():
        return {"ok": False, "reason": "no-migration-backup"}
    try:
        with zipfile.ZipFile(backup, "r") as zf:
            meta = json.loads(zf.read("_migration.json").decode("utf-8"))
            if not isinstance(meta, dict):
                return {"ok": False, "reason": "rollback-invalid-backup", "backup": str(backup)}
            recorded_repo = Path(meta.get("repo", "")).resolve()
            if recorded_repo != root.resolve():
                return {"ok": False, "reason": "backup-repository-mismatch", "backup": str(backup)}
            raw_entries = meta.get("entries")
            if not isinstance(raw_entries, list):
                return {"ok": False, "reason": "rollback-invalid-backup", "backup": str(backup)}

            names = zf.namelist()
            entries: list[tuple[str, Path, bool, bytes | None, str | None]] = []
            for entry in raw_entries:
                if not isinstance(entry, dict):
                    raise ValueError("backup entry must be an object")
                rel = entry.get("path")
                normalized, target = backup_target(root, rel)
                if rel != normalized:
                    raise ValueError(f"backup path is not normalized: {rel!r}")
                existed = entry.get("existed")
                if not isinstance(existed, bool):
                    raise ValueError(f"backup entry has invalid existed value: {rel!r}")
                member = f"files/{normalized}"
                if existed:
                    if names.count(member) != 1:
                        raise ValueError(f"backup member mismatch for {normalized!r}")
                    data: bytes | None = zf.read(member)
                else:
                    data = None
                digest = entry.get("post_digest")
                if digest is not None and (not isinstance(digest, str) or len(digest) != 64):
                    raise ValueError(f"backup entry has invalid post_digest: {normalized!r}")
                entries.append((normalized, target, existed, data, digest))

            for _, target, _, _, _ in entries:
                if target.exists() or target.is_symlink():
                    reject_aliased_rollback_destination(target)

            conflicts: list[str] = []
            for rel, target, existed, _, digest in entries:
                present = target.exists() or target.is_symlink()
                if _force_recovery:
                    continue
                if not present:
                    if existed and digest is not None:
                        conflicts.append(rel)
                    continue
                if digest is None or not target.is_file() or file_hash(target) != digest:
                    conflicts.append(rel)
            if conflicts:
                return {
                    "ok": False,
                    "reason": "rollback-conflict",
                    "backup": str(backup),
                    "conflicts": conflicts,
                }

            transaction: list[dict] = []
            try:
                for rel, target, existed, data, _ in entries:
                    present = target.exists() or target.is_symlink()
                    mode = stat.S_IMODE(target.stat().st_mode) if present else None
                    before = stage_sibling_bytes(target, target.read_bytes(), mode) if present else None
                    desired = stage_sibling_bytes(target, data, mode) if existed and data is not None else None
                    transaction.append({
                        "rel": rel,
                        "target": target,
                        "existed": existed,
                        "present": present,
                        "before": before,
                        "desired": desired,
                    })

                restored: list[str] = []
                removed: list[str] = []
                try:
                    for item in transaction:
                        target = item["target"]
                        if item["existed"]:
                            staged = item["desired"]
                            assert isinstance(staged, Path)
                            reject_aliased_rollback_destination(target)
                            commit_staged_file(staged, target)
                            item["desired"] = None
                            restored.append(item["rel"])
                        elif item["present"]:
                            reject_aliased_rollback_destination(target)
                            target.unlink()
                            removed.append(item["rel"])
                except BaseException as apply_error:
                    recovery_errors: list[str] = []
                    for item in transaction:
                        target = item["target"]
                        try:
                            before = item["before"]
                            if isinstance(before, Path):
                                commit_staged_file(before, target)
                                item["before"] = None
                            elif target.exists() or target.is_symlink():
                                target.unlink()
                        except BaseException as recovery_error:
                            recovery_errors.append(f"{item['rel']}: {recovery_error}")
                    if recovery_errors:
                        recovery_detail = "; ".join(recovery_errors)
                        if not isinstance(apply_error, Exception):
                            try:
                                # All archive data is staged in memory by this point.
                                # Release the Windows file handle before atomically
                                # replacing metadata with the residual retry baseline.
                                zf.close()
                                record_post_migration_digests(root, backup)
                            except BaseException as baseline_error:
                                recovery_detail += f"; retry baseline failed: {baseline_error}"
                            if hasattr(apply_error, "add_note"):
                                apply_error.add_note(f"rollback recovery failed: {recovery_detail}")
                            raise apply_error
                        raise RuntimeError(
                            f"rollback apply failed ({apply_error}); recovery failed ({recovery_detail})"
                        ) from apply_error
                    if isinstance(apply_error, Exception):
                        raise RuntimeError(f"rollback apply failed and pre-rollback state was restored: {apply_error}") from apply_error
                    raise
                return {"ok": True, "backup": str(backup), "restored": restored, "removed": removed}
            finally:
                for item in transaction:
                    for key in ("before", "desired"):
                        temporary = item.get(key)
                        if isinstance(temporary, Path) and temporary.exists():
                            temporary.unlink()
    except Exception as exc:
        return {"ok": False, "reason": f"rollback-failed: {exc}", "backup": str(backup)}


def migrate_agent_text(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    out: list[str] = []
    for original_line in text.splitlines(keepends=True):
        if original_line.endswith("\r\n"):
            line, ending = original_line[:-2], "\r\n"
        elif original_line.endswith(("\r", "\n")):
            line, ending = original_line[:-1], original_line[-1]
        else:
            line, ending = original_line, ""
        if line in V1_CANONICAL_LINES:
            replacement = V1_CANONICAL_LINES[line]
            if replacement is None:
                changes.append(f"removed canonical v1 line: {line}")
            else:
                out.append(replacement + ending)
                changes.append(f"rewrote canonical v1 line: {line}")
            continue
        if line == V1_LEGACY_INVOCATION_LINE:
            changes.append("removed canonical v1 task-end invocation")
            continue
        out.append(original_line)
    return "".join(out), changes


def migrate_agent_bytes(content: bytes) -> tuple[bytes, list[str]]:
    text = content.decode("utf-8", errors="surrogateescape")
    migrated, changes = migrate_agent_text(text)
    return migrated.encode("utf-8", errors="surrogateescape"), changes


def legacy_invocation_ambiguities(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if LEGACY_SKILL in line and line != V1_LEGACY_INVOCATION_LINE
    ]


def migration_plan(root: Path, agents: list[str]) -> dict:
    status = detect_status(root)
    if status["state"] == "v2":
        return {
            "repo": str(root),
            "from": "v2",
            "to": "v2",
            "agents": agents,
            "already_v2": True,
            "semantic_review_required": False,
            "warnings": [],
            "actions": [],
        }
    actions: list[dict] = []
    warnings: list[str] = []
    semantic_review_required = False

    agents_file = root / "AGENTS.md"
    claude_file = root / "CLAUDE.md"
    if agents_file.exists():
        old_bytes = agents_file.read_bytes()
        old = old_bytes.decode("utf-8", errors="surrogateescape")
        new_bytes, changes = migrate_agent_bytes(old_bytes)
        ambiguities = legacy_invocation_ambiguities(old)
        if ambiguities:
            semantic_review_required = True
            warnings.append(
                "AGENTS.md contains non-canonical references to $codebase-documentation-architect. "
                "They were preserved; review their project-specific meaning before migration."
            )
        if new_bytes != old_bytes:
            actions.append({"action": "rewrite", "path": "AGENTS.md", "changes": changes})
    elif claude_file.exists() and "codex" in agents:
        semantic_review_required = True
        warnings.append("Standalone CLAUDE.md exists without AGENTS.md. Shared-vs-Claude-specific instructions need semantic review before enabling Codex.")
    elif claude_file.exists() and agents == ["claude"]:
        old_bytes = claude_file.read_bytes()
        old = old_bytes.decode("utf-8", errors="surrogateescape")
        new_bytes, changes = migrate_agent_bytes(old_bytes)
        ambiguities = legacy_invocation_ambiguities(old)
        if ambiguities:
            semantic_review_required = True
            warnings.append(
                "CLAUDE.md contains non-canonical references to $codebase-documentation-architect. "
                "They were preserved; review their project-specific meaning before migration."
            )
        if new_bytes != old_bytes:
            actions.append({"action": "rewrite", "path": "CLAUDE.md", "changes": changes})

    if claude_file.exists() and agents_file.exists():
        ctext = claude_file.read_text(encoding="utf-8", errors="replace").strip()
        if ctext == "@AGENTS.md":
            pass
        elif "claude" in agents and "@AGENTS.md" not in ctext:
            warnings.append("CLAUDE.md is standalone while AGENTS.md also exists; preserve its Claude-specific content and add @AGENTS.md only after review.")
            semantic_review_required = True
    elif "claude" in agents and agents_file.exists() and not claude_file.exists():
        actions.append({"action": "create", "path": "CLAUDE.md", "content": "@AGENTS.md\n"})

    legacy = status.get("legacy_signals", [])
    semantic_legacy = [
        signal for signal in legacy
        if signal == "legacy-documentation-system-subtree" or signal.startswith("generic-procedure:")
    ]
    if semantic_legacy:
        semantic_review_required = True
        warnings.append(
            "Legacy documentation-procedure files are present. Extract any project-specific facts before deleting or archiving them; automatic migration will not guess which content is durable."
        )

    actions.append({"action": "write-model", "path": MODEL_FILE, "content": default_model(agents)})
    return {
        "repo": str(root),
        "from": status["state"],
        "to": "v2",
        "agents": agents,
        "semantic_review_required": semantic_review_required,
        "warnings": warnings,
        "actions": actions,
    }


def apply_migration(root: Path, plan: dict, force_ambiguous: bool = False) -> dict:
    if plan.get("already_v2"):
        return {"ok": True, "applied": False, "reason": "already-v2", "files": []}
    if plan["semantic_review_required"] and not force_ambiguous:
        return {"ok": False, "applied": False, "reason": "semantic_review_required", "plan": plan}
    actions = [a for a in plan.get("actions", []) if a.get("action") in {"rewrite", "create", "write-model"}]
    if not actions:
        return {"ok": True, "applied": False, "reason": "no-changes", "files": []}

    backup: Path | None = None
    applied: list[str] = []
    try:
        prepared_actions: list[tuple[dict, Path]] = []
        for action in actions:
            _, path = migration_destination(root, action["path"])
            prepared_actions.append((action, path))

        staged: list[tuple[Path, str, bytes]] = []
        for action, path in prepared_actions:
            rel = action["path"]
            if action["action"] == "rewrite":
                old = path.read_bytes()
                new, _ = migrate_agent_bytes(old)
                if new != old:
                    staged.append((path, rel, new))
            elif action["action"] == "create":
                if not path.exists():
                    staged.append((path, rel, action["content"].encode("utf-8")))
            else:
                model_bytes = (json.dumps(action["content"], ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                staged.append((path, rel, model_bytes))

        backup = backup_files(root, [action["path"] for action in actions])
        for path, rel, content in staged:
            atomic_write_migration_text(path, content)
            applied.append(rel)

        validation = validate(root)
        if not validation["ok"]:
            rollback = rollback_migration(root, backup, _force_recovery=True)
            make_failed_rollback_retryable(root, backup, rollback)
            return {
                "ok": False,
                "applied": False,
                "reason": "validation-failed",
                "backup": str(backup),
                "files": applied,
                "validation": validation,
                "rollback": rollback,
            }
        record_post_migration_digests(root, backup)
        return {"ok": True, "applied": True, "backup": str(backup), "files": applied, "validation": validation}
    except BaseException as exc:
        rollback = rollback_migration(root, backup, _force_recovery=True) if backup else None
        make_failed_rollback_retryable(root, backup, rollback)
        if not isinstance(exc, Exception):
            raise
        return {
            "ok": False,
            "applied": False,
            "reason": f"migration-failed: {exc}",
            "backup": str(backup) if backup else None,
            "files": applied,
            "rollback": rollback,
        }


def create_marker(root: Path, agents: list[str], overwrite: bool = False, docs_are_product: bool = False) -> dict:
    p = root / MODEL_FILE
    if p.exists() and not overwrite:
        return {"ok": False, "reason": f"{MODEL_FILE} already exists"}
    model = default_model(agents)
    # Written only when true, so the marker stays minimal in code repositories
    # where the flag must remain off by default.
    if docs_are_product:
        model["docs_are_product"] = True
    p.write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(p)}


def scaffold(root: Path, agents: list[str], docs_are_product: bool = False) -> dict:
    """Create only missing, low-risk skeletons. Never overwrite project content."""
    created: list[str] = []
    if not (root / "README.md").exists():
        title = root.name.replace("-", " ").replace("_", " ").strip().title() or "Project"
        (root / "README.md").write_text(
            f"# {title}\n\nProject overview. Replace this placeholder with the repository purpose and setup.\n\n"
            "## Documentation\n\n- [Documentation index](docs/README.md)\n",
            encoding="utf-8",
        )
        created.append("README.md")
    if "codex" in agents and not (root / "AGENTS.md").exists():
        (root / "AGENTS.md").write_text(
            "# Agent Guide\n\n## Documentation\n\n"
            "- Use [docs/README.md](docs/README.md) when project documentation is needed.\n"
            "- Use [MEMORY.md](MEMORY.md) for current cross-session project context when the task needs it.\n",
            encoding="utf-8",
        )
        created.append("AGENTS.md")
    if "claude" in agents and not (root / "CLAUDE.md").exists():
        if (root / "AGENTS.md").exists():
            (root / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        else:
            (root / "CLAUDE.md").write_text("# Claude Code\n", encoding="utf-8")
        created.append("CLAUDE.md")
    if not (root / "MEMORY.md").exists():
        (root / "MEMORY.md").write_text(
            "# Project Memory\n\n## Current Priorities\n\n- None recorded.\n\n"
            "## Critical Invariants\n\n- None recorded.\n\n"
            "## Active Gaps and Risks\n\n- None recorded.\n",
            encoding="utf-8",
        )
        created.append("MEMORY.md")
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    if not (docs / "README.md").exists():
        (docs / "README.md").write_text(
            "# Documentation\n\nUse this page as the project documentation index.\n\n## Start Here\n\n"
            "- [Project README](../README.md)\n- [Project memory](../MEMORY.md)\n",
            encoding="utf-8",
        )
        created.append("docs/README.md")
    marker = create_marker(root, agents, overwrite=False, docs_are_product=docs_are_product)
    if marker.get("ok"):
        created.append(MODEL_FILE)
    return {"ok": True, "created": created, "validation": validate(root)}


def parse_agents(value: str) -> list[str]:
    if value == "both":
        return ["codex", "claude"]
    if value in {"codex", "claude"}:
        return [value]
    raise argparse.ArgumentTypeError("agents must be codex, claude, or both")


def emit(obj: object, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                print(f"{k}: {json.dumps(v, ensure_ascii=False)}")
            else:
                print(f"{k}: {v}")
    else:
        print(obj)


def main() -> int:
    parser = argparse.ArgumentParser(prog="docsctl", description="Deterministic support for Codebase Documentation Kit v2")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ["status", "scan", "validate"]:
        p = sub.add_parser(name)
        p.add_argument("repo", nargs="?", default=".")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("migrate")
    p.add_argument("repo", nargs="?", default=".")
    p.add_argument("--agents", default="both", choices=["codex", "claude", "both"])
    p.add_argument("--apply", action="store_true")
    p.add_argument("--force-ambiguous", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("mark")
    p.add_argument("repo", nargs="?", default=".")
    p.add_argument("--agents", default="both", choices=["codex", "claude", "both"])
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("scaffold")
    p.add_argument("repo", nargs="?", default=".")
    p.add_argument("--agents", default="both", choices=["codex", "claude", "both"])
    p.add_argument(
        "--docs-are-product",
        action="store_true",
        help="mark the repository as documentation-as-product: edits to tracked docs count as needing review",
    )
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("rollback")
    p.add_argument("repo", nargs="?", default=".")
    p.add_argument("--backup", help="Specific migration backup zip; defaults to the newest backup for this repository")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("session-start")
    p.add_argument("repo", nargs="?", default=".")
    p.add_argument("--session", required=True)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("impact")
    p.add_argument("repo", nargs="?", default=".")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--session")
    group.add_argument("--latest", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("session-finalize")
    p.add_argument("repo", nargs="?", default=".")
    p.add_argument("--session", required=True)
    p.add_argument("--json", action="store_true")

    args = parser.parse_args()
    try:
        root = resolve_repo(args.repo)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.command == "status":
        obj = detect_status(root)
    elif args.command == "scan":
        obj = compact_scan(root)
    elif args.command == "validate":
        obj = validate(root)
    elif args.command == "migrate":
        agents = parse_agents(args.agents)
        plan = migration_plan(root, agents)
        obj = apply_migration(root, plan, args.force_ambiguous) if args.apply else plan
    elif args.command == "mark":
        obj = create_marker(root, parse_agents(args.agents), args.overwrite)
    elif args.command == "scaffold":
        obj = scaffold(root, parse_agents(args.agents), docs_are_product=args.docs_are_product)
    elif args.command == "rollback":
        backup = Path(args.backup).expanduser().resolve() if args.backup else None
        obj = rollback_migration(root, backup)
    elif args.command == "session-start":
        if detect_status(root)["state"] != "v2":
            obj = {"ok": True, "active": False, "reason": "repo-not-v2"}
        elif not git_available(root):
            obj = {"ok": True, "active": False, "reason": "git-unavailable"}
        else:
            snapshot(root, args.session)
            obj = {"ok": True, "active": True, "session": args.session}
    elif args.command == "impact":
        snap, sid = load_snapshot(root, args.session, latest=args.latest or not args.session)
        if not snap:
            obj = {"ok": False, "reason": "no-session-snapshot", "session": sid}
        else:
            obj = {"ok": True, "session": sid, **impact_report(root, snap)}
    elif args.command == "session-finalize":
        if detect_status(root)["state"] == "v2":
            snapshot(root, args.session)
            obj = {"ok": True, "session": args.session}
        else:
            obj = {"ok": True, "active": False}
    else:
        return 2

    emit(obj, getattr(args, "json", False))
    if isinstance(obj, dict) and obj.get("ok") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
