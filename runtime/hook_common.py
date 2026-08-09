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


def begin(data: dict) -> None:
    root = repo_from_input(data)
    session_id = str(data.get("session_id") or "unknown")
    if root is None or docsctl.detect_status(root)["state"] != "v2" or not docsctl.git_available(root):
        return
    docsctl.snapshot(root, session_id)


def evaluate_stop(data: dict) -> dict:
    root = repo_from_input(data)
    session_id = str(data.get("session_id") or "unknown")
    if root is None:
        return {"action": "allow"}
    state = docsctl.detect_status(root)["state"]
    snap, _ = docsctl.load_snapshot(root, session_id, latest=False)
    if state != "v2" and snap is None:
        return {"action": "allow"}
    if not docsctl.git_available(root):
        return {
            "action": "continue",
            "kind": "git-unavailable",
            "message": "Documentation impact could not be determined because Git is unavailable. Restore Git access before finishing.",
            "report": docsctl.impact_report(root, {"git_available": False}),
        }

    if snap is None:
        return {
            "action": "continue",
            "kind": "impact-indeterminate",
            "message": "Session impact is indeterminate because its baseline snapshot is missing or corrupt. Start a new session before finishing.",
        }

    report = docsctl.impact_report(root, snap)
    if report.get("impact_indeterminate"):
        return {
            "action": "continue",
            "kind": "git-unavailable",
            "message": "Documentation impact could not be determined because Git is unavailable. Restore Git access before finishing.",
            "report": report,
        }
    validation = docsctl.validate(root)
    stop_hook_active = bool(data.get("stop_hook_active"))

    stored_counts = snap.get("validation_failure_counts")
    if isinstance(stored_counts, dict):
        baseline_counts = Counter({str(failure): int(count) for failure, count in stored_counts.items()})
    else:
        baseline_counts = Counter(snap.get("validation_failures", []))
    current_counts = Counter(validation.get("failure_counts", Counter(validation["failures"])))
    new_failures: list[str] = []
    for failure, count in current_counts.items():
        new_failures.extend([failure] * max(0, count - baseline_counts[failure]))
    if new_failures:
        problems = new_failures[:6]
        return {
            "action": "continue",
            "kind": "validation",
            "message": "Documentation model validation found new deterministic failures from this session. Fix only these before finishing: " + "; ".join(problems),
            "report": report,
        }

    if not report["changed_count"]:
        docsctl.snapshot(root, session_id, validation["failures"], validation["failure_counts"])
        return {"action": "allow"}

    if stop_hook_active:
        docsctl.snapshot(root, session_id, validation["failures"], validation["failure_counts"])
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
        message += " Update docs or MEMORY.md only if they would otherwise be stale, incomplete, or useful for future steering."
        return {"action": "continue", "kind": "review", "message": message, "report": report}

    docsctl.snapshot(root, session_id, validation["failures"], validation["failure_counts"])
    return {"action": "allow"}
