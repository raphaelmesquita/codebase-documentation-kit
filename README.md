# Codebase Documentation Kit 2.1.1

A low-context documentation maintenance toolkit for Codex and Claude Code.

The kit separates rare documentation architecture work from frequent task-end maintenance and moves deterministic inventory, Git impact detection, validation, migration, rollback, and installation work out of model context.

## Why this exists

The original V1 skill coupled routine completion maintenance to a large architecture skill through always-loaded repository instructions. V2 replaces that flow with deterministic lifecycle hooks and two skills with very different frequencies:

1. `SessionStart` captures a silent Git-aware baseline.
2. Work proceeds normally with no per-tool documentation hooks.
3. `Stop` compares the repository with the baseline.
4. Test-only, generated-only, and ordinary documentation-only edits can finish silently.
5. Source/config/structural changes receive at most one compact semantic review continuation in the normal flow.
6. New deterministic documentation-model failures are reported directly.
7. Successful hooks add no model-visible context.

Recognized V1 repositories may receive a silent baseline before migration so that a V1 -> V2 conversion performed during the same session does not lose attribution of task changes. Completion gating remains inert while the repository is still V1.

## Package layout

```text
README.md
RUNBOOK.md
CHANGELOG.md
COST_MODEL.md
KNOWN_LIMITATIONS.md
TEST_REPORT.md
install.py
migrate.py
rollback.py
runtime/
  docsctl.py
  hook_common.py
  hook_codex.py
  hook_claude.py
skills/
  codebase-documentation-architect/
  codebase-documentation-maintainer/
tests/
```

## Two skills, two frequencies

### `codebase-documentation-architect`

Use for first bootstrap, architecture/alignment, V1 conversion, and structural simplification. It may read the optional architecture reference when needed. Provider scope is derived from the actual repository and is preserved through scaffold, migration, and final marking.

### `codebase-documentation-maintainer`

Use only after a completion hook requests review, or when explicitly requested. It starts from changed paths and candidate docs and must not rescan the repository.

## Deterministic CLI

`runtime/docsctl.py` provides:

- `status`: detect untreated, probable V1, legacy V1, or V2 state;
- `scan`: compact repository inventory for architecture work;
- `migrate`: plan or apply known-safe V1 transformations;
- `rollback`: restore a migration backup safely;
- `scaffold`: create only missing low-risk V2 skeleton files;
- `mark`: add or update the V2 machine-readable marker;
- `validate`: check model invariants and active local Markdown links;
- `session-start`: capture a Git-aware task baseline;
- `impact`: compare a session against that baseline;
- `session-finalize`: reset a session baseline.

The runtime uses only the Python standard library.

## Repository model

V2 uses:

- `AGENTS.md` as concise shared repository instructions when Codex is supported;
- `CLAUDE.md` importing `@AGENTS.md` when both agents are supported;
- `docs/README.md` as the documentation router;
- `MEMORY.md` only for high-signal cross-session steering context;
- `docs/state/` only when durable state really exists;
- `.docsctl.json` as a machine-readable model marker that agents normally do not need to read.

A V2 repository does not reference either toolkit skill by name in `AGENTS.md` or `CLAUDE.md`. Toolkit upgrades therefore do not normally require repository-instruction migrations.

## Requirements

- Python 3.10 or newer.
- Git for automatic session impact detection.
- For project-scope hooks, the committed environment must expose Python to the generated hook command. Codex has separate POSIX and Windows commands; Claude project scope is optimized for POSIX/cloud environments. See [RUNBOOK.md](RUNBOOK.md).

Bootstrap, migration, and validation can still run without Git, but automatic completion gating is Git-oriented.

## Install

Preview before writing:

```bash
python install.py --target both --scope user --dry-run
python install.py --target both --scope user
```

User scope is recommended for normal local use. It installs the two skills and one shared runtime under your home directory while preserving unrelated hooks and settings. On Windows, Codex handlers include a PowerShell-safe `commandWindows` override that invokes the exact installer interpreter even when paths contain spaces.

