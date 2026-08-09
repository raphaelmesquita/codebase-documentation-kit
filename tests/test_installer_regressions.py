from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.py"
OWNER_FILE = ".codebase-documentation-kit-owner.json"


def load_installer():
    spec = importlib.util.spec_from_file_location("installer_under_test", INSTALLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def tree_snapshot(root: Path) -> dict[str, bytes | None]:
    snapshot: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            snapshot[relative + "/"] = None
        else:
            snapshot[relative] = path.read_bytes()
    return snapshot


class InstallerRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_installer(self, *args: str, home: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if home is not None:
            env["HOME"] = str(home)
            env["USERPROFILE"] = str(home)
        return subprocess.run(
            [sys.executable, str(INSTALLER), *args],
            text=True,
            capture_output=True,
            env=env,
            cwd=ROOT,
        )

    def test_c05_a04_incidental_markers_and_settings_survive_lifecycle(self) -> None:
        home = self.root / "home with spaces"
        codex = home / ".codex" / "hooks.json"
        claude = home / ".claude" / "settings.json"
        foreign = [
            {"type": "command", "command": "python tools/codebase-documentation-kit-audit.py"},
            {"type": "command", "command": "python tools/hook_codex.py --note keep"},
            {"type": "command", "command": "python tools/hook_claude.py --note keep"},
        ]
        for path in (codex, claude):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"permissions": {"allow": ["Read"]}, "hooks": {"Stop": [{"hooks": foreign}]}}),
                encoding="utf-8",
            )

        for args in (
            ("--target", "both", "--scope", "user"),
            ("--target", "both", "--scope", "user"),
            ("--target", "both", "--scope", "user", "--uninstall"),
        ):
            result = self.run_installer(*args, home=home)
            self.assertEqual(result.returncode, 0, result.stderr)
            for path in (codex, claude):
                obj = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(obj["permissions"], {"allow": ["Read"]})
                self.assertEqual(obj["hooks"]["Stop"][0]["hooks"], foreign)

        self.assertFalse((home / ".codebase-documentation-kit").exists())

    def test_user_hook_ownership_survives_python_interpreter_change(self) -> None:
        home = self.root / "python switch home with spaces"
        home.mkdir()
        python_a = self.root / "Python A" / "python.exe"
        installer = load_installer()
        args = ["--target", "codex", "--scope", "user"]
        environment = {"HOME": str(home), "USERPROFILE": str(home)}

        with mock.patch.dict(os.environ, environment), mock.patch.object(installer.sys, "executable", str(python_a)):
            self.assertEqual(installer.main(args), 0)

        config_path = home / ".codex" / "hooks.json"
        first = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertTrue(
            any(
                str(python_a) in handler.get("command", "")
                for groups in first["hooks"].values()
                for group in groups
                for handler in group.get("hooks", [])
                if isinstance(handler, dict)
            )
        )

        result = self.run_installer(*args, home=home)
        self.assertEqual(result.returncode, 0, result.stderr)
        updated = json.loads(config_path.read_text(encoding="utf-8"))
        script = str(home / ".codebase-documentation-kit" / "runtime" / "hook_codex.py")
        for event in ("SessionStart", "Stop"):
            commands = [
                handler["command"]
                for group in updated["hooks"][event]
                for handler in group.get("hooks", [])
                if isinstance(handler, dict) and script in handler.get("command", "")
            ]
            self.assertEqual(len(commands), 1, commands)
            self.assertIn(sys.executable, commands[0])
            self.assertNotIn(str(python_a), commands[0])
        self.assertTrue((home / ".codebase-documentation-kit" / "runtime" / OWNER_FILE).is_file())
        for skill in ("codebase-documentation-architect", "codebase-documentation-maintainer"):
            self.assertTrue((home / ".agents" / "skills" / skill / OWNER_FILE).is_file())

        # Recreate an A-owned hook state, then prove a B uninstall recognizes it.
        with mock.patch.dict(os.environ, environment), mock.patch.object(installer.sys, "executable", str(python_a)):
            self.assertEqual(installer.main(args), 0)
        result = self.run_installer(*args, "--uninstall", home=home)
        self.assertEqual(result.returncode, 0, result.stderr)
        final_config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertNotIn("hook_codex.py", json.dumps(final_config))
        self.assertFalse((home / ".codebase-documentation-kit").exists())
        for skill in ("codebase-documentation-architect", "codebase-documentation-maintainer"):
            self.assertFalse((home / ".agents" / "skills" / skill).exists())

    def test_p03_foreign_or_modified_skill_tree_is_never_replaced_or_removed(self) -> None:
        repo = self.root / "foreign skill repo"
        repo.mkdir()
        foreign = repo / ".agents" / "skills" / "codebase-documentation-architect"
        foreign.mkdir(parents=True)
        foreign_file = foreign / "foreign-user-file.txt"
        foreign_file.write_text("keep", encoding="utf-8")

        result = self.run_installer("--target", "codex", "--scope", "project", "--repo", str(repo))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Ownership conflict", result.stderr)
        self.assertEqual(foreign_file.read_text(encoding="utf-8"), "keep")
        self.assertFalse((repo / ".codebase-documentation-kit").exists())
        self.assertFalse((repo / ".codex" / "hooks.json").exists())

        clean = self.root / "owned skill repo"
        clean.mkdir()
        result = self.run_installer("--target", "codex", "--scope", "project", "--repo", str(clean))
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = clean / ".agents" / "skills" / "codebase-documentation-architect"
        self.assertTrue((installed / OWNER_FILE).is_file())
        skill_file = installed / "SKILL.md"
        skill_file.write_text(skill_file.read_text(encoding="utf-8") + "\nlocal change\n", encoding="utf-8")

        for extra in ((), ("--uninstall",)):
            result = self.run_installer("--target", "codex", "--scope", "project", "--repo", str(clean), *extra)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Ownership conflict", result.stderr)
            self.assertIn("local change", skill_file.read_text(encoding="utf-8"))

    def test_p04_dry_run_with_spaces_has_no_writes(self) -> None:
        repo = self.root / "dry run repo [x] & spaces"
        repo.mkdir()
        result = self.run_installer("--target", "both", "--scope", "project", "--repo", str(repo), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY RUN", result.stdout)
        self.assertEqual(list(repo.iterdir()), [])

    def test_p05_destination_and_config_preflight_leave_both_targets_untouched(self) -> None:
        for name, make_conflict in (
            ("skill conflict", lambda repo: (repo / ".claude" / "skills" / "codebase-documentation-architect").parent.mkdir(parents=True)),
            ("config conflict", lambda repo: (repo / ".claude" / "settings.json").mkdir(parents=True)),
        ):
            with self.subTest(name=name):
                repo = self.root / name
                repo.mkdir()
                make_conflict(repo)
                if name == "skill conflict":
                    (repo / ".claude" / "skills" / "codebase-documentation-architect").write_text("foreign", encoding="utf-8")
                result = self.run_installer("--target", "both", "--scope", "project", "--repo", str(repo))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((repo / ".codebase-documentation-kit").exists())
                self.assertFalse((repo / ".codex" / "hooks.json").exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_p1_project_codex_junction_cannot_escape_scope(self) -> None:
        repo = self.root / "junction repo with spaces"
        outside = self.root / "outside hooks directory"
        repo.mkdir()
        outside.mkdir()
        junction = repo / ".codex"
        created = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
            text=True,
            capture_output=True,
        )
        if created.returncode != 0:
            self.skipTest(f"could not create test junction: {created.stderr or created.stdout}")

        result = self.run_installer("--target", "codex", "--scope", "project", "--repo", str(repo))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue("escapes" in result.stderr or "unsafe destination component" in result.stderr, result.stderr)
        self.assertFalse((outside / "hooks.json").exists())
        self.assertFalse((repo / ".codebase-documentation-kit").exists())
        self.assertFalse((repo / ".agents").exists())

    def test_p06_injected_destination_and_config_commit_failures_restore_exact_state(self) -> None:
        for name, destination_name in (
            ("destination", "codebase-documentation-architect"),
            ("config", "settings.json"),
        ):
            with self.subTest(name=name):
                repo = self.root / f"transaction {name} repo"
                repo.mkdir()
                installer = load_installer()
                args = ["--target", "both", "--scope", "project", "--repo", str(repo)]
                self.assertEqual(installer.main(args), 0)
                if destination_name == "settings.json":
                    settings = repo / ".claude" / "settings.json"
                    obj = json.loads(settings.read_text(encoding="utf-8"))
                    obj["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "echo preserved"}]})
                    settings.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
                before = tree_snapshot(repo)
                target = (
                    repo / ".claude" / "skills" / destination_name
                    if destination_name == "codebase-documentation-architect"
                    else repo / ".claude" / destination_name
                )
                original_replace = installer.os.replace
                failed = False

                def fail_once(source, destination):
                    nonlocal failed
                    if not failed and Path(destination) == target:
                        failed = True
                        raise OSError("injected commit failure")
                    return original_replace(source, destination)

                with mock.patch.object(installer.os, "replace", side_effect=fail_once):
                    self.assertEqual(installer.main(args), 1)
                self.assertTrue(failed)
                self.assertEqual(tree_snapshot(repo), before)

    def test_keyboard_interrupt_during_reinstall_restores_previous_runtime(self) -> None:
        repo = self.root / "interrupted reinstall repo"
        repo.mkdir()
        installer = load_installer()
        args = ["--target", "codex", "--scope", "project", "--repo", str(repo)]
        self.assertEqual(installer.main(args), 0)
        before = tree_snapshot(repo)
        actions, transaction = installer.plan("project", repo, ["codex"], False, False)
        self.assertTrue(actions)
        self.assertIsNotNone(transaction)
        assert transaction is not None
        original_replace = installer.os.replace
        replace_calls = 0

        def interrupt_second_replace(source, destination):
            nonlocal replace_calls
            replace_calls += 1
            if replace_calls == 2:
                raise KeyboardInterrupt("injected installer interruption")
            return original_replace(source, destination)

        try:
            with mock.patch.object(installer.os, "replace", side_effect=interrupt_second_replace):
                with self.assertRaises(KeyboardInterrupt):
                    transaction.commit()
        finally:
            transaction.close()

        self.assertFalse(transaction.recovery_needed)
        self.assertEqual(tree_snapshot(repo), before)

    def test_p07_owned_uninstall_removes_only_last_target_runtime(self) -> None:
        repo = self.root / "runtime isolation repo"
        repo.mkdir()
        self.assertEqual(self.run_installer("--target", "both", "--scope", "project", "--repo", str(repo)).returncode, 0)
        runtime = repo / ".codebase-documentation-kit" / "runtime"
        self.assertTrue((runtime / OWNER_FILE).is_file())

        result = self.run_installer("--target", "codex", "--scope", "project", "--repo", str(repo), "--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(runtime.is_dir())
        self.assertTrue((repo / ".claude" / "settings.json").is_file())

        result = self.run_installer("--target", "claude", "--scope", "project", "--repo", str(repo), "--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((repo / ".codebase-documentation-kit").exists())


if __name__ == "__main__":
    unittest.main()
