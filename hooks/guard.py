#!/usr/bin/env python3
"""Claude Code PreToolUse hook: refuse direct GPU launches, point at `exp submit`.

Install in the research repo's .claude/settings.json:
  {"hooks": {"PreToolUse": [{"matcher": "Bash",
     "hooks": [{"type": "command", "command": "python3 hooks/guard.py"}]}]}}

Patterns are a regex list; override with EXP_GUARD_PATTERNS (one per line).
"""
import json
import os
import re
import sys

DEFAULT = [
    r"\btorchrun\b",
    r"\baccelerate\s+launch\b",
    r"\bdeepspeed\b",
    r"\bpython[0-9.]*\s+(-m\s+)?\S*(train|finetune|pretrain|sweep)\S*",
    r"\bCUDA_VISIBLE_DEVICES=",
]


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = data.get("tool_input", {}).get("command", "")
    if re.search(r"\bexp\s+submit\b", cmd):
        return
    pats = os.environ.get("EXP_GUARD_PATTERNS")
    pats = pats.splitlines() if pats else DEFAULT
    for p in pats:
        if p and re.search(p, cmd):
            print(
                f"Blocked: looks like a direct GPU launch ({p}). "
                "Queue it instead: exp submit --label <name> -- <command>, then exp wait <id>.",
                file=sys.stderr,
            )
            sys.exit(2)


if __name__ == "__main__":
    main()
