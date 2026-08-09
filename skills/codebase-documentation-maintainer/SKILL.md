---
name: codebase-documentation-maintainer
description: Perform targeted documentation and memory follow-through after repository changes. Use when a completion hook asks for documentation review or when the user explicitly asks whether completed code, config, API, deployment, or workflow changes require docs or durable memory updates. Do not bootstrap or rescan the repository.
---

# Codebase Documentation Maintainer

Perform the smallest documentation follow-through justified by the completed task.

1. Use the changed paths and candidate docs supplied by the completion hook. If they are unavailable, execute the bundled `scripts/docsctl.py impact . --latest --json`. If no session snapshot exists, inspect only the current task diff.
2. Do not inventory or rescan the whole repository.
3. Inspect only changed implementation/configuration sections and documentation that can plausibly be affected.
4. Update project docs only when observable behavior, API/data contracts, configuration, deployment, workflow, error handling, or user-visible output would otherwise be stale or incomplete.
5. Update `MEMORY.md` only for current priorities, active risks, critical invariants, or facts future agents would otherwise need to rediscover. Default to no memory write for ordinary completed changes.
6. Promote genuinely durable context to the canonical project doc or `docs/state/` when appropriate. Keep only a short pointer in memory if it has current steering value.
7. Do not create generic maintenance manuals, duplicate Git history, or write a delta merely to prove this skill ran.

After any documentation write, execute the bundled `scripts/docsctl.py validate . --json` and fix deterministic failures. If no documentation change is needed, finish without touching repository files.
