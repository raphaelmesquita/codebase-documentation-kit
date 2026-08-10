# Installation and Migration Runbook

## 0. Verify prerequisites

```bash
python --version
git --version
python -m unittest discover -s tests -v
```

Python 3.10+ is required. Git is required for automatic session impact detection.

## 1. Choose deployment scope

### User scope

Use user scope for normal local development across many repositories:

```bash
python install.py --target codex --scope user --dry-run
python install.py --target claude --scope user --dry-run
python install.py --target both --scope user --dry-run

python install.py --target both --scope user
```

Locations:

```text
Codex skills:       ~/.codex/skills/
Codex hooks:        ~/.codex/hooks.json
Claude skills:      ~/.claude/skills/
Claude hooks:       ~/.claude/settings.json
Shared runtime:     ~/.codebase-documentation-kit/runtime/
```

User-scope hooks use the exact Python executable that ran the installer. This is the preferred setup for local Windows use.

### Codex layout migration from `.agents`

Release 2.1.1 installs Codex skills under `.codex/skills`, not `.agents/skills`. On every install or uninstall where Codex is selected, the installer checks the old `.agents/skills` locations for this toolkit.

- the V1 `codebase-documentation-architect` is removed whenever its `SKILL.md` declares that exact skill name, including locally modified pre-manifest copies;
- prior/manual `codebase-documentation-architect` and `codebase-documentation-maintainer` copies without manifests are also removed when their `SKILL.md` declares the exact reserved product name;
- manifest-owned kit architect/maintainer installations are verified and removed;
- unrelated skills or other `.agents` content are preserved;
- if a reserved same-name directory does not actually identify itself as the expected skill, preflight fails before installing the new copy;
- dry-run reports the legacy removal but performs no write.

A successful Codex installation therefore leaves neither the old standalone architect skill nor an older kit copy active under `.agents/skills`. Claude-only installation does not perform this cleanup.

### Project scope

Use project scope when the integration must travel with the repository, especially remote/cloud environments:

```bash
python install.py --target codex --scope project --repo . --dry-run
python install.py --target claude --scope project --repo . --dry-run
python install.py --target both --scope project --repo . --dry-run

python install.py --target both --scope project --repo .
```

Locations:

```text
Codex skills:       .codex/skills/
Codex hooks:        .codex/hooks.json
Claude skills:      .claude/skills/
Claude hooks:       .claude/settings.json
Shared runtime:     .codebase-documentation-kit/runtime/
```

For Codex, project settings include a POSIX command plus the officially supported `commandWindows` override. The Windows override locates the nearest Git root in Python so starting Codex from a subdirectory still resolves the committed runtime.

For Claude Code, project scope uses:

```text
python3 "${CLAUDE_PROJECT_DIR}/.codebase-documentation-kit/runtime/hook_claude.py"
```

This is optimized for POSIX/cloud environments. Claude Code supports project-root placeholders and recommends them for project scripts. For native local Windows Claude Code, prefer user scope unless the shell exposes `python3`.

## 2. Hook trust

Codex project hooks load only from trusted project configuration. After installing or changing non-managed Codex hooks, review/trust the exact definitions with the Codex hook UI before relying on them.

Hook commands execute with the user's permissions. Review committed project-scope runtime changes like any other executable code.

## 3. Convert a V1 repository

Start with detection, not manual edits:

```bash
python runtime/docsctl.py status . --json
```

Expected legacy states are `v1-legacy` or `v1-probable`.

Plan with no writes:

```bash
python migrate.py . --agents both --json
```

Inspect `semantic_review_required`, warnings, and planned actions. Use the real provider scope:

```text
--agents codex
--agents claude
--agents both
```

Apply only when unambiguous:

```bash
python migrate.py . --agents both --apply --json
python runtime/docsctl.py validate . --json
```

The migrator preserves unrelated project instructions and refuses automatic destructive merging when root agent files or legacy procedure files may contain project-specific facts.

## 4. Same-session V1 -> V2 migration

Release 2.1 supports the important rollout case where V2 is installed globally before an individual V1 repository is migrated.

For a recognized V1 repository:

```text
SessionStart
  -> silent legacy Git/validation baseline

repository remains V1
  -> Stop gating is inert

architect migrates repository to V2 in the same session
  -> the existing baseline is reused

Stop
  -> actual task changes are attributable
  -> migration-only changes can finish silently
  -> source/config changes still receive one targeted maintainer continuation
```

