# Stable Release Test Report - Codebase Documentation Kit 2.1.1

Date: 2026-08-09

## Verdict

**PASS for package-level stable release**, with live-host limitations documented separately.

## Basis

The stable work started from `raphaelmesquita/codebase-documentation-kit` at commit `dd85be263306b0f6f0e2ff192fb1cd9978af4f88` (the current `main` when this release work began) and the previously Work-tested V2 artifact. Core runtime, installer, skill, and test file Git blob hashes matched the tested artifact before the stable delta was applied.

Current OpenAI Codex hook documentation was rechecked for `SessionStart`, `Stop`, `stop_hook_active`, project hook root resolution, and `commandWindows`. Current Claude Code documentation was rechecked for `Stop`, `stop_hook_active`, `hookSpecificOutput.additionalContext`, `${CLAUDE_PROJECT_DIR}`, and project command behavior.

## Stable changes under test

The 2.1 stable line closes the remaining release-candidate findings. Release 2.1.1 additionally migrates the Codex installation layout from `.agents/skills` to `.codex/skills`:

1. V1 -> V2 migration during the same session no longer loses its baseline or enters a missing-snapshot continuation loop.
2. Source changes made before same-session migration remain visible to the semantic maintenance gate.
3. Architect finish instructions preserve the derived Codex/Claude provider scope.
4. Codex project hooks include a Windows-specific command override.
5. Tests execute the installed POSIX hook commands from nested repository directories.
6. Common generated outputs and language-specific tests are filtered deterministically.
7. Editing an existing doc is distinguished from adding/deleting/renaming documentation structure.
8. Codex skills install only under `.codex/skills`; existing `config.toml` content is left unchanged by skill placement.
9. The standalone V1 architect and older/manual kit copies under `.agents/skills` are removed transactionally when their `SKILL.md` declares one of the exact reserved product skill names; unrelated `.agents` content is preserved.
10. A same-name `.agents` path that does not identify itself as the expected reserved skill blocks installation rather than being deleted or left as a duplicate.
11. Claude-only installation does not perform Codex legacy-layout cleanup.

## Test count

The suite contains **63 tests** across:

- `tests/test_kit.py`
- `tests/test_runtime_regressions.py`
- `tests/test_installer_regressions.py`

On the stable validation host:

```text
Ran 63 tests
OK (skipped=1)
```

The single skip is the Windows-junction-specific regression. It is platform-gated by design. All other tests passed.

## New 2.1 regressions

- same-session V1 migration uses a legacy SessionStart baseline;
- same-session source impact survives migration and requests only one semantic continuation;
- a missing baseline does not request another continuation when `stop_hook_active` is already true;
- generated/test classification covers `.next`, coverage/output/target caches, dependency lock artifacts, Go tests, Ruby specs, and `.Tests` path segments;
- ordinary modification of existing documentation does not count as structural change;
- architect Finish section contains `<provider-scope>` and no hardcoded `--agents both`;
- Codex project handlers contain `commandWindows`;
- actual installed POSIX Codex and Claude SessionStart commands execute successfully from a nested repository directory.
- Codex user and project skills are installed under `.codex/skills` with no toolkit installation created under `.agents`;
- locally modified V1 architect copies are removed when their `SKILL.md` still declares the reserved `codebase-documentation-architect` name;
- pre-manifest/manual maintainer copies are removed when their `SKILL.md` declares the reserved `codebase-documentation-maintainer` name;
- pre-existing Codex `config.toml` content remains byte-for-byte unchanged by skill placement;
- unmodified and locally modified V1 architect copies, pre-manifest/manual kit copies, and manifest-owned V2 toolkit skills are removed from `.agents/skills` during Codex layout migration;
- unrelated `.agents` skills survive the migration;
- ambiguous same-name `.agents` collisions fail before new installation writes;
- project dry-run reports legacy cleanup with zero writes;
- Claude-only installation leaves `.agents` untouched.

## Retained coverage from the Work-tested candidate

The existing regression suite continues to cover migration ambiguity, byte/CRLF preservation, rollback conflicts, hard links/reparse points, transaction compensation, staged-index changes, untracked/deleted/renamed files, pre-existing documentation debt, Markdown links, malformed models, required-path failures, ownership manifests, multi-target install order, uninstall isolation, dry-run zero writes, and Python-interpreter changes for user scope.

## Package gate

The release packaging procedure additionally requires:

- Python compilation of runtime/install entry points;
- test execution before packaging;
- exclusion of `__pycache__`, `.pyc`, `.pyo`, `.pytest_cache`, `.git`, and other build caches;
- a clean extraction of the produced ZIP;
- a second test run from that extracted ZIP;
- SHA-256 generation for the final artifact.

## Limitations

See `KNOWN_LIMITATIONS.md`. Most importantly, host contracts are simulated and command strings are smoke-tested, but a full interactive Codex/Claude host session was not available in this environment.
