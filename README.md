# Codebase Documentation Kit v2

A low-context documentation maintenance toolkit for Codex and Claude Code.

The kit separates rare documentation architecture work from frequent task-end maintenance and moves deterministic work out of the model context.

## What changed from V1

V1 coupled routine completion maintenance to `codebase-documentation-architect` through root agent instructions and then loaded a large checklist and maintenance reference. V2 changes the control flow:

1. `SessionStart` records a silent Git-aware baseline outside the repository.
2. Work proceeds normally with no per-tool documentation hooks.
3. `Stop` compares the final repository state to the baseline.
4. Test-only or generated-only changes finish silently.
5. Source/config/structural changes receive one compact continuation asking the small maintainer skill for targeted review.
6. Deterministic validation failures introduced by the task are reported directly and compactly.
7. The second stop is allowed after the maintainer has had one chance to decide whether documentation is actually needed.

Successful hooks emit no model-visible context.

## Package layout

```text
codebase-documentation-kit-v2/
  README.md
  RUNBOOK.md
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

Use for first bootstrap, architecture/alignment, V1 conversion, and structural simplification. It may read the optional architecture reference when needed.

### `codebase-documentation-maintainer`

Use only after a completion hook requests review, or when explicitly requested. It starts from changed paths and candidate docs and must not rescan the repository.

## Deterministic CLI

`runtime/docsctl.py` provides:

- `status`: detect untreated, probable V1, legacy V1, or V2 state;
- `scan`: compact repository inventory for architecture work;
- `migrate`: plan or apply known-safe V1 transformations;
- `rollback`: restore the latest migration backup;
- `scaffold`: create only missing low-risk V2 skeleton files;
- `mark`: add the V2 machine-readable marker;
- `validate`: check model invariants and active local Markdown links;
- `session-start`: capture a Git-aware task baseline;
- `impact`: compare a session against that baseline;
- `session-finalize`: reset a session baseline.

It uses only the Python standard library.

## Repository model

V2 uses:

- `AGENTS.md` as concise shared repository instructions when Codex is supported;
- `CLAUDE.md` importing `@AGENTS.md` when both agents are supported;
- `docs/README.md` as the documentation router;
- `MEMORY.md` only for high-signal cross-session steering context;
- `docs/state/` only when durable state really exists;
- `.docsctl.json` as a machine-readable marker that agents do not need to read normally.

A V2 repository does not reference either toolkit skill by name in `AGENTS.md` or `CLAUDE.md`. This removes repository coupling to future skill names and versions.

## Requirements

- Python 3.10 or newer.
- Git for automatic session impact detection. Bootstrap, migration, and validation can still run without Git, but completion gating is designed for Git repositories.

## Install the kit

Clone this private repository on a machine where your GitHub account has access:

```bash
git clone https://github.com/raphaelmesquita/codebase-documentation-kit.git
cd codebase-documentation-kit
```

Choose the providers you use: `codex`, `claude`, or `both`. Preview the user-level installation first, then apply it:

```bash
python install.py --target both --scope user --dry-run
python install.py --target both --scope user
```

User scope is recommended for normal local use, especially on Windows. It installs the two skills and one shared runtime under your home directory while preserving unrelated hooks and settings.

Use project scope only when the integration must travel with a repository, such as a cloned remote/cloud environment:

```bash
python install.py --target both --scope project --repo /path/to/repo --dry-run
python install.py --target both --scope project --repo /path/to/repo
```

Project scope writes `.agents/`, `.codex/`, `.claude/`, and `.codebase-documentation-kit/` inside the target repository. Review and commit those generated files deliberately. Project hook commands expect `python3`; for local Windows use, prefer user scope unless `python3` is available in the project shell.

## Convert a repository from the legacy skill

Run these commands from this toolkit checkout. Replace `/path/to/legacy-repo` and select the providers actually used by that repository.

1. Start from a clean Git worktree or commit/stash unrelated changes.
2. Detect the current documentation model:

   ```bash
   python runtime/docsctl.py status /path/to/legacy-repo --json
   ```

3. Generate a migration plan with no writes:

   ```bash
   python migrate.py /path/to/legacy-repo --agents both --json
   ```

4. Inspect `semantic_review_required`, `warnings`, and the planned actions. Do not force an ambiguous migration as a routine shortcut.
5. If the plan is unambiguous, apply it and validate:

   ```bash
   python migrate.py /path/to/legacy-repo --agents both --apply --json
   python runtime/docsctl.py validate /path/to/legacy-repo --json
   ```

6. Review `git diff`, run the target repository's own tests, and commit the conversion separately from unrelated product work.

The migration creates an external backup before its first write. To restore the newest backup:

```bash
python rollback.py /path/to/legacy-repo --json
```

The converter removes only recognized V1 routing/invocation lines, preserves unrelated root instructions, and refuses destructive guesses when `AGENTS.md`, `CLAUDE.md`, or legacy procedure documents may contain project-specific facts. Installing V2 before converting repositories is safe: hooks remain inert until a repository has a valid `.docsctl.json` marker.

For standalone Claude repositories use `--agents claude`; for Codex-only repositories use `--agents codex`.

## Inspect and operate manually

Inspect before writing:

```bash
python runtime/docsctl.py status /path/to/repo --json
python runtime/docsctl.py migrate /path/to/repo --agents both --json
```

See [RUNBOOK.md](RUNBOOK.md) for installation, migration, rollback, trust, updates, uninstall, and troubleshooting.

## Verification and release evidence

- [TEST_REPORT.md](TEST_REPORT.md): complete test matrix and clean-ZIP verification.
- [CHANGELOG.md](CHANGELOG.md): fixes relative to the initial V2 candidate.
- [COST_MODEL.md](COST_MODEL.md): instruction and operational cost model.
- [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md): environment and coverage boundaries.

## Design constraints

- No `PostToolUse` hook is required for normal operation.
- Session cache and migration backups live outside target repositories.
- The migrator never overwrites unrelated root agent instructions wholesale.
- Ambiguous `AGENTS.md` / `CLAUDE.md` layouts require semantic review.
- Pre-existing validation failures do not block an unrelated task; only new deterministic failures introduced after the session baseline are completion blockers.
- V1 procedure docs containing possible project facts are not auto-deleted.
