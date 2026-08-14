# Changelog

## 2.2.0 - 2026-08-14

### Documentation integrity checks

Motivated by a documentation-heavy repository where the same defect recurred four times in one session: a document updated in one region and left stale in another, with no existing check able to see it. Three of the four were intra-document; one was an index describing a document it linked.

- **Upgrade guard.** The session snapshot records `toolkit_version`. When `Stop` finds a baseline written by a different version it re-baselines and allows, instead of comparing validation deltas across a changed set of checks and reporting pre-existing conditions as regressions of the current session.
- **Warning channel.** `validate()` returns `warning_counts`; warnings are stored in the snapshot; warnings introduced during a session ride along on a message the hook was already sending — capped at three and worded to deprioritise. Warnings still never summon a message of their own.
- **Link labels.** A label that spells out a path is checked against its destination. A label goes stale when the target moves while the link still resolves, so nothing else catches it. Relative spellings (`../a/b.md`), repo-relative tails, and dotted directories (`.scratch/README.md`) are accepted.
- **Anchors.** `#section` fragments are validated against the destination's headings, approximating GitHub's slugger including duplicate suffixes and explicit `id`/`name` anchors. Emitted as a **warning**, because slug semantics are an approximation. Previously the fragment was discarded and a stale anchor failed silently.
- **`doc_dirs`.** Optional explicit list in `.docsctl.json` extending the scanned set beyond `docs/` plus the root files. Absent by default. A list rather than a "scan everything" switch, so a code repository does not pull in vendored markdown, changelogs and templates.
- **`docs_are_product`.** Optional flag in `.docsctl.json`. When set, editing an existing tracked document counts as needing review. Off by default: in a code repository it would speak on every prose touch-up; in a repository whose product is the documentation, that work previously received no review at all.
- **Version bump.** `VERSION` moves to 2.2.0 so the upgrade guard can actually distinguish baselines; the set of checks changed, so the version must change with it. Bump on every future change to the check set.
- **Reverse index.** When documentation changes, `impact_report` also reports documents that mention a changed document by path — whatever asserts something *about* it. One batched `git grep`, capped at five, skipping bare generic names. Renames need no special handling: the old path already appears as its own deleted entry, and it is the valuable needle.

### Windows user-scope hooks

- Codex user-scope installation now emits a PowerShell-safe `commandWindows` override while retaining the exact Python interpreter that ran the installer.
- Added a Windows regression that installs into paths containing spaces and executes both `SessionStart` and `Stop` through PowerShell.

## 2.1.1 - 2026-08-09

Codex installation-layout migration.

### Codex skill location

- Codex user skills now install under `~/.codex/skills/`; project skills install under `.codex/skills/`.
- The installer no longer writes toolkit skills to `.agents/skills`.
- Codex `config.toml` is not modified merely to register these skills.
- Existing hooks remain under `.codex/hooks.json`, and the shared runtime remains under `.codebase-documentation-kit/runtime/`.

### Mandatory cleanup of the old layout

- A Codex install or uninstall removes recognized legacy toolkit skills from `.agents/skills` in the same filesystem transaction.
- The standalone pre-kit V1 architect is removed whenever its `SKILL.md` declares `name: codebase-documentation-architect`, including locally modified copies that no longer match the original package fingerprints.
- Pre-manifest/manual copies of either reserved kit skill name are removed when their `SKILL.md` declares that exact name.
- Prior manifest-owned V2 skill trees are removed only when their ownership manifest and content still verify.
- Unrelated `.agents` content is preserved. If only replaced product skills remain, the now-empty `.agents` tree is pruned.
- A same-name path that does not identify itself as the expected reserved skill blocks preflight instead of being destructively guessed.
- Claude-only installation never performs Codex `.agents` cleanup.

### Tests

- Added regressions for user/project `.codex/skills` layout, untouched `config.toml`, unmodified and locally modified V1 cleanup, pre-manifest/manual kit cleanup, V2-owned cleanup, foreign `.agents` preservation, ambiguous-collision refusal, project dry-run cleanup planning, and Claude-only isolation.

## 2.1.0 - 2026-08-09

Stable release hardening on top of the Work-tested V2 candidate.

### Completion lifecycle

- Recognized V1 repositories now capture a silent legacy SessionStart baseline so a V1 -> V2 migration performed during the same session retains task-impact attribution.
- Stop gating remains inert while a repository is still V1.
- Missing/corrupt baseline and Git-unavailable conditions no longer re-trigger indefinitely after the Stop hook already caused a continuation.
- Same-session migration with source changes still triggers exactly one targeted semantic maintenance pass.
- Ordinary edits to existing documentation are no longer misclassified as documentation-structure changes merely because the file was clean at the baseline.

### Provider scope

- Architect finish instructions now use the already-derived `<provider-scope>` instead of hardcoding `both`.
- If `scaffold` already created `.docsctl.json`, the architect is instructed not to mark it again.
- Claude-only and Codex-only bootstrap semantics remain single-provider unless the additional provider is explicitly requested.

### Project-scope hooks

- Codex project installation now emits the official `commandWindows` override in addition to the POSIX command.
- The Windows command locates the nearest Git root before executing the committed runtime so sessions started from subdirectories resolve correctly.
- Regression tests now execute the actual installed POSIX Codex and Claude project hook commands from a nested repository directory instead of testing only the Python scripts directly.

### Cost/impact classification

- Added deterministic generated-output recognition for `coverage/`, `.next/`, `out/`, `target/`, `.cache/`, `package-lock.json`, `pnpm-lock.yaml`, `bun.lock`, `bun.lockb`, and `go.sum`.
- Expanded test recognition for language conventions such as `*_test.go`, `*_spec.rb`, and `.Tests` / `_tests` path segments.
- These classifications avoid semantic maintainer activation when the task changed only recognized generated/test artifacts.

### Tests

- Stable suite now contains 54 tests, up from 46 in the Work-tested candidate.
- Added regressions for same-session migration, Stop-loop prevention, provider-scope finish instructions, installed project command execution, Codex Windows hook configuration, generated/test classification, and docs edit classification.
- Clean package extraction is tested again before release.

## 2.0.0 tested candidate - 2026-08-09

- Hardened migration/rollback with atomic writes, conflict detection, link/path protections, interruption compensation, and idempotent V2 migration.
- Added Git index-aware impact detection, deterministic Markdown link validation, failure multisets, and safe malformed-model diagnostics.
- Added ownership manifests, transactional multi-target install, preserved unrelated hooks/settings, and shared runtime lifecycle.
- Expanded the original 14-test baseline to 46 tests during isolated Work adversarial review.
