# Architecture Guidance

Read this reference only for bootstrap, alignment, migration ambiguity, or documentation-topology decisions.

## Evidence and source precedence

Use executable evidence to establish current behavior. Prefer, in order:

1. source code and executable configuration;
2. tests, schemas, typed interfaces, and generated runtime contracts;
3. current project documentation;
4. legacy or historical documentation.

Keep useful historical context but label it when it no longer describes current behavior. Record unresolved contradictions rather than choosing a convenient answer.

## Documentation topology

Keep navigation shallow and domain-oriented.

- Root `README.md` is for humans: purpose, setup, major structure, and a pointer to `docs/README.md`.
- `docs/README.md` is the project documentation router. It should answer where to look for common tasks and major domains.
- Use `docs/<topic>.md` for a topic that needs one page.
- Use `docs/<domain>/README.md` only when a domain needs multiple pages. The local README should route to those pages, not repeat them.
- Use `docs/state/` for durable context that is not naturally owned by a domain page, such as persistent constraints, accepted assumptions, long-lived known issues, or cross-cutting decisions. Do not create it when there is no durable state.
- Archive superseded material only when keeping it in the active navigation would confuse retrieval.

Avoid generic repository-local manuals about how agents should maintain documentation. Those procedures belong to the toolkit.

## Always-loaded instructions

Keep shared project rules in `AGENTS.md`. For repositories used by both Codex and Claude Code, make `CLAUDE.md` import `@AGENTS.md`. Put Claude-only rules below that import only when they are truly provider-specific.

Documentation guidance in always-loaded instructions should be routing, not process. A typical two-line section is enough:

- use `docs/README.md` when project documentation is needed;
- use `MEMORY.md` when current cross-session project context is needed.

Do not require every task to read docs, memory, or durable state. Do not mention the toolkit or a skill name there. Completion hooks handle maintenance automatically for treated repositories.

## MEMORY.md

Memory is a high-signal steering layer, not a changelog.

Good memory content includes:

- current priorities that affect near-term work;
- critical invariants not obvious from code;
- active risks or gaps;
- recent deltas only when they change how the next agent should work;
- do-not-rediscover facts whose repeated investigation is costly.

Do not record routine task completion, formatting work, obvious implementation details, or facts already easy to retrieve from canonical documentation. If `Recent Deltas` is used, keep at most five rows and use local timestamps in `yyyy-MM-dd HH:mm` format.

## Migration from V1

V1 commonly contains four documentation bullets in the root agent instructions, including a mandatory `$codebase-documentation-architect` completion invocation and a direct `docs/state/README.md` route. The deterministic migrator removes the skill coupling, rewrites the known canonical routing lines to lazy wording, preserves the `@AGENTS.md` Claude shim, and adds `.docsctl.json`.

Do not automatically rewrite ambiguous standalone `CLAUDE.md` content into shared `AGENTS.md`. Some instructions may be Claude-specific. The migration tool reports `semantic_review_required` so the model inspects only those root instruction files instead of rescanning the repository.

Legacy generic procedure locations such as `docs/operations/documentation-system/`, `documentation-maintenance.md`, `memory-workflow.md`, or `document-editing-rules.md` can be removed only after project-specific facts have been extracted.

## Bootstrap coverage priorities

For a new documentation model, cover in this order:

1. reliable entry points and setup;
2. core domain behavior and critical workflows;
3. architecture boundaries and integrations;
4. configuration, deployment, operations, and failure modes that are project-specific;
5. secondary utilities and lower-risk areas.

Do not document every source file. Document concepts, contracts, workflows, and operational facts that improve retrieval or prevent mistakes.

## Completion quality

A treated repository should have:

- discoverable project documentation through `docs/README.md`;
- concise cross-provider instructions without embedded maintenance procedures;
- selective memory with current steering value;
- no broken active documentation links;
- one canonical home for each important concept when practical;
- a V2 `.docsctl.json` marker so deterministic hooks can activate without adding prompt text.
