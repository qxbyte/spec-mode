#!/usr/bin/env python3
# Hook entry for spec-mode plugin. Reads Claude Code hook JSON on stdin,
# dispatches to a handler, writes an audit log line, exits.
#
# Phase 1: every handler short-circuits to ok(). Real logic lands in later phases:
#   - Phase 2: spec_state integration (SessionStart, UserPromptSubmit status inject, SessionEnd)
#   - Phase 3: Code-Doc Sync Guard (PreToolUse INV-1/INV-6, PostToolUse ledger, Stop INV-2/INV-4)
#
# Invariants for THIS file, even in Phase 1:
#   - Never raise out of main(). Any internal error is logged and returns 0 (don't wedge user).
#   - Honor SPEC_MODE_GUARD=off as a global bypass.
#   - Audit-log every invocation so we can verify hook wiring without changing model behavior.

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

AUDIT_DIR = Path(
    os.environ.get("SPEC_MODE_AUDIT_DIR")
    or os.path.expanduser("~/.spec-mode/audit")
)


def _audit(event: str, payload: dict, decision: str, msg: str = "") -> None:
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        log_file = AUDIT_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "decision": decision,
            "msg": msg,
            "tool": payload.get("tool_name"),
            "session_id": payload.get("session_id"),
            "cwd": payload.get("cwd") or os.getcwd(),
        }
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Audit failure must never break the hook chain.
        pass


def ok() -> int:
    return 0


def deny(msg: str) -> int:
    # exit 2 + stderr content is how Claude Code surfaces hook denial back to the model.
    sys.stderr.write(msg)
    return 2


def handle_session_start(payload: dict) -> int:
    _audit("SessionStart", payload, "ok")
    return ok()


def handle_user_prompt_submit(payload: dict) -> int:
    _audit("UserPromptSubmit", payload, "ok")
    return ok()


def handle_pre_tool_use(payload: dict) -> int:
    _audit("PreToolUse", payload, "ok")
    return ok()


def handle_post_tool_use(payload: dict) -> int:
    _audit("PostToolUse", payload, "ok")
    return ok()


def handle_stop(payload: dict) -> int:
    _audit("Stop", payload, "ok")
    return ok()


def handle_session_end(payload: dict) -> int:
    _audit("SessionEnd", payload, "ok")
    return ok()


HANDLERS = {
    "session-start": handle_session_start,
    "user-prompt-submit": handle_user_prompt_submit,
    "pre-tool-use": handle_pre_tool_use,
    "post-tool-use": handle_post_tool_use,
    "stop": handle_stop,
    "session-end": handle_session_end,
}


def main(argv: list) -> int:
    if os.environ.get("SPEC_MODE_GUARD", "").lower() == "off":
        return 0

    if len(argv) < 2 or argv[1] not in HANDLERS:
        sys.stderr.write(f"spec_guard: unknown subcommand {argv[1:]!r}\n")
        return 0

    subcommand = argv[1]

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        _audit(subcommand, {}, "bad-json", str(e))
        return 0

    try:
        return HANDLERS[subcommand](payload)
    except Exception as e:
        _audit(subcommand, payload, "handler-error", f"{e}\n{traceback.format_exc()}")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