This avoids the former missing-baseline loop without requiring per-tool hooks.

## 5. Fresh bootstrap

Invoke the architect skill or start from the deterministic scaffold with the provider scope you actually intend to support:

```bash
python runtime/docsctl.py scaffold . --agents codex --json
python runtime/docsctl.py scaffold . --agents claude --json
python runtime/docsctl.py scaffold . --agents both --json
```

`scaffold` does not overwrite existing project files and currently creates `.docsctl.json` with that same scope. If scaffold already created the marker, do not run `mark` again. If the documentation architecture was created manually and the marker is absent, finish with:

```bash
python runtime/docsctl.py mark . --agents <codex|claude|both>
python runtime/docsctl.py validate . --json
```

A Claude-only bootstrap must remain Claude-only unless Codex support was explicitly requested, and vice versa.

## 6. Normal V2 task flow

```text
SessionStart
  -> silent baseline of HEAD, dirty state, index identity, and existing deterministic failures

normal task
  -> no per-tool documentation process

Stop
  -> compare final state to baseline
  -> validate deterministic invariants
  -> no changes: allow silently
  -> tests/generated only: allow silently
  -> ordinary edits to existing docs only: allow silently
  -> source/config/unknown or real doc-structure change: one targeted maintainer continuation
  -> new deterministic validation failure: compact exact feedback

second Stop after semantic maintainer continuation
  -> allow when no new deterministic failure remains
```

The maintainer may legitimately decide that no documentation write is required.

## 7. Generated and test classification

The deterministic impact classifier suppresses maintainer activation for common generated outputs, including:

```text
dist/
build/
coverage/
.next/
out/
target/
.cache/
*.min.js
*.min.css
*.map
*.lock
package-lock.json
pnpm-lock.yaml
bun.lock / bun.lockb
go.sum
```

Common language-specific test shapes are also recognized, including test/spec directories, `test_*`, `*_test`, `*_spec`, `.test.*`, `.spec.*`, and directory names ending in `.Tests` / `_tests`.

If a project has generated or test conventions outside these defaults, extending the deterministic classifier is preferable to adding prose instructions to the skill.

## 8. Rollback

Every applied migration creates an external backup.

Newest backup:

```bash
python rollback.py . --json
```

Specific backup:

```bash
python rollback.py . --backup /path/to/migration-YYYYMMDD-HHMMSS.zip --json
```

Rollback is conflict-aware, rejects unsafe aliasing/path escapes, and avoids overwriting post-migration edits unless an explicit recovery path is used.

## 9. Update

### User scope

```bash
python install.py --target both --scope user
```

### Project scope

```bash
python install.py --target both --scope project --repo .
```

Review and commit the generated tooling diff. V2 repositories do not name the toolkit skill in root agent instructions, so a runtime/skill update normally does not require a repository documentation migration.

The `toolkit_version` inside an existing `.docsctl.json` is informational about the model marker and is not required to match the installed runtime version for a 2.x update.

## 10. Uninstall

```bash
python install.py --target codex --scope user --uninstall
python install.py --target claude --scope user --uninstall
python install.py --target both --scope user --uninstall

python install.py --target codex --scope project --repo . --uninstall
python install.py --target claude --scope project --repo . --uninstall
```

The shared runtime is removed only after no remaining installed target depends on it. Uninstalling the environment integration deliberately does not rewrite repository documentation or delete `.docsctl.json`.

## 11. Troubleshooting

### Stop reports a missing/corrupt baseline

The first Stop requests one manual documentation-impact check. A Stop already continued by that feedback is allowed rather than entering a loop. A fresh baseline will be created on the next normal SessionStart.

### Stop reports Git unavailable

Restore Git if practical or perform the task documentation review manually. The hook does not repeatedly continue an already continued Stop for this indeterminate infrastructure condition.

### Existing broken docs block unrelated tasks

They should not. The session baseline stores deterministic failure counts. Only failures newly introduced after the baseline are blockers.

### Migration requires semantic review

Inspect only the flagged root instruction files or procedure docs. Preserve project facts, decide shared versus provider-specific instructions, then complete the migration and validate. Do not force ambiguous conversion as a routine shortcut.

### Project hook does not execute

For Codex, inspect the trusted `.codex/hooks.json` definition and verify Python is available as `python3` on POSIX or `python` on Windows. For Claude Code project scope, verify `python3` exists in the project shell and `${CLAUDE_PROJECT_DIR}` is available. User scope avoids these portable-command constraints by using the installer interpreter directly.
