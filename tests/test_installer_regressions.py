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
            self.assertTrue((home / ".codex" / "skills" / skill / OWNER_FILE).is_file())

        # Recreate an A-owned hook state, then prove a B uninstall recognizes it.
        with mock.patch.dict(os.environ, environment), mock.patch.object(installer.sys, "executable", str(python_a)):
            self.assertEqual(installer.main(args), 0)
        result = self.run_installer(*args, "--uninstall", home=home)
        self.assertEqual(result.returncode, 0, result.stderr)
        final_config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertNotIn("hook_codex.py", json.dumps(final_config))
        self.assertFalse((home / ".codebase-documentation-kit").exists())
        for skill in ("codebase-documentation-architect", "codebase-documentation-maintainer"):
            self.assertFalse((home / ".codex" / "skills" / skill).exists())

    def test_p03_foreign_or_modified_skill_tree_is_never_replaced_or_removed(self) -> None:
        repo = self.root / "foreign skill repo"
        repo.mkdir()
        foreign = repo / ".codex" / "skills" / "codebase-documentation-architect"
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
        installed = clean / ".codex" / "skills" / "codebase-documentation-architect"
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

    def test_codex_skills_install_only_under_dotcodex_without_touching_config_toml(self) -> None:
        home = self.root / "codex home"
        config = home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        original = 'model = "gpt-5.6"\n\n[features]\nunified_exec = true\n'
        config.write_text(original, encoding="utf-8")

        result = self.run_installer("--target", "codex", "--scope", "user", home=home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((home / ".agents").exists())
        self.assertEqual(config.read_text(encoding="utf-8"), original)
        for skill in ("codebase-documentation-architect", "codebase-documentation-maintainer"):
            self.assertTrue((home / ".codex" / "skills" / skill / "SKILL.md").is_file())

        result = self.run_installer("--target", "codex", "--scope", "user", home=home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config.read_text(encoding="utf-8"), original)

        result = self.run_installer("--target", "codex", "--scope", "user", "--uninstall", home=home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config.read_text(encoding="utf-8"), original)
        self.assertFalse((home / ".codex" / "skills" / "codebase-documentation-architect").exists())
        self.assertFalse((home / ".agents").exists())

    @unittest.skipUnless(os.name == "nt", "PowerShell command execution is Windows-specific")
    def test_user_codex_windows_override_executes_in_powershell(self) -> None:
        home = self.root / "codex user home with spaces"
        home.mkdir()
        result = self.run_installer("--target", "codex", "--scope", "user", home=home)
        self.assertEqual(result.returncode, 0, result.stderr)

        config = json.loads((home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        for event in ("SessionStart", "Stop"):
            handler = config["hooks"][event][0]["hooks"][0]
            self.assertIn("commandWindows", handler)
            payload = json.dumps({
                "hook_event_name": event,
                "source": "startup",
                "session_id": f"powershell-{event.lower()}",
                "cwd": str(home),
                "stop_hook_active": False,
            })
            cp = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", handler["commandWindows"]],
                input=payload,
                text=True,
                capture_output=True,
                cwd=home,
            )
            self.assertEqual(
                cp.returncode,
                0,
                f"{event}: {cp.stderr}\ncommandWindows={handler['commandWindows']}",
            )

    def test_project_codex_uses_dotcodex_skills_and_no_agents_directory(self) -> None:
        repo = self.root / "project codex layout"
        repo.mkdir()
        result = self.run_installer("--target", "codex", "--scope", "project", "--repo", str(repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((repo / ".agents").exists())
        self.assertFalse((repo / ".codex" / "config.toml").exists())
        for skill in ("codebase-documentation-architect", "codebase-documentation-maintainer"):
            self.assertTrue((repo / ".codex" / "skills" / skill / "SKILL.md").is_file())

    def test_codex_install_removes_known_v1_skill_from_agents_and_preserves_foreign_content(self) -> None:
        home = self.root / "legacy v1 home"
        legacy = home / ".agents" / "skills" / "codebase-documentation-architect"
        (legacy / "references").mkdir(parents=True)
        (legacy / "scripts").mkdir(parents=True)
        (legacy / "SKILL.md").write_text(
            "---\nname: codebase-documentation-architect\n---\n# Codebase Documentation Architect\n"
            "Read references/bootstrap-checklist.md before completion maintenance.\n",
            encoding="utf-8",
        )
        (legacy / "references" / "bootstrap-checklist.md").write_text(
            "# Bootstrap Checklist\nUse completion maintenance when appropriate.\n", encoding="utf-8"
        )
        (legacy / "scripts" / "validate_docs_model.py").write_text(
            "\"\"\"Validate the documentation model produced by codebase-documentation-architect.\"\"\"\n", encoding="utf-8"
        )
        foreign = home / ".agents" / "skills" / "other-skill" / "SKILL.md"
        foreign.parent.mkdir(parents=True)
        foreign.write_text("# Foreign\n", encoding="utf-8")

        result = self.run_installer("--target", "codex", "--scope", "user", home=home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(legacy.exists())
        self.assertEqual(foreign.read_text(encoding="utf-8"), "# Foreign\n")
        self.assertTrue((home / ".codex" / "skills" / "codebase-documentation-architect" / "SKILL.md").is_file())

    def test_codex_install_removes_locally_modified_v1_architect_by_reserved_skill_identity(self) -> None:
        home = self.root / "modified legacy architect home"
        legacy = home / ".agents" / "skills" / "codebase-documentation-architect"
        legacy.mkdir(parents=True)
        (legacy / "SKILL.md").write_text(
            "---\nname: codebase-documentation-architect\ndescription: locally customized old skill\n---\n"
            "# My modified legacy architect\nThis copy no longer matches the original package fingerprints.\n",
            encoding="utf-8",
        )
        (legacy / "local-notes.md").write_text("old local customization\n", encoding="utf-8")

        result = self.run_installer("--target", "codex", "--scope", "user", home=home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((home / ".agents").exists())
        self.assertTrue((home / ".codex" / "skills" / "codebase-documentation-architect" / "SKILL.md").is_file())

    def test_codex_install_removes_pre_manifest_maintainer_copy_from_agents(self) -> None:
        home = self.root / "manual old maintainer home"
        legacy = home / ".agents" / "skills" / "codebase-documentation-maintainer"
        legacy.mkdir(parents=True)
        (legacy / "SKILL.md").write_text(
            "---\nname: codebase-documentation-maintainer\ndescription: manually copied older kit skill\n---\n"
            "# Old maintainer\n",
            encoding="utf-8",
        )

        result = self.run_installer("--target", "codex", "--scope", "user", home=home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((home / ".agents").exists())
        self.assertTrue((home / ".codex" / "skills" / "codebase-documentation-maintainer" / "SKILL.md").is_file())

    def test_project_codex_dry_run_plans_legacy_agents_removal_then_apply_prunes_it(self) -> None:
        repo = self.root / "legacy project repo"
        legacy = repo / ".agents" / "skills" / "codebase-documentation-architect"
        (legacy / "references").mkdir(parents=True)
        (legacy / "scripts").mkdir(parents=True)
        (legacy / "SKILL.md").write_text(
            "---\nname: codebase-documentation-architect\n---\n# Codebase Documentation Architect\n"
            "Read references/bootstrap-checklist.md before completion maintenance.\n", encoding="utf-8"
        )
        (legacy / "references" / "bootstrap-checklist.md").write_text(
            "# Bootstrap Checklist\nChoose completion maintenance for routine completion.\n", encoding="utf-8"
        )
        (legacy / "scripts" / "validate_docs_model.py").write_text(
            '"""Validate the documentation model produced by codebase-documentation-architect."""\n', encoding="utf-8"
        )

        dry = self.run_installer("--target", "codex", "--scope", "project", "--repo", str(repo), "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("remove legacy toolkit path", dry.stdout)
        self.assertTrue(legacy.exists())
        self.assertFalse((repo / ".codex" / "skills" / "codebase-documentation-architect").exists())

        result = self.run_installer("--target", "codex", "--scope", "project", "--repo", str(repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((repo / ".agents").exists())
        self.assertTrue((repo / ".codex" / "skills" / "codebase-documentation-architect" / "SKILL.md").is_file())

    def test_claude_only_install_does_not_touch_agents(self) -> None:
        home = self.root / "claude only home"
        foreign = home / ".agents" / "skills" / "codebase-documentation-architect" / "SKILL.md"
        foreign.parent.mkdir(parents=True)
        foreign.write_text("# Leave for Codex migration later\n", encoding="utf-8")

        result = self.run_installer("--target", "claude", "--scope", "user", home=home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(foreign.read_text(encoding="utf-8"), "# Leave for Codex migration later\n")
        self.assertTrue((home / ".claude" / "skills" / "codebase-documentation-architect" / "SKILL.md").is_file())

    def test_codex_install_removes_owned_v210_skills_from_agents(self) -> None:
        home = self.root / "owned old layout home"
        installer = load_installer()
        for skill in installer.SKILLS:
            source = ROOT / "skills" / skill
            legacy = home / ".agents" / "skills" / skill
            legacy.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copytree(source, legacy)
            manifest = installer.make_manifest("skill", skill, source)
            (legacy / OWNER_FILE).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        result = self.run_installer("--target", "codex", "--scope", "user", home=home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((home / ".agents").exists())
        for skill in installer.SKILLS:
            self.assertTrue((home / ".codex" / "skills" / skill / OWNER_FILE).is_file())

    def test_codex_install_blocks_ambiguous_same_name_agents_skill_instead_of_leaving_duplicate(self) -> None:
        home = self.root / "ambiguous old layout home"
        collision = home / ".agents" / "skills" / "codebase-documentation-architect"
        collision.mkdir(parents=True)
        (collision / "SKILL.md").write_text("# User customized unrelated content\n", encoding="utf-8")

        result = self.run_installer("--target", "codex", "--scope", "user", home=home)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Legacy Codex skill collision", result.stderr)
        self.assertTrue(collision.exists())
        self.assertFalse((home / ".codex" / "skills" / "codebase-documentation-architect").exists())

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

    def test_project_codex_installs_windows_override(self) -> None:
        repo = self.root / "codex windows override"
        repo.mkdir()
        result = self.run_installer("--target", "codex", "--scope", "project", "--repo", str(repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads((repo / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        handlers = [
            handler
            for event in ("SessionStart", "Stop")
            for group in config["hooks"][event]
            for handler in group["hooks"]
            if ".codebase-documentation-kit/runtime/hook_codex.py" in handler.get("command", "")
        ]
        self.assertEqual(len(handlers), 2, handlers)
        for handler in handlers:
            self.assertIn("commandWindows", handler)
            self.assertIn("hook_codex.py", handler["commandWindows"])
            self.assertIn("Path.cwd()", handler["commandWindows"])

    @unittest.skipIf(os.name == "nt", "POSIX project command execution is covered on non-Windows hosts")
    def test_project_hook_commands_execute_from_repository_subdirectory(self) -> None:
        repo = self.root / "project command smoke"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True)
        scaffold = subprocess.run(
            [sys.executable, str(ROOT / "runtime" / "docsctl.py"), "scaffold", str(repo), "--agents", "both", "--json"],
            text=True, capture_output=True,
        )
        self.assertEqual(scaffold.returncode, 0, scaffold.stderr)
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
        result = self.run_installer("--target", "both", "--scope", "project", "--repo", str(repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        subdir = repo / "src" / "nested"
        subdir.mkdir(parents=True)
        env = os.environ.copy()
        env["XDG_CACHE_HOME"] = str(self.root / "hook-cache")
        env["CLAUDE_PROJECT_DIR"] = str(repo)

        for provider, config_path in (
            ("codex", repo / ".codex" / "hooks.json"),
            ("claude", repo / ".claude" / "settings.json"),
        ):
            config = json.loads(config_path.read_text(encoding="utf-8"))
            handler = config["hooks"]["SessionStart"][0]["hooks"][0]
            payload = json.dumps({
                "hook_event_name": "SessionStart",
                "source": "startup",
                "session_id": f"smoke-{provider}",
                "cwd": str(subdir),
            })
            cp = subprocess.run(
                handler["command"],
                input=payload, text=True, capture_output=True, shell=True, cwd=subdir, env=env,
            )
            self.assertEqual(cp.returncode, 0, f"{provider}: {cp.stderr}\ncommand={handler['command']}")


if __name__ == "__main__":
    unittest.main()