For Codex user scope, the toolkit intentionally uses `~/.codex/skills/`. Current Codex still loads `$CODEX_HOME/skills` for backward compatibility. When Codex is selected, installation also migrates away from the previous `.agents/skills` layout. The reserved product skill names `codebase-documentation-architect` and `codebase-documentation-maintainer` are removed from `.agents/skills` in the same transaction when their `SKILL.md` identifies them by that exact name. This covers the pre-kit V1 architect, locally modified V1 copies, and older/manual kit copies without ownership manifests. Manifest-owned kit copies are verified before removal. Unrelated `.agents` content is preserved, and a same-name path that does not identify itself as the expected skill fails preflight instead of being deleted or left as a duplicate active skill.

Project scope is intended when the integration itself must travel with the repository, including remote/cloud environments:

```bash
python install.py --target both --scope project --repo /path/to/repo --dry-run
python install.py --target both --scope project --repo /path/to/repo
```

Project scope writes `.codex/`, `.claude/`, and `.codebase-documentation-kit/` inside the target repository. Codex skills are installed under `.codex/skills`; the installer does not install this toolkit under `.agents`. Review and commit generated project-scope files deliberately.

For Codex project scope, the installer now emits both the standard POSIX command and the supported `commandWindows` override. For Claude Code project scope, the hook uses `${CLAUDE_PROJECT_DIR}` and `python3`, which is appropriate for the intended POSIX/cloud deployment. Prefer user scope for native local Windows Claude Code unless the project shell provides `python3`.

## Convert a repository from V1

After the toolkit is installed in user scope, **the standard migration procedure does not use the toolkit checkout**. You do not need to remember where `codebase-documentation-kit` was cloned, and the target repository does not need a copy of the toolkit.

From the root of the legacy repository, open Codex and invoke the installed architect skill:

```text
$codebase-documentation-architect
```

That is the normal migration command. The installed skill uses the shared runtime under your home directory to detect the repository model, derive the active provider scope, plan the V1 migration, apply it when unambiguous, and validate the result. If semantic review is required, it inspects only the flagged repository instructions or legacy procedure documents instead of making destructive guesses.

The migration:

- removes only recognized V1 routing/invocation lines;
- preserves unrelated root instructions and line endings;
- keeps a valid `CLAUDE.md` shim when appropriate;
- refuses destructive guesses for ambiguous layouts;
- creates an external backup before writes;
- supports conflict-aware rollback;
- can be performed repository by repository after the global toolkit installation.

If a recognized V1 repository is migrated during the same Codex session that opened it, the SessionStart hook's legacy baseline allows the Stop hook to evaluate the actual task changes instead of entering an indeterminate-baseline loop.

Direct `runtime/docsctl.py`, `migrate.py`, and `rollback.py` commands are advanced diagnostics and toolkit-development interfaces. They are not required for the normal Codex migration workflow.

## Cost behavior

The routine maintainer skill remains 1,794 bytes / 249 whitespace-separated words, about 93% smaller by raw instruction size than the V1 forced completion path measured for this project. Successful hooks add zero model-visible context.

Release 2.1 expands deterministic classification for common generated outputs and language-specific tests, reducing unnecessary maintainer calls for paths such as `.next/`, `coverage/`, `target/`, `package-lock.json`, `pnpm-lock.yaml`, `*_test.go`, and `*_spec.rb`.

See [COST_MODEL.md](COST_MODEL.md) for the measured model.

## Verification

The stable release adds workflow regressions on top of the Work-tested candidate, including:

- V1 -> V2 migration inside the same live session baseline;
- single-continuation behavior when a baseline is missing;
- preservation of source impact across same-session migration;
- Claude-only provider-scope finish instructions;
- Codex `commandWindows` generation;
- execution of the actual installed POSIX project hook commands from a repository subdirectory;
- expanded generated/test classification;
- documentation edit versus documentation-structure distinction.

See [TEST_REPORT.md](TEST_REPORT.md), [CHANGELOG.md](CHANGELOG.md), and [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## Design constraints

- No `PostToolUse` hook is required for normal operation.
- Session cache and migration backups live outside target repositories.
- The migrator never overwrites unrelated root agent instructions wholesale.
- Ambiguous `AGENTS.md` / `CLAUDE.md` layouts require semantic review.
- Pre-existing deterministic validation failures do not block an unrelated task.
- Missing/indeterminate baselines do not create an endless Stop loop.
- V1 procedure docs containing possible project facts are not auto-deleted.
