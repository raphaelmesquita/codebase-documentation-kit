---
name: codebase-documentation-architect
description: Bootstrap, align, migrate, or simplify a repository's project documentation and agent memory. Use for first-time documentation architecture, upgrading repositories from the legacy codebase-documentation-architect model, or reorganizing stale documentation. Do not use for routine task-end maintenance; use codebase-documentation-maintainer instead.
---

# Codebase Documentation Architect

Design or migrate the repository documentation model while keeping always-loaded agent instructions small.

## Start deterministically

1. Execute the bundled `scripts/docsctl.py status . --json`.
2. Execute `scripts/docsctl.py scan . --json` for a compact repository inventory.
3. Choose exactly one mode: `fresh bootstrap`, `alignment`, `v1 migration`, or `simplification`.
4. Do not broadly read the repository before the scan identifies relevant source, config, tests, deployment, and existing documentation paths.

## V1 compatibility bridge

A repository upgraded from V1 may still contain an always-loaded instruction that invokes `$codebase-documentation-architect` at task completion. If this skill is invoked in that situation, do not run the old maintenance workflow and do not rescan the repository. Run `status`, follow the V1 migration flow below, then use the maintainer workflow only for the current task changes. This makes installing V2 before converting every repository safe.

## V1 migration

When status is `v1-legacy` or `v1-probable`:

1. Derive the provider scope before planning: standalone `CLAUDE.md` means `claude`; standalone `AGENTS.md` means `codex`; an existing dual-provider layout means `both`. Use `both` for a single-provider layout only when the user explicitly requests the additional provider.
2. Run `scripts/docsctl.py migrate . --agents <provider-scope> --json` without `--apply` first.
3. If `semantic_review_required` is false, apply with `--apply`.
4. If it is true, inspect only `AGENTS.md` and `CLAUDE.md`, preserve all project-specific instructions, separate any provider-specific Claude guidance if necessary, then complete the migration manually and run `scripts/docsctl.py mark . --agents <provider-scope> --overwrite`.
5. Preserve a valid `CLAUDE.md` containing `@AGENTS.md` when both providers are enabled. That is the portable shim for Claude Code.
6. Do not delete legacy project facts. Generic documentation-operation manuals may be removed only after any project-specific facts are relocated.

The V2 repository must not depend on this skill name in `AGENTS.md` or `CLAUDE.md`. Maintenance is hook-gated, so future toolkit upgrades do not require editing repository instructions.

## Fresh bootstrap or alignment

Use [references/architecture.md](references/architecture.md) only when structural guidance is needed. Inspect executable evidence before asserting project behavior. Prefer existing useful docs over renaming or rebuilding them.

The portable default is:

- `AGENTS.md` for concise shared, always-loaded repository instructions.
- `CLAUDE.md` importing `@AGENTS.md`, with Claude-specific additions only when truly provider-specific.
- `docs/README.md` as the documentation router.
- `MEMORY.md` as selective cross-session steering memory, not a changelog.
- `docs/state/` only when durable project state actually exists.
- `.docsctl.json` as machine-readable V2 state. Agents should not read it unless needed.

Use the template asset matching the file you are creating. Do not load every template.

## Boundaries

- Keep generic documentation-maintenance procedures in this toolkit, not in target repositories.
- Preserve unrelated repository instructions in `AGENTS.md` and `CLAUDE.md`.
- Prefer code and executable configuration over prose when sources conflict.
- Mark uncertainty instead of inventing facts.
- Do not create an empty `docs/state/` solely to satisfy the model.
- Do not add task-by-task entries to `MEMORY.md` unless they have future steering value.

## Finish

Run `scripts/docsctl.py validate . --json`. Resolve deterministic failures. Explain intentional warnings. Ensure `.docsctl.json` uses the same derived provider scope selected for scaffold/alignment. If scaffold already created it, do not mark it again. If the marker is absent, create it with `scripts/docsctl.py mark . --agents <provider-scope>`. Never default a Claude-only or Codex-only repository to `both`.
