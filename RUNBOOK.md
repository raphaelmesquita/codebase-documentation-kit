# Installation and Migration Runbook

## 0. Clone and verify prerequisites

```bash
git clone https://github.com/raphaelmesquita/codebase-documentation-kit.git
cd codebase-documentation-kit
python --version
git --version
python -m unittest discover -s tests -v
```

Python 3.10+ is required. Git is required for automatic session impact detection. Run installer and migration commands from this checkout; target repositories do not need a copy of the toolkit when user scope is used.

## 1. Choose deployment scope

### User scope

Use when you want the toolkit available across local repositories without committing the toolkit itself into each repository.

```bash
python install.py --target codex --scope user
python install.py --target claude --scope user
python install.py --target both --scope user
```

The installer preserves unrelated existing hooks and replaces only hook handlers owned by this toolkit.

User-scope locations:

```text
Codex skills:       ~/.agents/skills/
Codex hooks:        ~/.codex/hooks.json
Claude skills:      ~/.claude/skills/
Claude hooks:       ~/.claude/settings.json
Shared runtime:     ~/.codebase-documentation-kit/runtime/
```

The generated hook commands use the exact Python executable that ran the installer, which is preferable on local Windows installations.

### Project scope

Use when the repository itself must carry the configuration, especially for cloned remote environments.

```bash
python install.py --target codex --scope project --repo .
python install.py --target claude --scope project --repo .
python install.py --target both --scope project --repo .
```

Project-scope locations:

```text
Codex skills:       .agents/skills/
Codex hooks:        .codex/hooks.json
Claude skills:      .claude/skills/
Claude hooks:       .claude/settings.json
Shared runtime:     .codebase-documentation-kit/runtime/
```

Project hook commands use `python3` and are aimed at Git/Linux-style remote environments. For local Windows, prefer user scope unless the project environment already exposes `python3` in its shell.

## 2. Dry-run installation

Before changing user or project configuration:

```bash
python install.py --target both --scope user --dry-run
python install.py --target both --scope project --repo . --dry-run
```

The installer merges JSON and removes/replaces only handlers whose command contains this toolkit's markers. It does not replace unrelated hook groups.

## 3. Convert a repository using V1

Do not begin by editing `AGENTS.md` manually.

### Detect

```bash
python runtime/docsctl.py status . --json
```

Expected legacy states:

```text
v1-legacy
v1-probable
```

### Plan, no writes

```bash
python migrate.py . --agents both --json
```

Review:

- `semantic_review_required`
- `warnings`
- planned file rewrites/creates

### Apply only when unambiguous

```bash
python migrate.py . --agents both --apply --json
```

The migrator:

- rewrites only recognized V1 documentation-routing lines;
- removes the V1 root instruction that invokes `$codebase-documentation-architect` after every task;
- removes the mandatory direct `docs/state/README.md` route;
- preserves unrelated `AGENTS.md` instructions;
- preserves a `CLAUDE.md` that is exactly `@AGENTS.md`;
- creates `CLAUDE.md` as `@AGENTS.md` when `AGENTS.md` exists and Claude support is requested;
- adds `.docsctl.json`;
- writes a migration backup outside the repository before changing files.

It refuses automatic application when root agent instructions require semantic merging, or when legacy procedure documents may contain project-specific facts.

### Validate

```bash
python runtime/docsctl.py validate . --json
```

Commit the migration separately from unrelated product changes when practical.

## 4. Compatibility when V2 is installed before repository migration

This ordering is supported.

A V1 repository may still contain a task-end instruction that invokes `$codebase-documentation-architect`. The V2 architect skill contains a compatibility bridge. On such a call it detects the V1 repository, avoids the former broad maintenance workflow, and routes through the migration path first.

Hooks remain inert until `.docsctl.json` identifies the repository as V2, so installing the new user-level package does not immediately impose V2 completion behavior on untreated repositories.

## 5. Rollback

Every applied migration creates an external backup recording both files that existed and files that were absent before migration.

Restore the newest backup for the repository:

