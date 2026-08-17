#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import docsctl


def read_input() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def repo_from_input(data: dict) -> Path | None:
    cwd = data.get("cwd") or "."
    try:
        return docsctl.resolve_repo(cwd)
    except Exception:
        return None


# Cap on new warnings surfaced in one message. Warnings ride along on a message
# the hook was already going to send, so they must never dominate it.
WARNING_CAP = 3


def new_entries(
    stored_counts: object,
    stored_list: object,
    current_counts: object,
    current_list: list[str],
) -> list[str]:
    """Entries present now beyond what the baseline already had."""
    baseline: Counter[str] = Counter()
    if isinstance(stored_counts, dict):
        # A snapshot can be hand-edited or written by another version, so a
        # malformed count must degrade instead of crashing the hook.
        for entry, count in stored_counts.items():
            try:
                baseline[str(entry)] = int(count)
            except (TypeError, ValueError):
                baseline[str(entry)] = 1
    elif isinstance(stored_list, list):
        baseline = Counter(str(entry) for entry in stored_list)
    current = Counter(current_counts) if isinstance(current_counts, dict) else Counter(current_list)
    out: list[str] = []
    for entry, count in current.items():
        out.extend([entry] * max(0, count - baseline[entry]))
    return out


def warning_suffix(new_warnings: list[str]) -> str:
    """Advisory tail appended to a message the hook is already sending.

    Deliberately worded to deprioritise: a warning injected into a working
    agent's context must not turn into a false-positive hunt mid-task.
    """
    if not new_warnings:
        return ""
    shown = new_warnings[:WARNING_CAP]
    hidden = len(new_warnings) - len(shown)
    more = f" (+{hidden} more)" if hidden > 0 else ""
    return (
        " Non-blocking documentation warnings also appeared during this session. "
        "They do not gate completion: fix one only if it touches what you already changed, "
        "otherwise leave it. " + "; ".join(shown) + more + "."
    )


def begin(data: dict) -> None:
    root = repo_from_input(data)
    session_id = str(data.get("session_id") or "unknown")
    if root is None or not docsctl.git_available(root):
        return
    state = docsctl.detect_status(root)["state"]
    if state == "v2":
        docsctl.snapshot(root, session_id)
    elif state in {"v1-legacy", "v1-probable"}:
        # Capture a silent legacy baseline so a repository migrated to V2 during
        # this same session can still attribute task changes correctly. Stop
        # remains inert while the repository is still V1.
        docsctl.snapshot(root, session_id)


def evaluate_stop(data: dict) -> dict:
    root = repo_from_input(data)
    session_id = str(data.get("session_id") or "unknown")
    if root is None:
        return {"action": "allow"}
    state = docsctl.detect_status(root)["state"]
    snap, _ = docsctl.load_snapshot(root, session_id, latest=False)
    stop_hook_active = bool(data.get("stop_hook_active"))

    # Hooks may capture a legacy baseline solely to support an in-session V1 ->
    # V2 migration, but completion gating stays fully inert until V2 is active.
    if state != "v2":
        return {"action": "allow"}

    if not docsctl.git_available(root):
        if stop_hook_active:
            return {"action": "allow"}
        return {
            "action": "continue",
            "kind": "git-unavailable",
            "message": "Documentation impact could not be determined because Git is unavailable. Restore Git access or review the task documentation manually before finishing.",
            "report": docsctl.impact_report(root, {"git_available": False}),
        }

    if snap is None:
        if stop_hook_active:
            return {"action": "allow"}
        return {
            "action": "continue",
            "kind": "impact-indeterminate",
            "message": "Session impact cannot be reconstructed because its baseline snapshot is missing or corrupt. Review the current task documentation manually; automatic gating resumes on the next session baseline.",
        }

    # The baseline was captured by a different toolkit version, so this session
    # straddles an upgrade. The stored validation deltas cannot be trusted -- the
    # set of checks may have changed underneath -- and comparing them would report
    # pre-existing conditions as regressions of this session. Re-baseline and allow.
    if str(snap.get("toolkit_version") or "") != docsctl.VERSION:
        docsctl.snapshot(root, session_id)
        return {"action": "allow"}

    report = docsctl.impact_report(root, snap)
    if report.get("impact_indeterminate"):
        return {
            "action": "continue",
            "kind": "git-unavailable",
            "message": "Documentation impact could not be determined because Git is unavailable. Restore Git access before finishing.",
            "report": report,
        }
    validation = docsctl.validate(root)

    new_failures = new_entries(
        snap.get("validation_failure_counts"),
        snap.get("validation_failures"),
        validation.get("failure_counts"),
        validation["failures"],
    )
    new_warnings = new_entries(
        snap.get("validation_warning_counts"),
        snap.get("validation_warnings"),
        validation.get("warning_counts"),
        validation.get("warnings", []),
    )
    if new_failures:
        problems = new_failures[:6]
        return {
            "action": "continue",
            "kind": "validation",
            "message": "Documentation model validation found new deterministic failures from this session. Fix only these before finishing: "
            + "; ".join(problems)
            + "." + warning_suffix(new_warnings),
            "report": report,
        }

    def rebaseline() -> None:
        docsctl.snapshot(
            root,
            session_id,
            validation["failures"],
            validation["failure_counts"],
            validation.get("warnings", []),
            validation.get("warning_counts"),
        )

    if not report["changed_count"]:
        rebaseline()
        return {"action": "allow"}

    if stop_hook_active:
        rebaseline()
        return {"action": "allow"}

    if report["needs_documentation_review"]:
        changed = [item["path"] for item in report["changed"][:12]]
        candidates = report.get("candidate_docs", [])[:8]
        message = (
            "Run the codebase-documentation-maintainer skill for targeted follow-through. "
            "Do not rescan the repository. Review only the task changes and relevant documentation. "
            f"Changed paths: {', '.join(changed) if changed else '(none)'}."
        )
        if candidates:
            message += f" Candidate docs: {', '.join(candidates)}."
        mentioning = report.get("mentioning_docs", [])[:5]
        if mentioning:
            message += (
                " These documents mention a changed document by path and may now describe it wrongly: "
                + ", ".join(mentioning)
                + "."
            )
        message += (
            " This list is path-based and misses docs that restate the old fact in their own words,"
            " or that are stale by omission - also search for the superseded content itself."
        )
        message += (
            " Update docs or MEMORY.md only if they would otherwise be stale, incomplete, or useful"
            " for future steering - and prune MEMORY.md entries that stopped steering anything."
        )
        message += warning_suffix(new_warnings)
        return {"action": "continue", "kind": "review", "message": message, "report": report}

    rebaseline()
    return {"action": "allow"}
