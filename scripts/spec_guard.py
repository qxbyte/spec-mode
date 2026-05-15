#!/usr/bin/env python3
# Hook entry for spec-mode plugin. Reads Claude Code hook JSON on stdin,
# dispatches to a handler, writes an audit log line, exits.
#
# Phase 2: integrate spec_state — SessionStart/End record Claude sessions,
# UserPromptSubmit injects a status block when a spec is active, all other
# handlers fast-exit when no active spec.
#
# Hook invariants:
#   - Never raise out of main(). Internal errors log to audit and return 0.
#   - Honor SPEC_MODE_GUARD=off as a global bypass.
#   - Audit only meaningful events (skip when no active spec on idle session).

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Make sibling modules importable when invoked from hooks.json.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import spec_state  # noqa: E402


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
        pass


def ok() -> int:
    return 0


def deny(msg: str) -> int:
    sys.stderr.write(msg)
    return 2


def _prefer_session_id() -> str:
    return os.environ.get("TERM_SESSION_ID") or ""


def handle_session_start(payload: dict) -> int:
    sid = payload.get("session_id") or ""
    try:
        spec_state.write_claude_session(sid, payload)
        is_active = spec_state.sync_any_active_sentinel()
    except Exception as e:
        _audit("SessionStart", payload, "state-error", str(e))
        return ok()
    _audit("SessionStart", payload, "ok", f"any_active={is_active}")
    return ok()


def handle_user_prompt_submit(payload: dict) -> int:
    info = spec_state.find_active_spec(prefer_session_id=_prefer_session_id())
    if info is None:
        spec_state.sync_any_active_sentinel()
        return ok()

    block = spec_state.render_status_block(info)
    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": block,
        }
    }
    sys.stdout.write(json.dumps(output, ensure_ascii=False))
    _audit("UserPromptSubmit", payload, "injected", info.get("spec_slug") or "")
    return ok()


def handle_pre_tool_use(payload: dict) -> int:
    info = spec_state.find_active_spec(prefer_session_id=_prefer_session_id())
    if info is None:
        spec_state.sync_any_active_sentinel()
        return ok()
    _audit("PreToolUse", payload, "ok-active-spec", info.get("spec_slug") or "")
    return ok()  # Phase 3 will add INV-1 / INV-6 here.


def handle_post_tool_use(payload: dict) -> int:
    info = spec_state.find_active_spec(prefer_session_id=_prefer_session_id())
    if info is None:
        spec_state.sync_any_active_sentinel()
        return ok()
    _audit("PostToolUse", payload, "ok-active-spec", info.get("spec_slug") or "")
    return ok()  # Phase 3 will update sync-ledger here.


def handle_stop(payload: dict) -> int:
    info = spec_state.find_active_spec(prefer_session_id=_prefer_session_id())
    if info is None:
        spec_state.sync_any_active_sentinel()
        return ok()
    _audit("Stop", payload, "ok-active-spec", info.get("spec_slug") or "")
    return ok()  # Phase 3 will add INV-2 / INV-4 here.


def handle_session_end(payload: dict) -> int:
    sid = payload.get("session_id") or ""
    try:
        spec_state.clear_claude_session(sid)
        spec_state.sync_any_active_sentinel()
    except Exception as e:
        _audit("SessionEnd", payload, "state-error", str(e))
        return ok()
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
