#!/usr/bin/env python3
# Hook entry for spec-mode plugin.
#
# Phase 3: wires Code-Doc Sync Guard.
#   - UserPromptSubmit: inject status block; start new turn in ledger; refresh tasks_files.
#   - PreToolUse: INV-1 — block code edits not covered by tasks/doc-change/freeform.
#   - PostToolUse: append the just-completed change to the ledger.
#   - Stop: INV-2 (turn conservation).
#   - SessionStart / SessionEnd: track Claude session, sync sentinel.
#
# Invariants:
#   - Never raise out of main(). Internal errors log to audit and return 0.
#   - SPEC_MODE_GUARD=off → global bypass.

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spec_state  # noqa: E402
import spec_sync   # noqa: E402


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


def _resolve_project_root(payload: dict, ledger: dict) -> Path:
    """Pick the project root used to classify code edits.

    Priority:
      1. ledger['project_root'] if previously set
      2. payload['cwd']
      3. os.getcwd()
    """
    pr = ledger.get("project_root")
    if pr:
        return Path(pr).expanduser()
    cwd = payload.get("cwd") or os.getcwd()
    return Path(cwd).expanduser()


def _edit_target(payload: dict) -> Optional[Path]:
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("path")
    if not raw:
        return None
    return Path(raw).expanduser()


# --- handlers ---------------------------------------------------------------

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

    spec_dir = Path(info["spec_dir"])
    ledger = spec_sync.read_ledger(spec_dir)
    tasks_files = spec_sync.extract_tasks_files(spec_dir)
    project_root = _resolve_project_root(payload, ledger)
    spec_sync.start_new_turn(ledger, project_root, tasks_files)
    # Freeform flag is read from per-spec config and mirrored into ledger.
    config = spec_sync._read_spec_config(spec_dir)
    ledger["freeform_mode"] = bool(config.get("freeformMode"))
    spec_sync.write_ledger(spec_dir, ledger)

    block = spec_state.render_status_block(info)
    if ledger["freeform_mode"]:
        block += "\nmode:          freeform (INV-1 relaxed, INV-2 still enforced)"
    else:
        block += "\nmode:          strict"
    block += f"\ntasks_files:   {len(tasks_files)} entries"
    block += f"\nturn:          {ledger['turn_id']}"

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

    target = _edit_target(payload)
    if target is None:
        _audit("PreToolUse", payload, "ok-no-target", "")
        return ok()

    spec_dir = Path(info["spec_dir"])
    ledger = spec_sync.read_ledger(spec_dir)
    project_root = _resolve_project_root(payload, ledger)
    current_phase = info.get("current_phase") or "unknown"
    session_id = info.get("session_id") or _prefer_session_id()
    slug = info.get("spec_slug") or spec_dir.name

    cls = spec_sync.classify_path(target, spec_dir, project_root)

    if cls == "outside":
        _audit("PreToolUse", payload, "ok-outside", str(target))
        return ok()

    if cls == "spec-doc":
        # INV-3: verify lock ownership before spec-doc writes.
        decision, info_msg = spec_sync.check_verify_lock(spec_dir, session_id, slug)
        if decision == "deny":
            ledger["last_violation"] = {"id": "INV-3", "file": str(target), "at": spec_sync._now()}
            spec_sync.write_ledger(spec_dir, ledger)
            _audit("PreToolUse", payload, "deny-INV-3", str(target))
            return deny(info_msg)
        _audit("PreToolUse", payload, f"ok-spec-doc[{info_msg}]", str(target))
        return ok()

    # project-code branch.
    # INV-6 phase gate first (absolute; freeform does NOT exempt).
    decision, msg = spec_sync.check_phase_gate(current_phase)
    if decision == "deny":
        ledger["last_violation"] = {
            "id": "INV-6",
            "phase": current_phase,
            "file": str(target),
            "at": spec_sync._now(),
        }
        spec_sync.write_ledger(spec_dir, ledger)
        _audit("PreToolUse", payload, "deny-INV-6", f"phase={current_phase} target={target}")
        return deny(msg)

    # Then INV-1 (relaxable by freeform).
    decision, msg = spec_sync.check_pre_edit(target, spec_dir, project_root, ledger)
    if decision == "deny":
        ledger["last_violation"] = {"id": "INV-1", "file": str(target), "at": spec_sync._now()}
        spec_sync.write_ledger(spec_dir, ledger)
        _audit("PreToolUse", payload, "deny-INV-1", str(target))
        return deny(msg)

    _audit("PreToolUse", payload, "ok-code-allowed", str(target))
    return ok()


def handle_post_tool_use(payload: dict) -> int:
    info = spec_state.find_active_spec(prefer_session_id=_prefer_session_id())
    if info is None:
        spec_state.sync_any_active_sentinel()
        return ok()

    target = _edit_target(payload)
    if target is None:
        _audit("PostToolUse", payload, "ok-no-target", "")
        return ok()

    spec_dir = Path(info["spec_dir"])
    ledger = spec_sync.read_ledger(spec_dir)
    project_root = _resolve_project_root(payload, ledger)
    cls = spec_sync.classify_path(target, spec_dir, project_root)

    if cls == "spec-doc":
        spec_sync.append_change(ledger, "doc", str(target), payload.get("tool_name") or "")
    elif cls == "project-code":
        spec_sync.append_change(ledger, "code", str(target), payload.get("tool_name") or "")
    else:
        _audit("PostToolUse", payload, "ok-outside", str(target))
        return ok()

    spec_sync.write_ledger(spec_dir, ledger)
    _audit("PostToolUse", payload, f"ledger-{cls}", str(target))
    return ok()


def handle_stop(payload: dict) -> int:
    info = spec_state.find_active_spec(prefer_session_id=_prefer_session_id())
    if info is None:
        spec_state.sync_any_active_sentinel()
        return ok()

    spec_dir = Path(info["spec_dir"])
    ledger = spec_sync.read_ledger(spec_dir)
    violations = spec_sync.check_stop(ledger)

    if violations:
        ledger["last_violation"] = {
            "ids": [v["id"] for v in violations],
            "at": spec_sync._now(),
        }
        spec_sync.write_ledger(spec_dir, ledger)
        body = "\n\n".join(v["msg"] for v in violations)
        _audit("Stop", payload, "deny-" + "+".join(v["id"] for v in violations), "")
        return deny(body)

    # Pass: reset turn counters (but keep turn_id until next UserPromptSubmit).
    spec_sync.reset_turn(ledger)
    spec_sync.write_ledger(spec_dir, ledger)
    _audit("Stop", payload, "ok-conserved", info.get("spec_slug") or "")
    return ok()


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