```bash
python rollback.py . --json
```

Restore a specific backup:

```bash
python rollback.py . --backup /path/to/migration-YYYYMMDD-HHMMSS.zip --json
```

Rollback restores changed files and removes files that were created by that migration but did not previously exist.

## 6. Bootstrap a new repository

Install the environment integration first, then either invoke the architect skill or use the deterministic scaffold as a starting point:

```bash
python runtime/docsctl.py scaffold . --agents both --json
```

Scaffold never overwrites existing project files. The architect should then inspect the compact scan and replace placeholder content with repository-specific facts where necessary.

## 7. Normal V2 task flow

No explicit documentation instruction is required in `AGENTS.md` after migration.

```text
SessionStart
  -> silent snapshot of HEAD, dirty state, dirty-file hashes, and existing validation failures

normal coding task
  -> no documentation hook per tool call

Stop
  -> compare with baseline
  -> validate deterministic invariants
  -> no changes: silent allow
  -> tests/generated only: silent allow
  -> new deterministic failure: compact continuation with exact failures
  -> source/config/unknown or doc-structure change: one compact maintainer continuation

second Stop after maintainer decision
  -> allow if no new deterministic failure remains
  -> refresh baseline
```

The maintainer may conclude that no documentation write is necessary. That is a valid result.

## 8. Codex hook trust

After adding or changing non-managed Codex hooks, review/trust them in Codex before relying on them. The installer cannot grant that trust on your behalf.

Because project hooks are code execution, commit and review the exact runtime scripts with the repository when project scope is used.

## 9. Claude Code remote/cloud use

A local `~/.claude/skills/` installation is for local sessions. For a cloned remote/cloud session, use project scope so `.claude/skills/` and `.claude/settings.json` travel with the repository.

Recommended:

```bash
python install.py --target claude --scope project --repo .
git add .claude .codebase-documentation-kit
```

Then review the generated diff before commit.

## 10. Updating the toolkit later

### User scope

Re-run the installer from the new package:

```bash
python install.py --target both --scope user
```

It replaces only the two toolkit skill directories, the shared runtime, and toolkit-owned hook handlers.

### Project scope

Re-run the appropriate project installation and commit the generated tooling diff:

```bash
python install.py --target claude --scope project --repo .
```

Repository `AGENTS.md` and `CLAUDE.md` do not name the toolkit in V2, so ordinary toolkit upgrades should not require repository-instruction migrations.

## 11. Uninstall

Local:

```bash
python install.py --target codex --scope user --uninstall
python install.py --target claude --scope user --uninstall
python install.py --target both --scope user --uninstall
```

The shared user runtime is removed only when neither Codex nor Claude Code still has toolkit-owned hooks, so uninstalling one target does not break the other.

Project:

```bash
python install.py --target codex --scope project --repo . --uninstall
python install.py --target claude --scope project --repo . --uninstall
```

Uninstalling the environment integration does not rewrite project documentation or remove `.docsctl.json`. That is deliberate: the repository documentation model remains valid even without automatic completion hooks.

## 12. Troubleshooting

### Stop hook keeps asking for maintenance

Check:

```bash
python runtime/docsctl.py impact . --latest --json
python runtime/docsctl.py validate . --json
```

A Stop continuation is intentionally limited by the hook's `stop_hook_active` handling. New deterministic validation failures remain blockers until fixed; semantic documentation review gets one targeted continuation and may finish with no write.

### Existing broken docs make every task fail

They should not. Session snapshots record pre-existing deterministic validation failures. The Stop hook blocks only failures not present in the session baseline.

### Migration says semantic review is required

Do not use a force flag as a routine shortcut. Inspect only the root instruction files or the flagged legacy procedure docs. Preserve project-specific content, decide which instructions are shared versus Claude-specific, then mark/validate V2.

### Repository is not Git-backed

Architecture/scaffold/validation still work, but session impact detection is intentionally Git-oriented. For reliable automatic maintenance gating, use the toolkit in a Git repository.
