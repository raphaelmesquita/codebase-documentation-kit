# Context and Operation Cost Model

This file documents design measurements, not billing guarantees. Token counts vary by tokenizer, host-added context, repository content, and model behavior.

## Measured instruction footprint

The measured V1 package forced this normal completion path:

```text
SKILL.md                         3,987 bytes
bootstrap-checklist.md          11,685 bytes
maintenance-procedures.md        9,959 bytes
--------------------------------------------
forced completion path          25,631 bytes
```

Release 2.1 routine completion uses:

```text
codebase-documentation-maintainer/SKILL.md   1,794 bytes / 249 words
successful hook feedback                      0 model-visible context
Stop feedback when needed                     compact, path-focused
```

By raw instruction bytes, the routine skill body remains about 93% smaller than the V1 forced completion path before repository reads are counted.

For architecture work, the measured V1 core path was approximately 29.9 KB. Release 2.1 uses:

```text
architect SKILL.md             4,409 bytes
architecture reference         4,750 bytes
-----------------------------------------
common architecture path       9,159 bytes
```

That remains about 69% smaller by raw bytes than the measured V1 architecture path.

## Operational savings

The larger saving is operational:

- no repository-wide completion rescan;
- no mandatory maintenance reference read;
- no `PostToolUse` process on every edit;
- no mandatory `docs/state/README.md` read or creation;
- no default task-by-task `MEMORY.md` write;
- deterministic Git impact/validation consume CPU, not model context;
- pre-existing deterministic debt is baselined rather than re-injected;
- routine edits to existing docs no longer look like doc-structure changes;
- common generated outputs and language-specific tests are filtered before semantic maintenance.

Release 2.1 specifically reduces false maintainer activations for `.next/`, `coverage/`, `out/`, `target/`, `.cache/`, `package-lock.json`, `pnpm-lock.yaml`, `go.sum`, `*_test.go`, `*_spec.rb`, and equivalent recognized test paths.

## Frequency model

### V2 session

- one silent `SessionStart` snapshot for active V2 repositories;
- recognized V1 repositories may also get one silent baseline solely to support same-session migration;
- one `Stop` impact/validation check;
- no model context when no follow-through is needed.

### Test/generated/docs-only task

- no maintainer invocation when classification is deterministic and no new validation failure exists.

### Source/config task

- at most one compact semantic documentation-review continuation under the normal flow;
- maintainer reads changed paths and plausible docs only;
- no write is a valid outcome.

### Architecture/migration task

- architect is intentionally more expensive but rare;
- deterministic scan limits initial exploration;
- optional reference/template loading remains demand-driven.

## Repository context budgets

Default warning budgets stored in `.docsctl.json`:

```text
MEMORY.md       12,000 bytes
docs/README.md 20,000 bytes
```

These are context-hygiene defaults, not correctness limits, and can be changed per repository.
