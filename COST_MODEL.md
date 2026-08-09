# Context and Operation Cost Model

This file documents the design target, not billing guarantees. Token counts vary by tokenizer, host-added context, repository content, and model behavior.

## Measured instruction footprint

The supplied V1 package had these context-facing files on the normal completion path:

```text
SKILL.md                         3,987 bytes
bootstrap-checklist.md          11,685 bytes
maintenance-procedures.md        9,959 bytes
--------------------------------------------
forced completion path          25,631 bytes
```

V2 routine completion uses:

```text
codebase-documentation-maintainer/SKILL.md   ~1.8 KB
Stop hook feedback when needed                compact, path-focused
successful hook feedback                      0 model-visible context
```

By raw instruction bytes, the routine skill body is about 93% smaller than the V1 forced completion instruction path before counting repository reads.

For architecture work, a common V1 core path was approximately:

```text
SKILL.md + bootstrap checklist + taxonomy + page templates
~29.9 KB
```

The V2 architect plus its optional structural reference is approximately:

```text
architect SKILL.md + architecture reference
~8.7 KB
```

That is about 71% smaller by raw bytes before repository evidence is read.

## More important than file size

V2 changes operations as well as prompt size:

- no repository-wide completion rescan;
- no mandatory maintenance reference read;
- no per-tool hook process;
- no mandatory `docs/state/README.md` read or creation;
- no task-by-task memory write;
- no runtime consistency check of the toolkit's own examples/templates;
- no provider-specific duplicate shared runtime in project installs;
- deterministic validation and Git impact classification consume CPU, not model context;
- existing validation debt is baselined instead of re-injected on every task.

## Expected frequency model

### Every V2 session

- one silent `SessionStart` snapshot;
- one `Stop` diff/validation check;
- no model context when no follow-through is needed.

### Test-only/generated-only task

- no maintainer invocation;
- no repository documentation write.

### Source/config task

- at most one compact semantic documentation-review continuation under the normal Stop flow;
- maintainer inspects task diff and plausible docs only;
- no write is a valid outcome.

### Architecture/migration task

- architect skill is intentionally more expensive but should be rare;
- deterministic scan limits initial repository exploration;
- optional reference/template loading is demand-driven.

## Budgets

Default repository budgets enforced as warnings by `.docsctl.json`:

```text
MEMORY.md       12,000 bytes
docs/README.md 20,000 bytes
```

They are context hygiene defaults, not correctness limits, and can be changed per repository in `.docsctl.json`.
