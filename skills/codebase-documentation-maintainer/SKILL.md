---
name: codebase-documentation-maintainer
description: Perform targeted documentation and memory follow-through after repository changes. Use when a completion hook asks for documentation review or when the user explicitly asks whether completed code, config, API, deployment, or workflow changes require docs or durable memory updates. Do not bootstrap or rescan the repository.
---

# Codebase Documentation Maintainer

Perform the smallest documentation follow-through justified by the completed task.

## Scope

1. Use the changed paths and candidate docs supplied by the completion hook. If they are unavailable, execute the bundled `scripts/docsctl.py impact . --latest --json`. If no session snapshot exists, inspect only the current task diff.
2. Do not inventory or rescan the whole repository.
3. Inspect only changed implementation/configuration sections and documentation that can plausibly be affected.
4. Update project docs only when observable behavior, API/data contracts, configuration, deployment, workflow, error handling, or user-visible output would otherwise be stale or incomplete.

## The candidate list is a starting point, not the boundary

The hook's reverse index finds documents that **mention a changed document by path**. It cannot find two common cases, and both produce silent staleness:

5. **Documents that describe the superseded fact in their own words, without linking.** After replacing a fact, grep for the *old content* — the previous number, term, tier, name, or threshold — not only for references to the file. A document that recommends what you just replaced will not appear in a path-based index.
6. **Documents that should mention the fact and do not.** Ask which document is the canonical home for the topic you changed, and open it even if it links to nothing. A doc is invisible to every search when it is stale *by omission* — for example, a "current configuration" page that never named the setting you just changed.

## Checks that catch the failures a diff review misses

7. **Derived values.** When a line item in a computed table changes, **recompute the derived values from their parts** rather than adjusting them by the delta. Verify the stated total still equals the sum of the rows; drift accumulates silently across revisions.
8. **Claims that reason from a changed value.** Updating a number is not enough. Find statements that *argue from* it — "the only reason", "exceeds the budget", "fits within", "X times faster", "this is what forces Y" — and re-evaluate whether the reasoning still holds. A recommendation whose justification quietly became false is worse than a stale number, because it keeps persuading.
9. **Counts and enumerated lists.** If the change alters a count (entries, items, open questions) or the membership of a list, update every place that states that count or enumerates that list. When the same list is duplicated across documents, **convert the copies to pointers** to the canonical one instead of updating each — duplicated lists diverge again on the next change.
10. **Identifier series.** When adding rows to a numbered or prefixed series, confirm the identifier is not already in use in that document. Reusing a number makes every existing cross-reference ambiguous. If a collision already shipped, renumber only the newer entries and leave a mapping note.
11. **Summaries and banners last.** Edits are local; a document's truth is global. After changing a section, sweep the whole file — executive summary, tables, closing sections — and update the banner or status marker **last**, once the body is settled.

## Memory follow-through includes removal

12. Update `MEMORY.md` only for current priorities, active risks, critical invariants, or facts future agents would otherwise need to rediscover. Default to no memory write for ordinary completed changes.
13. **Prune as deliberately as you add.** Memory drifts into changelog one justified entry at a time. Ask of the *existing* entries, not only the new one: does this still change what someone does next? A decision that closed, a risk that was resolved, a count that moved — demote it to a pointer or delete it. The history already lives in the commit log and the project's decision records.
14. Promote genuinely durable context to the canonical project doc or the state/ directory when appropriate. Keep only a short pointer in memory if it has current steering value.
15. Do not create generic maintenance manuals, duplicate Git history, or write a delta merely to prove this skill ran.

## Finish

After any documentation write, execute the bundled `scripts/docsctl.py validate . --json` and fix deterministic failures. If no documentation change is needed, finish without touching repository files.

Report what you changed and, just as explicitly, **what you found stale and chose to leave** — a reviewer cannot tell silence from oversight.
