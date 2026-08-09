#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common


def main() -> int:
    data = hook_common.read_input()
    event = data.get("hook_event_name")
    if event == "SessionStart":
        if data.get("source") != "compact":
            hook_common.begin(data)
        return 0
    if event == "Stop":
        result = hook_common.evaluate_stop(data)
        if result["action"] == "continue":
            print(json.dumps({"decision": "block", "reason": result["message"]}, ensure_ascii=False))
        else:
            print("{}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
