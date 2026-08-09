from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCSCTL_PATH = ROOT / "runtime" / "docsctl.py"
HOOK_CODEX = ROOT / "runtime" / "hook_codex.py"
HOOK_CLAUDE = ROOT / "runtime" / "hook_claude.py"
INSTALLER = ROOT / "install.py"

spec = importlib.util.spec_from_file_location("docsctl_tested", DOCSCTL_PATH)
docsctl = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = docsctl
spec.loader.exec_module(docsctl)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=True)


def init_git(repo: Path) -> None:
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")


def commit_all(repo: Path, message: str = "initial") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)


def legacy_agent() -> str:
    return """# Agent Guide

## Repository Rules

- Run tests before commit.

## Documentation and Memory

- Start with [docs/README.md](docs/README.md) for the project documentation map.
- Check [MEMORY.md](MEMORY.md) for current priorities, recent deltas, and active risks.
- Check [docs/state/README.md](docs/state/README.md) for durable project context, decisions, assumptions, and known issues.
- At the end of any task that changes behavior, documentation, structure, or durable project knowledge, invoke `$codebase-documentation-architect` to decide whether memory or docs need updates.
"""


class TempRepoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        init_git(self.repo)
        self.old_cache = os.environ.get("XDG_CACHE_HOME")
        self.cache = Path(self.tmp.name) / "cache"
        os.environ["XDG_CACHE_HOME"] = str(self.cache)

    def tearDown(self) -> None:
        if self.old_cache is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = self.old_cache

    def make_v1(self) -> None:
        (self.repo / "AGENTS.md").write_text(legacy_agent(), encoding="utf-8")
        (self.repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (self.repo / "MEMORY.md").write_text("# Repository Memory\n\n## Current Priorities\n\n- Stable.\n", encoding="utf-8")
        (self.repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.repo / "docs" / "state").mkdir(parents=True)
        (self.repo / "docs" / "README.md").write_text("# Documentation\n\n- [Memory](../MEMORY.md)\n", encoding="utf-8")
        (self.repo / "docs" / "state" / "README.md").write_text("# Durable State\n", encoding="utf-8")
        commit_all(self.repo)

    def make_v2(self) -> None:
        result = docsctl.scaffold(self.repo, ["codex", "claude"])
        self.assertTrue(result["validation"]["ok"], result)
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.repo / "tests").mkdir(exist_ok=True)
        (self.repo / "tests" / "test_app.py").write_text("def test_value():\n    assert True\n", encoding="utf-8")
        commit_all(self.repo)

    def test_detect_and_migrate_known_v1(self) -> None:
        self.make_v1()
        status = docsctl.detect_status(self.repo)
        self.assertEqual(status["state"], "v1-legacy")

        plan = docsctl.migration_plan(self.repo, ["codex", "claude"])
        self.assertFalse(plan["semantic_review_required"], plan)
        result = docsctl.apply_migration(self.repo, plan)
        self.assertTrue(result["ok"], result)

        agents = (self.repo / "AGENTS.md").read_text(encoding="utf-8")
        self.assertNotIn("$codebase-documentation-architect", agents)
        self.assertNotIn("docs/state/README.md", agents)
        self.assertIn("Run tests before commit.", agents)
        self.assertEqual((self.repo / "CLAUDE.md").read_text(encoding="utf-8"), "@AGENTS.md\n")
        self.assertEqual(docsctl.detect_status(self.repo)["state"], "v2")

    def test_migration_rollback_restores_and_removes_created_marker(self) -> None:
        self.make_v1()
        before_agents = (self.repo / "AGENTS.md").read_text(encoding="utf-8")
        plan = docsctl.migration_plan(self.repo, ["codex", "claude"])
        result = docsctl.apply_migration(self.repo, plan)
        self.assertTrue(result["applied"])
        self.assertTrue((self.repo / docsctl.MODEL_FILE).exists())

        rollback = docsctl.rollback_migration(self.repo, Path(result["backup"]))
        self.assertTrue(rollback["ok"], rollback)
        self.assertEqual((self.repo / "AGENTS.md").read_text(encoding="utf-8"), before_agents)
        self.assertFalse((self.repo / docsctl.MODEL_FILE).exists())

    def test_standalone_claude_requires_review_when_enabling_codex(self) -> None:
        (self.repo / "CLAUDE.md").write_text(legacy_agent() + "\n## Claude Only\n- Use plan mode.\n", encoding="utf-8")
        (self.repo / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
        (self.repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.repo / "docs" / "state").mkdir(parents=True)
        (self.repo / "docs" / "README.md").write_text("# Docs\n", encoding="utf-8")
        (self.repo / "docs" / "state" / "README.md").write_text("# State\n", encoding="utf-8")
        plan = docsctl.migration_plan(self.repo, ["codex", "claude"])
        self.assertTrue(plan["semantic_review_required"])
        result = docsctl.apply_migration(self.repo, plan)
        self.assertFalse(result["applied"])

    def test_standalone_claude_can_migrate_for_claude_only(self) -> None:
        (self.repo / "CLAUDE.md").write_text(legacy_agent(), encoding="utf-8")
        (self.repo / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
        (self.repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.repo / "docs" / "state").mkdir(parents=True)
        (self.repo / "docs" / "README.md").write_text("# Docs\n", encoding="utf-8")
        (self.repo / "docs" / "state" / "README.md").write_text("# State\n", encoding="utf-8")
        plan = docsctl.migration_plan(self.repo, ["claude"])
        self.assertFalse(plan["semantic_review_required"], plan)
        result = docsctl.apply_migration(self.repo, plan)
        self.assertTrue(result["ok"], result)
        text = (self.repo / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertNotIn("$codebase-documentation-architect", text)

    def test_legacy_procedure_docs_prevent_auto_migration(self) -> None:
        self.make_v1()
        path = self.repo / "docs" / "documentation-maintenance.md"
        path.write_text("# Old procedure\nProject-specific fact may be here.\n", encoding="utf-8")
        plan = docsctl.migration_plan(self.repo, ["codex", "claude"])
        self.assertTrue(plan["semantic_review_required"])

    def test_docs_state_is_optional_in_v2(self) -> None:
        result = docsctl.scaffold(self.repo, ["codex", "claude"])
        self.assertTrue(result["validation"]["ok"], result)
        self.assertFalse((self.repo / "docs" / "state").exists())
        self.assertTrue(docsctl.validate(self.repo)["ok"])

    def test_session_impact_source_change_needs_review(self) -> None:
        self.make_v2()
        docsctl.snapshot(self.repo, "s1")
        (self.repo / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        report = docsctl.impact_report(self.repo, docsctl.load_snapshot(self.repo, "s1")[0])
        self.assertTrue(report["needs_documentation_review"], report)
        self.assertEqual(report["categories"].get("source"), 1)

    def test_session_impact_test_only_skips_review(self) -> None:
        self.make_v2()
        docsctl.snapshot(self.repo, "s2")
        (self.repo / "tests" / "test_app.py").write_text("def test_value():\n    assert 1 == 1\n", encoding="utf-8")
        report = docsctl.impact_report(self.repo, docsctl.load_snapshot(self.repo, "s2")[0])
        self.assertFalse(report["needs_documentation_review"], report)

    def test_preexisting_dirty_file_further_edit_is_detected(self) -> None:
        self.make_v2()
        (self.repo / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        docsctl.snapshot(self.repo, "s3")
        (self.repo / "src" / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
        report = docsctl.impact_report(self.repo, docsctl.load_snapshot(self.repo, "s3")[0])
        self.assertEqual(report["changed_count"], 1, report)
        self.assertEqual(report["changed"][0]["reason"], "modified-preexisting-dirty")

    def test_preexisting_validation_failure_is_not_new_blocker(self) -> None:
        self.make_v2()
        # Introduce failure before the baseline.
        (self.repo / "docs" / "README.md").write_text("# Docs\n\n[Broken](missing.md)\n", encoding="utf-8")
        docsctl.snapshot(self.repo, "s4")
        (self.repo / "tests" / "test_app.py").write_text("def test_value():\n    assert True\n# changed\n", encoding="utf-8")
        snap, _ = docsctl.load_snapshot(self.repo, "s4")
        current = docsctl.validate(self.repo)["failures"]
        self.assertTrue(current)
        self.assertEqual(set(current) - set(snap["validation_failures"]), set())

    def test_codex_and_claude_stop_hook_shapes(self) -> None:
        self.make_v2()
        env = os.environ.copy()
        env["XDG_CACHE_HOME"] = str(self.cache)
        start = {"hook_event_name": "SessionStart", "source": "startup", "session_id": "hook1", "cwd": str(self.repo)}
        subprocess.run([sys.executable, str(HOOK_CODEX)], input=json.dumps(start), text=True, capture_output=True, env=env, check=True)
        (self.repo / "src" / "app.py").write_text("VALUE = 99\n", encoding="utf-8")

        stop = {"hook_event_name": "Stop", "session_id": "hook1", "cwd": str(self.repo), "stop_hook_active": False}
        cp = subprocess.run([sys.executable, str(HOOK_CODEX)], input=json.dumps(stop), text=True, capture_output=True, env=env, check=True)
        codex_obj = json.loads(cp.stdout)
        self.assertEqual(codex_obj.get("decision"), "block", cp.stdout)
        self.assertIn("codebase-documentation-maintainer", codex_obj.get("reason", ""))

        # Create a separate Claude baseline for the same changed state, then change again.
        start["session_id"] = "hook2"
        subprocess.run([sys.executable, str(HOOK_CLAUDE)], input=json.dumps(start), text=True, capture_output=True, env=env, check=True)
        (self.repo / "src" / "app.py").write_text("VALUE = 100\n", encoding="utf-8")
        stop["session_id"] = "hook2"
        cp = subprocess.run([sys.executable, str(HOOK_CLAUDE)], input=json.dumps(stop), text=True, capture_output=True, env=env, check=True)
        claude_obj = json.loads(cp.stdout)
        out = claude_obj["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "Stop")
        self.assertIn("codebase-documentation-maintainer", out["additionalContext"])

    def test_project_install_shares_one_runtime_and_uninstall_one_target_keeps_it(self) -> None:
        cp = subprocess.run(
            [sys.executable, str(INSTALLER), "--target", "both", "--scope", "project", "--repo", str(self.repo)],
            text=True, capture_output=True,
        )
        self.assertEqual(cp.returncode, 0, cp.stderr)
        runtime = self.repo / ".codebase-documentation-kit" / "runtime"
        self.assertTrue((runtime / "hook_codex.py").exists())
        self.assertTrue((runtime / "hook_claude.py").exists())
        codex_cfg = (self.repo / ".codex" / "hooks.json").read_text(encoding="utf-8")
        claude_cfg = (self.repo / ".claude" / "settings.json").read_text(encoding="utf-8")
        self.assertIn(".codebase-documentation-kit/runtime/hook_codex.py", codex_cfg)
        self.assertIn(".codebase-documentation-kit/runtime/hook_claude.py", claude_cfg)

        cp = subprocess.run(
            [sys.executable, str(INSTALLER), "--target", "codex", "--scope", "project", "--repo", str(self.repo), "--uninstall"],
            text=True, capture_output=True,
        )
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertTrue(runtime.exists(), "Claude still depends on the shared runtime")

        cp = subprocess.run(
            [sys.executable, str(INSTALLER), "--target", "claude", "--scope", "project", "--repo", str(self.repo), "--uninstall"],
            text=True, capture_output=True,
        )
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertFalse((self.repo / ".codebase-documentation-kit").exists())

    def test_skill_launcher_resolves_package_runtime(self) -> None:
        launcher = ROOT / "skills" / "codebase-documentation-maintainer" / "scripts" / "docsctl.py"
        cp = subprocess.run([sys.executable, str(launcher), "--version"], text=True, capture_output=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(cp.stdout.strip(), docsctl.VERSION)

    def test_installer_preserves_unrelated_hooks(self) -> None:
        fake_home = Path(self.tmp.name) / "home"
        codex_dir = fake_home / ".codex"
        claude_dir = fake_home / ".claude"
        codex_dir.mkdir(parents=True)
        claude_dir.mkdir(parents=True)
        unrelated = {"type": "command", "command": "echo unrelated"}
        (codex_dir / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [unrelated]}]}}), encoding="utf-8")
        (claude_dir / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [unrelated]}]}}), encoding="utf-8")
        env = os.environ.copy()
        env["HOME"] = str(fake_home)
        env["USERPROFILE"] = str(fake_home)
        cp = subprocess.run([sys.executable, str(INSTALLER), "--target", "both", "--scope", "user"], text=True, capture_output=True, env=env)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        for config in [codex_dir / "hooks.json", claude_dir / "settings.json"]:
            obj = json.loads(config.read_text(encoding="utf-8"))
            serialized = json.dumps(obj)
            self.assertIn("echo unrelated", serialized)
            self.assertIn("codebase-documentation-kit", serialized)


if __name__ == "__main__":
    unittest.main()
