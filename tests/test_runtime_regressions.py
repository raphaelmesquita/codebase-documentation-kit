from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
HOOK_CODEX = RUNTIME / "hook_codex.py"
HOOK_CLAUDE = RUNTIME / "hook_claude.py"
sys.path.insert(0, str(RUNTIME))
import docsctl  # noqa: E402
import hook_common  # noqa: E402


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=True)


def init_git(repo: Path) -> None:
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")


def commit_all(repo: Path, message: str = "initial") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)


def canonical_v1_agent(extra: str = "") -> str:
    return """# Agent Guide

## Repository Rules

- Run tests before commit.

## Documentation and Memory

- Start with [docs/README.md](docs/README.md) for the project documentation map.
- Check [MEMORY.md](MEMORY.md) for current priorities, recent deltas, and active risks.
- Check [docs/state/README.md](docs/state/README.md) for durable project context, decisions, assumptions, and known issues.
- At the end of any task that changes behavior, documentation, structure, or durable project knowledge, invoke `$codebase-documentation-architect` to decide whether memory or docs need updates.
""" + extra


class RuntimeRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        init_git(self.repo)
        self.cache = self.root / "cache"
        self.old_xdg = os.environ.get("XDG_CACHE_HOME")
        self.old_local = os.environ.get("LOCALAPPDATA")
        os.environ["XDG_CACHE_HOME"] = str(self.cache)
        os.environ["LOCALAPPDATA"] = str(self.cache)

    def tearDown(self) -> None:
        self._restore_environment("XDG_CACHE_HOME", self.old_xdg)
        self._restore_environment("LOCALAPPDATA", self.old_local)

    @staticmethod
    def _restore_environment(name: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    def make_v1(self, *, claude: bool = False, extra_agent: str = "") -> None:
        (self.repo / "AGENTS.md").write_text(canonical_v1_agent(extra_agent), encoding="utf-8")
        if claude:
            (self.repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (self.repo / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
        (self.repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.repo / "docs" / "state").mkdir(parents=True)
        (self.repo / "docs" / "README.md").write_text("# Documentation\n", encoding="utf-8")
        (self.repo / "docs" / "state" / "README.md").write_text("# State\n", encoding="utf-8")
        commit_all(self.repo)

    def make_v2(self) -> None:
        result = docsctl.scaffold(self.repo, ["codex", "claude"])
        self.assertTrue(result["validation"]["ok"], result)
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.repo / "tests").mkdir(exist_ok=True)
        (self.repo / "tests" / "test_app.py").write_text("def test_value():\n    assert True\n", encoding="utf-8")
        commit_all(self.repo)

    def test_m02_preserves_noncanonical_legacy_skill_references_and_marks_ambiguity(self) -> None:
        custom = "\n- Before invoking `$codebase-documentation-architect`, obtain owner approval.\n"
        self.make_v1(claude=True, extra_agent=custom)

        plan = docsctl.migration_plan(self.repo, ["codex", "claude"])

        self.assertTrue(plan["semantic_review_required"], plan)
        self.assertIn("non-canonical references", " ".join(plan["warnings"]))
        migrated, _ = docsctl.migrate_agent_text((self.repo / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertIn(custom.strip(), migrated)
        self.assertNotIn(docsctl.V1_LEGACY_INVOCATION_LINE, migrated)
        result = docsctl.apply_migration(self.repo, plan)
        self.assertFalse(result["applied"], result)
        self.assertIn(custom.strip(), (self.repo / "AGENTS.md").read_text(encoding="utf-8"))

    def test_m06_already_v2_migration_is_an_explicit_noop_without_backup_churn(self) -> None:
        self.make_v1(claude=True)
        first = docsctl.apply_migration(self.repo, docsctl.migration_plan(self.repo, ["codex", "claude"]))
        self.assertTrue(first["ok"], first)
        backups_before = sorted((self.cache / docsctl.TOOLKIT_NAME / "backups").rglob("migration-*.zip"))

        plan = docsctl.migration_plan(self.repo, ["codex", "claude"])
        second = docsctl.apply_migration(self.repo, plan)

        self.assertTrue(plan["already_v2"], plan)
        self.assertEqual(plan["actions"], [])
        self.assertEqual(second, {"ok": True, "applied": False, "reason": "already-v2", "files": []})
        self.assertEqual(sorted((self.cache / docsctl.TOOLKIT_NAME / "backups").rglob("migration-*.zip")), backups_before)

    def test_m07_rollback_preserves_post_migration_edits_and_reports_conflict(self) -> None:
        self.make_v1(claude=False)
        before_agents = (self.repo / "AGENTS.md").read_text(encoding="utf-8")
        result = docsctl.apply_migration(self.repo, docsctl.migration_plan(self.repo, ["codex", "claude"]))
        self.assertTrue(result["ok"], result)
        created = self.repo / "CLAUDE.md"
        self.assertTrue(created.exists())
        created.write_text("# User-owned Claude guidance\n", encoding="utf-8")

        rollback = docsctl.rollback_migration(self.repo, Path(result["backup"]))

        self.assertFalse(rollback["ok"], rollback)
        self.assertEqual(rollback["reason"], "rollback-conflict")
        self.assertEqual(rollback["conflicts"], ["CLAUDE.md"])
        self.assertEqual(created.read_text(encoding="utf-8"), "# User-owned Claude guidance\n")
        self.assertNotEqual((self.repo / "AGENTS.md").read_text(encoding="utf-8"), before_agents)

    def test_rollback_treats_deleted_original_file_as_conflict_unless_forced_recovery(self) -> None:
        self.make_v1(claude=True)
        original_agents = (self.repo / "AGENTS.md").read_text(encoding="utf-8")
        result = docsctl.apply_migration(self.repo, docsctl.migration_plan(self.repo, ["codex", "claude"]))
        self.assertTrue(result["ok"], result)
        (self.repo / "AGENTS.md").unlink()

        rollback = docsctl.rollback_migration(self.repo, Path(result["backup"]))

        self.assertFalse(rollback["ok"], rollback)
        self.assertEqual(rollback["reason"], "rollback-conflict")
        self.assertEqual(rollback["conflicts"], ["AGENTS.md"])
        self.assertFalse((self.repo / "AGENTS.md").exists(), "intentional deletion must not be silently undone")
        self.assertTrue((self.repo / docsctl.MODEL_FILE).exists(), "conflicted rollback must not partially restore")

        forced = docsctl.rollback_migration(self.repo, Path(result["backup"]), _force_recovery=True)
        self.assertTrue(forced["ok"], forced)
        self.assertEqual((self.repo / "AGENTS.md").read_text(encoding="utf-8"), original_agents)
        self.assertFalse((self.repo / docsctl.MODEL_FILE).exists())

    def test_rollback_rejects_hard_linked_existing_target_without_mutating_alias(self) -> None:
        self.make_v1(claude=True)
        result = docsctl.apply_migration(self.repo, docsctl.migration_plan(self.repo, ["codex", "claude"]))
        self.assertTrue(result["ok"], result)
        agents = self.repo / "AGENTS.md"
        unrelated = self.repo / "unrelated-same-content.txt"
        migrated_bytes = agents.read_bytes()
        unrelated.write_bytes(migrated_bytes)
        agents.unlink()
        os.link(unrelated, agents)
        before_unrelated_hash = docsctl.file_hash(unrelated)
        self.assertGreater(agents.stat().st_nlink, 1)

        rollback = docsctl.rollback_migration(self.repo, Path(result["backup"]))

        self.assertFalse(rollback["ok"], rollback)
        self.assertIn("rollback destination has multiple hard links: AGENTS.md", rollback["reason"])
        self.assertEqual(unrelated.read_bytes(), migrated_bytes)
        self.assertEqual(docsctl.file_hash(unrelated), before_unrelated_hash)
        self.assertEqual(agents.read_bytes(), migrated_bytes)
        self.assertTrue((self.repo / docsctl.MODEL_FILE).exists(), "rejected rollback must not partially restore")

    def test_rollback_failure_on_second_restore_recovers_exact_tree_and_allows_retry(self) -> None:
        self.make_v1(claude=True)
        original_agents = (self.repo / "AGENTS.md").read_bytes()
        original_model = b'{"legacy-marker": true}\r\n'
        (self.repo / docsctl.MODEL_FILE).write_bytes(original_model)
        migration = docsctl.apply_migration(self.repo, docsctl.migration_plan(self.repo, ["codex", "claude"]))
        self.assertTrue(migration["ok"], migration)

        def repository_bytes() -> dict[str, bytes]:
            return {
                path.relative_to(self.repo).as_posix(): path.read_bytes()
                for path in self.repo.rglob("*")
                if path.is_file() and ".git" not in path.relative_to(self.repo).parts
            }

        before_rollback = repository_bytes()
        original_commit = docsctl.commit_staged_file
        restore_calls = 0

        def fail_second_restore(staged: Path, destination: Path) -> None:
            nonlocal restore_calls
            restore_calls += 1
            if restore_calls == 2:
                raise OSError("injected second restore failure")
            original_commit(staged, destination)

        with patch.object(docsctl, "commit_staged_file", new=fail_second_restore):
            failed = docsctl.rollback_migration(self.repo, Path(migration["backup"]))

        self.assertFalse(failed["ok"], failed)
        self.assertIn("pre-rollback state was restored", failed["reason"])
        self.assertEqual(repository_bytes(), before_rollback)
        self.assertFalse(any(path.name.endswith(".tmp") for path in self.repo.iterdir()))

        retry = docsctl.rollback_migration(self.repo, Path(migration["backup"]))
        self.assertTrue(retry["ok"], retry)
        self.assertEqual((self.repo / "AGENTS.md").read_bytes(), original_agents)
        self.assertEqual((self.repo / docsctl.MODEL_FILE).read_bytes(), original_model)

    def test_rollback_rejects_path_escapes_and_symlinked_parents(self) -> None:
        outside = self.root / "outside.txt"
        outside.write_text("unchanged\n", encoding="utf-8")

        for index, rel in enumerate(["../outside.txt", "..\\outside.txt", "nested\\..\\outside.txt", "/outside.txt", "C:\\outside.txt"]):
            backup = self.root / f"malicious-{index}.zip"
            with zipfile.ZipFile(backup, "w") as archive:
                archive.writestr(
                    "_migration.json",
                    json.dumps({"repo": str(self.repo), "entries": [{"path": rel, "existed": False, "post_digest": None}]}),
                )
            result = docsctl.rollback_migration(self.repo, backup)
            self.assertFalse(result["ok"], result)
            self.assertEqual(outside.read_text(encoding="utf-8"), "unchanged\n")

        link = self.repo / "linked"
        try:
            link.symlink_to(self.root, target_is_directory=True)
            resolve_patch = None
        except OSError:
            original_resolve = Path.resolve

            def resolve_symlink_escape(path: Path, *args: object, **kwargs: object) -> Path:
                if path == link / "outside.txt":
                    return outside
                return original_resolve(path, *args, **kwargs)

            resolve_patch = patch.object(Path, "resolve", new=resolve_symlink_escape)
        backup = self.root / "symlink.zip"
        with zipfile.ZipFile(backup, "w") as archive:
            archive.writestr(
                "_migration.json",
                json.dumps(
                    {
                        "repo": str(self.repo),
                        "entries": [{"path": "linked/outside.txt", "existed": False, "post_digest": docsctl.file_hash(outside)}],
                    }
                ),
            )
        if resolve_patch is None:
            result = docsctl.rollback_migration(self.repo, backup)
        else:
            with resolve_patch:
                result = docsctl.rollback_migration(self.repo, backup)
        self.assertFalse(result["ok"], result)
        self.assertEqual(outside.read_text(encoding="utf-8"), "unchanged\n")

    def test_m08_write_and_validation_failures_automatically_restore_the_pre_migration_tree(self) -> None:
        self.make_v1(claude=False)
        before_agents = (self.repo / "AGENTS.md").read_text(encoding="utf-8")
        plan = docsctl.migration_plan(self.repo, ["codex", "claude"])
        original_atomic_write = docsctl.atomic_write_migration_text

        def fail_claude_write(path: Path, content: str) -> None:
            if path == self.repo / "CLAUDE.md":
                raise OSError("injected write failure")
            original_atomic_write(path, content)

        with patch.object(docsctl, "atomic_write_migration_text", new=fail_claude_write):
            failed_write = docsctl.apply_migration(self.repo, plan)

        self.assertFalse(failed_write["ok"], failed_write)
        self.assertIn("migration-failed", failed_write["reason"])
        self.assertTrue(failed_write["rollback"]["ok"], failed_write)
        self.assertEqual((self.repo / "AGENTS.md").read_text(encoding="utf-8"), before_agents)
        self.assertFalse((self.repo / "CLAUDE.md").exists())
        self.assertFalse((self.repo / docsctl.MODEL_FILE).exists())

        with patch.object(docsctl, "validate", return_value={"ok": False, "failures": ["injected validation failure"], "warnings": []}):
            failed_validation = docsctl.apply_migration(self.repo, plan)

        self.assertFalse(failed_validation["ok"], failed_validation)
        self.assertEqual(failed_validation["reason"], "validation-failed")
        self.assertTrue(failed_validation["rollback"]["ok"], failed_validation)
        self.assertEqual((self.repo / "AGENTS.md").read_text(encoding="utf-8"), before_agents)
        self.assertFalse((self.repo / "CLAUDE.md").exists())
        self.assertFalse((self.repo / docsctl.MODEL_FILE).exists())

    def test_failed_automatic_rollback_can_be_retried_but_later_edits_conflict(self) -> None:
        self.make_v1(claude=False)
        original_agents = (self.repo / "AGENTS.md").read_bytes()
        plan = docsctl.migration_plan(self.repo, ["codex", "claude"])
        original_atomic_write = docsctl.atomic_write_migration_text
        original_commit = docsctl.commit_staged_file
        commit_calls = 0

        def fail_claude_write(path: Path, content: str | bytes) -> None:
            if path == self.repo / "CLAUDE.md":
                raise OSError("injected migration write failure")
            original_atomic_write(path, content)

        def fail_first_automatic_restore(staged: Path, destination: Path) -> None:
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 2:
                raise OSError("injected automatic rollback failure")
            original_commit(staged, destination)

        with (
            patch.object(docsctl, "atomic_write_migration_text", new=fail_claude_write),
            patch.object(docsctl, "commit_staged_file", new=fail_first_automatic_restore),
        ):
            failed = docsctl.apply_migration(self.repo, plan)

        self.assertFalse(failed["ok"], failed)
        self.assertFalse(failed["rollback"]["ok"], failed)
        self.assertTrue(failed["rollback"]["retryable"], failed)
        backup = Path(failed["backup"])

        migrated_agents = (self.repo / "AGENTS.md").read_bytes()
        self.assertNotEqual(migrated_agents, original_agents)
        retry = docsctl.rollback_migration(self.repo, backup)
        self.assertTrue(retry["ok"], retry)
        self.assertEqual((self.repo / "AGENTS.md").read_bytes(), original_agents)

        second_failure = None
        with (
            patch.object(docsctl, "atomic_write_migration_text", new=fail_claude_write),
            patch.object(docsctl, "commit_staged_file", new=fail_first_automatic_restore),
        ):
            commit_calls = 0
            second_failure = docsctl.apply_migration(self.repo, plan)
        self.assertFalse(second_failure["ok"], second_failure)
        edited = (self.repo / "AGENTS.md").read_bytes() + b"\nuser edit after failure\n"
        (self.repo / "AGENTS.md").write_bytes(edited)
        conflict = docsctl.rollback_migration(self.repo, Path(second_failure["backup"]))
        self.assertFalse(conflict["ok"], conflict)
        self.assertEqual(conflict["reason"], "rollback-conflict")
        self.assertIn("AGENTS.md", conflict["conflicts"])
        self.assertEqual((self.repo / "AGENTS.md").read_bytes(), edited)

    def test_keyboard_interrupt_restores_migration_and_rollback_transactions(self) -> None:
        self.make_v1(claude=False)
        original_model = b'{"preexisting": true}\r\n'
        (self.repo / docsctl.MODEL_FILE).write_bytes(original_model)
        original_agents = (self.repo / "AGENTS.md").read_bytes()
        plan = docsctl.migration_plan(self.repo, ["codex", "claude"])
        original_write = docsctl.atomic_write_migration_text
        write_calls = 0

        def interrupt_second_write(path: Path, content: str | bytes) -> None:
            nonlocal write_calls
            write_calls += 1
            if write_calls == 2:
                raise KeyboardInterrupt("injected migration interruption")
            original_write(path, content)

        with patch.object(docsctl, "atomic_write_migration_text", new=interrupt_second_write):
            with self.assertRaises(KeyboardInterrupt):
                docsctl.apply_migration(self.repo, plan)
        self.assertEqual((self.repo / "AGENTS.md").read_bytes(), original_agents)
        self.assertEqual((self.repo / docsctl.MODEL_FILE).read_bytes(), original_model)
        self.assertFalse((self.repo / "CLAUDE.md").exists())

        migration = docsctl.apply_migration(self.repo, plan)
        self.assertTrue(migration["ok"], migration)

        def repository_bytes() -> dict[str, bytes]:
            return {
                path.relative_to(self.repo).as_posix(): path.read_bytes()
                for path in self.repo.rglob("*")
                if path.is_file() and ".git" not in path.relative_to(self.repo).parts
            }

        migrated = repository_bytes()
        original_commit = docsctl.commit_staged_file
        commit_calls = 0

        def interrupt_second_restore(staged: Path, destination: Path) -> None:
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 2:
                raise KeyboardInterrupt("injected rollback interruption")
            original_commit(staged, destination)

        with patch.object(docsctl, "commit_staged_file", new=interrupt_second_restore):
            with self.assertRaises(KeyboardInterrupt):
                docsctl.rollback_migration(self.repo, Path(migration["backup"]))
        self.assertEqual(repository_bytes(), migrated)
        retry = docsctl.rollback_migration(self.repo, Path(migration["backup"]))
        self.assertTrue(retry["ok"], retry)
        self.assertEqual((self.repo / "AGENTS.md").read_bytes(), original_agents)
        self.assertEqual((self.repo / docsctl.MODEL_FILE).read_bytes(), original_model)

    def test_rollback_propagates_interrupt_when_compensation_fails_and_remains_retryable(self) -> None:
        self.make_v1(claude=False)
        original_model = b'{"preexisting": true}\n'
        (self.repo / docsctl.MODEL_FILE).write_bytes(original_model)
        original_agents = (self.repo / "AGENTS.md").read_bytes()
        migration = docsctl.apply_migration(
            self.repo,
            docsctl.migration_plan(self.repo, ["codex", "claude"]),
        )
        self.assertTrue(migration["ok"], migration)
        original_commit = docsctl.commit_staged_file
        commit_calls = 0

        def interrupt_then_fail_compensation(staged: Path, destination: Path) -> None:
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 2:
                raise KeyboardInterrupt("rollback interrupted")
            if commit_calls == 3:
                raise OSError("compensating restore failed")
            original_commit(staged, destination)

        with patch.object(docsctl, "commit_staged_file", new=interrupt_then_fail_compensation):
            with self.assertRaises(KeyboardInterrupt) as raised:
                docsctl.rollback_migration(self.repo, Path(migration["backup"]))
        self.assertTrue(
            any("compensating restore failed" in note for note in getattr(raised.exception, "__notes__", [])),
            getattr(raised.exception, "__notes__", []),
        )

        retry = docsctl.rollback_migration(self.repo, Path(migration["backup"]))
        self.assertTrue(retry["ok"], retry)
        self.assertEqual((self.repo / "AGENTS.md").read_bytes(), original_agents)
        self.assertEqual((self.repo / docsctl.MODEL_FILE).read_bytes(), original_model)
        self.assertFalse((self.repo / "CLAUDE.md").exists())

    def test_dirty_index_object_change_is_detected_when_status_and_worktree_match(self) -> None:
        source = self.repo / "app.py"
        source.write_text("print('base')\n", encoding="utf-8")
        commit_all(self.repo)
        source.write_text("print('staged one')\n", encoding="utf-8")
        git(self.repo, "add", "app.py")
        source.write_text("print('worktree')\n", encoding="utf-8")
        self.assertEqual(git(self.repo, "status", "--porcelain", "--", "app.py").stdout[:2], "MM")
        docsctl.snapshot(self.repo, "index-change")

        blob = subprocess.run(
            ["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"],
            input="print('staged two')\n",
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        git(self.repo, "update-index", "--cacheinfo", "100644", blob, "app.py")
        self.assertEqual(git(self.repo, "status", "--porcelain", "--", "app.py").stdout[:2], "MM")

        snapshot, _ = docsctl.load_snapshot(self.repo, "index-change")
        report = docsctl.impact_report(self.repo, snapshot)
        self.assertTrue(report["needs_documentation_review"], report)
        self.assertEqual(report["changed"][0]["reason"], "modified-preexisting-index")

    def test_migration_rejects_hard_linked_model_before_any_write(self) -> None:
        self.make_v1(claude=True)
        readme = self.repo / "README.md"
        model = self.repo / docsctl.MODEL_FILE
        before_readme = readme.read_bytes()
        before_readme_hash = docsctl.file_hash(readme)
        before_agents = (self.repo / "AGENTS.md").read_bytes()
        os.link(readme, model)
        self.assertGreater(model.stat().st_nlink, 1)

        result = docsctl.apply_migration(
            self.repo,
            docsctl.migration_plan(self.repo, ["codex", "claude"]),
        )

        self.assertFalse(result["ok"], result)
        self.assertFalse(result["applied"], result)
        self.assertIn("multiple hard links: .docsctl.json", result["reason"])
        self.assertIsNone(result["backup"])
        self.assertEqual(result["files"], [])
        self.assertEqual(readme.read_bytes(), before_readme)
        self.assertEqual(docsctl.file_hash(readme), before_readme_hash)
        self.assertEqual(model.read_bytes(), before_readme)
        self.assertEqual((self.repo / "AGENTS.md").read_bytes(), before_agents)

    def test_m02_crlf_and_custom_bytes_are_preserved_outside_exact_line_transforms(self) -> None:
        canonical_docs = next(iter(docsctl.V1_CANONICAL_LINES))
        canonical_memory = list(docsctl.V1_CANONICAL_LINES)[1]
        canonical_state = list(docsctl.V1_CANONICAL_LINES)[2]
        before = (
            "# Agent Guide\r\n"
            "\r\n"
            f"{canonical_docs}\r\n"
            f"{canonical_memory}\r\n"
            f"{canonical_state}\r\n"
            f"{docsctl.V1_LEGACY_INVOCATION_LINE}\r\n"
            "\r\n"
            "## Custom Section\r\n"
            "\r\n"
            "\r\n"
            "- Preserve trailing spaces.  \r\n"
            "- Preserve café and emoji 🧭.\r\n"
            "\r\n"
            "tail-without-newline"
        ).encode("utf-8")
        agents = self.repo / "AGENTS.md"
        agents.write_bytes(before)
        (self.repo / "CLAUDE.md").write_bytes(b"@AGENTS.md\r\n")
        (self.repo / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
        (self.repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.repo / "docs" / "state").mkdir(parents=True)
        (self.repo / "docs" / "README.md").write_text("# Documentation\n", encoding="utf-8")
        (self.repo / "docs" / "state" / "README.md").write_text("# State\n", encoding="utf-8")
        commit_all(self.repo)

        result = docsctl.apply_migration(self.repo, docsctl.migration_plan(self.repo, ["codex", "claude"]))

        self.assertTrue(result["ok"], result)
        expected = before
        for original, replacement in docsctl.V1_CANONICAL_LINES.items():
            original_bytes = (original + "\r\n").encode("utf-8")
            replacement_bytes = ((replacement + "\r\n").encode("utf-8") if replacement is not None else b"")
            expected = expected.replace(original_bytes, replacement_bytes)
        expected = expected.replace((docsctl.V1_LEGACY_INVOCATION_LINE + "\r\n").encode("utf-8"), b"")
        self.assertEqual(agents.read_bytes(), expected)
        self.assertIn(b"\r\n\r\n\r\n- Preserve trailing spaces.  \r\n", agents.read_bytes())
        self.assertTrue(agents.read_bytes().endswith(b"tail-without-newline"))

    def test_i03_dist_build_and_minified_assets_are_generated_only(self) -> None:
        self.make_v2()
        (self.repo / "dist").mkdir()
        (self.repo / "build").mkdir()
        (self.repo / "dist" / "bundle.min.js").write_text("one\n", encoding="utf-8")
        (self.repo / "build" / "styles.min.css").write_text("one\n", encoding="utf-8")
        commit_all(self.repo, "assets")
        docsctl.snapshot(self.repo, "generated")
        (self.repo / "dist" / "bundle.min.js").write_text("two\n", encoding="utf-8")
        (self.repo / "build" / "styles.min.css").write_text("two\n", encoding="utf-8")

        report = docsctl.impact_report(self.repo, docsctl.load_snapshot(self.repo, "generated")[0])

        self.assertEqual(report["categories"], {"generated": 2})
        self.assertFalse(report["needs_documentation_review"], report)

    def test_i10_scans_active_inline_image_and_reference_links_without_code_false_positives(self) -> None:
        self.make_v2()
        docsctl.snapshot(self.repo, "links")
        (self.repo / "docs" / "file(1).md").write_text("# Valid\n", encoding="utf-8")
        (self.repo / "docs" / "README.md").write_text(
            "# Documentation\n\n"
            "[Inline](missing-inline.md)\n"
            "![Diagram](missing-image.png)\n"
            "[Reference][missing-reference]\n\n"
            "[missing-reference]: missing-reference.md\n\n"
            "[Balanced](file(1).md)\n"
            "`[Inline code](ignored-inline.md)`\n\n"
            "```md\n[Example](ignored-fenced.md)\n```\n",
            encoding="utf-8",
        )

        validation = docsctl.validate(self.repo)
        stop = hook_common.evaluate_stop({"cwd": str(self.repo), "session_id": "links"})

        self.assertFalse(validation["ok"], validation)
        self.assertTrue(any("missing-inline.md" in item for item in validation["failures"]))
        self.assertTrue(any("missing-image.png" in item for item in validation["failures"]))
        self.assertTrue(any("missing-reference.md" in item for item in validation["failures"]))
        self.assertFalse(any("file(1" in item or "ignored-" in item for item in validation["failures"]))
        self.assertEqual((stop["action"], stop["kind"]), ("continue", "validation"))

    def test_identical_additional_validation_failure_is_new_debt(self) -> None:
        self.make_v2()
        docs_index = self.repo / "docs" / "README.md"
        docs_index.write_text("# Documentation\n\n[First](missing.md)\n", encoding="utf-8")
        baseline_validation = docsctl.validate(self.repo)
        failure = "docs/README.md has missing link target: missing.md"
        self.assertEqual(baseline_validation["failures"], [failure])
        self.assertEqual(baseline_validation["failure_counts"], {failure: 1})
        docsctl.snapshot(self.repo, "duplicate-debt")
        docs_index.write_text(
            "# Documentation\n\n[First](missing.md)\n[Second](missing.md)\n",
            encoding="utf-8",
        )

        current_validation = docsctl.validate(self.repo)
        stop = hook_common.evaluate_stop({"cwd": str(self.repo), "session_id": "duplicate-debt"})

        self.assertEqual(current_validation["failures"], [failure], "public failure messages remain deduplicated")
        self.assertEqual(current_validation["failure_counts"], {failure: 2})
        self.assertEqual((stop["action"], stop["kind"]), ("continue", "validation"))
        self.assertIn(failure, stop["message"])

    def test_i11_non_git_impact_is_explicitly_indeterminate(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        result = docsctl.scaffold(plain, ["codex", "claude"])
        self.assertTrue(result["validation"]["ok"], result)
        docsctl.snapshot(plain, "nogit")
        (plain / "src").mkdir()
        (plain / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

        snapshot, _ = docsctl.load_snapshot(plain, "nogit")
        report = docsctl.impact_report(plain, snapshot)
        stop = hook_common.evaluate_stop({"cwd": str(plain), "session_id": "nogit"})
        unsnapshotted_stop = hook_common.evaluate_stop({"cwd": str(plain), "session_id": "fresh-nogit"})

        self.assertFalse(snapshot["git_available"])
        self.assertTrue(report["impact_indeterminate"], report)
        self.assertEqual(report["reason"], "git-unavailable")
        self.assertEqual((stop["action"], stop["kind"]), ("continue", "git-unavailable"))
        self.assertEqual((unsnapshotted_stop["action"], unsnapshotted_stop["kind"]), ("continue", "git-unavailable"))

    def test_i12_malformed_model_diagnostics_are_structured_and_never_raise(self) -> None:
        self.make_v2()
        model_path = self.repo / docsctl.MODEL_FILE

        model_path.write_text("{ invalid", encoding="utf-8")
        invalid_json = docsctl.validate(self.repo)
        self.assertIn("Malformed .docsctl.json: invalid JSON.", invalid_json["failures"])

        model_path.write_text("[]", encoding="utf-8")
        non_object = docsctl.validate(self.repo)
        self.assertIn("Malformed .docsctl.json: top-level JSON value must be an object.", non_object["failures"])

        model_path.write_text(
            json.dumps(
                {
                    "schema_version": "2",
                    "toolkit": 2,
                    "agents": "both",
                    "budgets": {"memory_max_bytes": "many", "docs_index_max_bytes": -1},
                }
            ),
            encoding="utf-8",
        )
        invalid_types = docsctl.validate(self.repo)
        self.assertIn("Malformed .docsctl.json: schema_version must be an integer.", invalid_types["failures"])
        self.assertIn("Malformed .docsctl.json: toolkit must be a string.", invalid_types["failures"])
        self.assertIn("Malformed .docsctl.json: agents must be a non-empty list containing unique 'codex' and/or 'claude' values.", invalid_types["failures"])
        self.assertIn("Malformed .docsctl.json: budgets.memory_max_bytes must be a non-negative integer.", invalid_types["failures"])
        self.assertIn("Malformed .docsctl.json: budgets.docs_index_max_bytes must be a non-negative integer.", invalid_types["failures"])

    def test_stop_rebaseline_reuses_the_validation_result_for_unchanged_and_test_only_sessions(self) -> None:
        self.make_v2()
        docsctl.snapshot(self.repo, "unchanged")
        with patch.object(docsctl, "validate", wraps=docsctl.validate) as validate:
            unchanged = hook_common.evaluate_stop({"cwd": str(self.repo), "session_id": "unchanged"})
        self.assertEqual(unchanged["action"], "allow")
        self.assertEqual(validate.call_count, 1)

        docsctl.snapshot(self.repo, "tests-only")
        (self.repo / "tests" / "test_app.py").write_text("def test_value():\n    assert 1 == 1\n", encoding="utf-8")
        with patch.object(docsctl, "validate", wraps=docsctl.validate) as validate:
            tests_only = hook_common.evaluate_stop({"cwd": str(self.repo), "session_id": "tests-only"})
        self.assertEqual(tests_only["action"], "allow")
        self.assertEqual(validate.call_count, 1)

    def test_missing_or_corrupt_stop_snapshot_is_explicitly_indeterminate_without_rebaseline(self) -> None:
        self.make_v2()
        (self.repo / "src" / "app.py").write_text("VALUE = 9\n", encoding="utf-8")
        missing_path = docsctl.session_path(self.repo, "missing-snapshot")

        missing = hook_common.evaluate_stop({"cwd": str(self.repo), "session_id": "missing-snapshot"})

        self.assertEqual((missing["action"], missing["kind"]), ("continue", "impact-indeterminate"))
        self.assertIn("missing or corrupt", missing["message"])
        self.assertFalse(missing_path.exists(), "Stop must not baseline the already-modified tree")

        corrupt_path = docsctl.session_path(self.repo, "corrupt-snapshot")
        corrupt_path.write_text("{not-json", encoding="utf-8")
        corrupt_before = corrupt_path.read_bytes()
        corrupt = hook_common.evaluate_stop({"cwd": str(self.repo), "session_id": "corrupt-snapshot"})
        self.assertEqual((corrupt["action"], corrupt["kind"]), ("continue", "impact-indeterminate"))
        self.assertEqual(corrupt_path.read_bytes(), corrupt_before, "Stop must not replace a corrupt baseline")

        payload = json.dumps({"hook_event_name": "Stop", "cwd": str(self.repo), "session_id": "host-missing"})
        codex = subprocess.run(
            [sys.executable, str(HOOK_CODEX)], input=payload, text=True, capture_output=True, check=True
        )
        codex_output = json.loads(codex.stdout)
        self.assertEqual(codex_output["decision"], "block")
        self.assertIn("missing or corrupt", codex_output["reason"])

        claude = subprocess.run(
            [sys.executable, str(HOOK_CLAUDE)], input=payload, text=True, capture_output=True, check=True
        )
        claude_output = json.loads(claude.stdout)["hookSpecificOutput"]
        self.assertEqual(claude_output["hookEventName"], "Stop")
        self.assertIn("missing or corrupt", claude_output["additionalContext"])

    def test_required_paths_that_become_directories_or_unreadable_emit_valid_stop_feedback(self) -> None:
        self.make_v2()
        required_paths = [docsctl.MODEL_FILE, "AGENTS.md", "CLAUDE.md", "MEMORY.md", "docs/README.md"]
        for rel in required_paths:
            with self.subTest(path=rel):
                path = self.repo / rel
                original = path.read_bytes()
                path.unlink()
                path.mkdir()
                validation = docsctl.validate(self.repo)
                self.assertFalse(validation["ok"], validation)
                self.assertIn(f"{rel} must be a readable regular file.", validation["failures"])
                path.rmdir()
                path.write_bytes(original)

        memory = self.repo / "MEMORY.md"
        original_read_text = Path.read_text

        def unreadable_memory(path: Path, *args: object, **kwargs: object) -> str:
            if path == memory:
                raise PermissionError("injected unreadable file")
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", new=unreadable_memory):
            unreadable = docsctl.validate(self.repo)
        self.assertIn("MEMORY.md must be a readable regular file.", unreadable["failures"])

        docsctl.snapshot(self.repo, "agents-directory")
        agents = self.repo / "AGENTS.md"
        agents.unlink()
        agents.mkdir()
        validation = docsctl.validate(self.repo)
        self.assertIn("AGENTS.md must be a readable regular file.", validation["failures"])

        payload = json.dumps({"hook_event_name": "Stop", "cwd": str(self.repo), "session_id": "agents-directory"})
        codex = subprocess.run([sys.executable, str(HOOK_CODEX)], input=payload, text=True, capture_output=True)
        self.assertEqual(codex.returncode, 0, codex.stderr)
        codex_output = json.loads(codex.stdout)
        self.assertEqual(codex_output["decision"], "block")
        self.assertIn("AGENTS.md must be a readable regular file.", codex_output["reason"])

        claude = subprocess.run([sys.executable, str(HOOK_CLAUDE)], input=payload, text=True, capture_output=True)
        self.assertEqual(claude.returncode, 0, claude.stderr)
        claude_output = json.loads(claude.stdout)["hookSpecificOutput"]
        self.assertEqual(claude_output["hookEventName"], "Stop")
        self.assertIn("AGENTS.md must be a readable regular file.", claude_output["additionalContext"])

    def test_architect_instructions_derive_provider_scope(self) -> None:
        skill = (ROOT / "skills" / "codebase-documentation-architect" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("standalone `CLAUDE.md` means `claude`", skill)
        self.assertIn("standalone `AGENTS.md` means `codex`", skill)
        self.assertIn("--agents <provider-scope>", skill)

    def test_v1_migrated_to_v2_during_same_session_uses_legacy_baseline(self) -> None:
        self.make_v1(claude=True)
        hook_common.begin({"cwd": str(self.repo), "session_id": "legacy-transition"})
        snap, _ = docsctl.load_snapshot(self.repo, "legacy-transition")
        self.assertIsNotNone(snap, "legacy repositories need a silent baseline for same-session migration")

        result = docsctl.apply_migration(self.repo, docsctl.migration_plan(self.repo, ["codex", "claude"]))
        self.assertTrue(result["ok"], result)
        stop = hook_common.evaluate_stop({"cwd": str(self.repo), "session_id": "legacy-transition", "stop_hook_active": False})

        self.assertNotEqual(stop.get("kind"), "impact-indeterminate", stop)
        self.assertEqual(stop["action"], "allow", stop)

    def test_v1_migration_preserves_same_session_source_impact_and_only_continues_once(self) -> None:
        self.make_v1(claude=True)
        (self.repo / "src").mkdir()
        (self.repo / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        commit_all(self.repo, "add source")
        hook_common.begin({"cwd": str(self.repo), "session_id": "legacy-source-transition"})
        (self.repo / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        result = docsctl.apply_migration(self.repo, docsctl.migration_plan(self.repo, ["codex", "claude"]))
        self.assertTrue(result["ok"], result)

        first = hook_common.evaluate_stop({"cwd": str(self.repo), "session_id": "legacy-source-transition", "stop_hook_active": False})
        second = hook_common.evaluate_stop({"cwd": str(self.repo), "session_id": "legacy-source-transition", "stop_hook_active": True})

        self.assertEqual((first["action"], first["kind"]), ("continue", "review"), first)
        self.assertIn("src/app.py", first["message"])
        self.assertEqual(second["action"], "allow", second)

    def test_indeterminate_stop_does_not_repeat_after_hook_continuation(self) -> None:
        self.make_v2()
        first = hook_common.evaluate_stop({"cwd": str(self.repo), "session_id": "missing-baseline", "stop_hook_active": False})
        second = hook_common.evaluate_stop({"cwd": str(self.repo), "session_id": "missing-baseline", "stop_hook_active": True})

        self.assertEqual((first["action"], first["kind"]), ("continue", "impact-indeterminate"))
        self.assertEqual(second["action"], "allow", second)

    def test_generated_and_language_specific_tests_do_not_trigger_maintenance(self) -> None:
        self.make_v2()
        generated = [
            ".next/cache/data.bin",
            "coverage/coverage.json",
            "out/app.js",
            "target/release/app",
            "package-lock.json",
            "pnpm-lock.yaml",
            "go.sum",
        ]
        tests = ["pkg/handler_test.go", "spec/unit/widget_spec.rb", "Project.Tests/Widget.cs"]
        for rel in generated + tests:
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("baseline\n", encoding="utf-8")
        commit_all(self.repo, "classification fixtures")
        docsctl.snapshot(self.repo, "classify-expanded")
        for rel in generated + tests:
            path = self.repo / rel
            path.write_text("changed\n", encoding="utf-8")

        report = docsctl.impact_report(self.repo, docsctl.load_snapshot(self.repo, "classify-expanded")[0])

        self.assertFalse(report["needs_documentation_review"], report)
        self.assertEqual(report["categories"].get("generated"), len(generated), report)
        self.assertEqual(report["categories"].get("tests"), len(tests), report)

    def test_modifying_existing_documentation_is_not_misclassified_as_structure_change(self) -> None:
        self.make_v2()
        docsctl.snapshot(self.repo, "docs-edit")
        docs_index = self.repo / "docs" / "README.md"
        docs_index.write_text(docs_index.read_text(encoding="utf-8") + "\nEditorial clarification.\n", encoding="utf-8")

        report = docsctl.impact_report(self.repo, docsctl.load_snapshot(self.repo, "docs-edit")[0])

        self.assertEqual(report["categories"], {"docs": 1}, report)
        self.assertFalse(report["needs_documentation_review"], report)

    def test_architect_finish_keeps_derived_provider_scope(self) -> None:
        skill = (ROOT / "skills" / "codebase-documentation-architect" / "SKILL.md").read_text(encoding="utf-8")
        finish = skill.split("## Finish", 1)[1]
        self.assertIn("--agents <provider-scope>", finish)
        self.assertNotIn("--agents both", finish)
        self.assertIn("if scaffold already created it", finish.lower())


if __name__ == "__main__":
    unittest.main()
