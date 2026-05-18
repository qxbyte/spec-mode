#!/usr/bin/env python3
# Hook entry for specode plugin.
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
#   - SPECODE_GUARD=off → global bypass.

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
import task_swarm_guard  # noqa: E402


AUDIT_DIR = Path(
    os.environ.get("SPECODE_AUDIT_DIR")
    or os.path.expanduser("~/.specode/audit")
)

# Per-file size cap. When today's daily log exceeds this, it gets truncated
# in place (keeping the most recent half). No cross-file pruning — older
# daily files are left alone. At ~256 bytes/record this holds ~80k entries
# per day, plenty of headroom for normal use; the cap exists to bound
# pathological growth (e.g. an error loop emitting tracebacks).
AUDIT_MAX_BYTES = int(
    os.environ.get("SPECODE_AUDIT_MAX_BYTES") or 20 * 1024 * 1024
)

_truncate_checked = False


def _maybe_truncate(log_file: Path) -> None:
    """If log_file is over the cap, rewrite it keeping only the tail half.

    Called once per process at first _audit() write. Safe under concurrent
    writers from other hook processes — worst case is a lost record at the
    rewrite boundary, which is acceptable for an advisory audit log.
    """
    try:
        size = log_file.stat().st_size
    except OSError:
        return
    if size <= AUDIT_MAX_BYTES:
        return
    keep = AUDIT_MAX_BYTES // 2
    try:
        with log_file.open("rb") as f:
            f.seek(-keep, 2)
            tail = f.read()
        nl = tail.find(b"\n")
        if nl >= 0:
            tail = tail[nl + 1:]
        marker = (
            json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "_truncate",
                "decision": "ok",
                "msg": f"prev_size={size} kept_bytes={len(tail)}",
            }, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        log_file.write_bytes(marker + tail)
    except OSError:
        pass


def _audit(event: str, payload: dict, decision: str, msg: str = "") -> None:
    global _truncate_checked
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        log_file = AUDIT_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"
        if not _truncate_checked:
            _truncate_checked = True
            _maybe_truncate(log_file)
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


def _enclosing_subagent_workspace(target: Path, project_root: Path) -> Optional[Path]:
    """If target lives inside a task-swarm agent workspace, return that ws path.

    The convention is:
      <project>/.task-swarm/runs/<RUN>/agents/stage-N-<role>[-rR]/{inbox,outbox,task.md,...}

    Return the agent workspace (the directory containing task.md) if target
    is inside it, else None.
    """
    try:
        target_abs = target.resolve()
        project_abs = project_root.resolve()
    except OSError:
        return None
    try:
        rel = target_abs.relative_to(project_abs)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 5:
        return None
    if parts[0] != ".task-swarm" or parts[1] != "runs" or parts[3] != "agents":
        return None
    ws = project_abs / parts[0] / parts[1] / parts[2] / parts[3] / parts[4]
    return ws if ws.exists() else None


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

    # Task-swarm run state, if a run is active in this project.
    swarm_block = _render_task_swarm_block(project_root)
    if swarm_block:
        block += "\n" + swarm_block

    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": block,
        }
    }
    sys.stdout.write(json.dumps(output, ensure_ascii=False))
    _audit("UserPromptSubmit", payload, "injected", info.get("spec_slug") or "")
    return ok()


def _render_task_swarm_block(project_root: Path) -> str:
    """Return a multi-line status block for the active task-swarm run, or ''."""
    run_dir = task_swarm_guard.find_active_run(project_root)
    if run_dir is None:
        return ""
    state_path = run_dir / "state.json"
    if not state_path.exists():
        return ""
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    summary_lines: list[str] = []
    counts = {"pending": 0, "running": 0, "converged": 0, "failed": 0, "skipped": 0}
    in_flight: list[str] = []
    for s in state.get("stages") or []:
        counts[s.get("phase", "pending")] = counts.get(s.get("phase", "pending"), 0) + 1
        if s.get("in_flight"):
            ifl = s["in_flight"]
            in_flight.append(f"stage {s['num']} {ifl['role']} r{ifl['round']}")
    next_hint = (
        f"sh ${{CLAUDE_PLUGIN_ROOT}}/scripts/run.sh "
        f"${{CLAUDE_PLUGIN_ROOT}}/scripts/task_swarm.py next --run {state['run_id']}"
    )
    summary_lines.append("--- task-swarm ---")
    summary_lines.append(f"run:           {state['run_id']}")
    summary_lines.append(
        f"stages:        ✔{counts['converged']} ▶{counts['running']} ○{counts['pending']}"
        f" ✗{counts['failed']} —{counts['skipped']}"
    )
    summary_lines.append(
        f"max_rounds:    {state['config']['max_rounds']}  parallel: {state['config']['parallel']}"
    )
    if in_flight:
        summary_lines.append("in-flight:     " + "; ".join(in_flight))
    summary_lines.append(f"next:          {next_hint}")
    summary_lines.append("------------------")
    return "\n".join(summary_lines)


def handle_pre_tool_use(payload: dict) -> int:
    info = spec_state.find_active_spec(prefer_session_id=_prefer_session_id())
    if info is None:
        spec_state.sync_any_active_sentinel()
        # Even without an active spec, INV-7 (Task subagent_type) may still
        # apply if a task-swarm run is active locally. Fall through.
        target = None
    else:
        target = _edit_target(payload)

    # ---- INV-7: Task tool subagent_type prefix ----
    tool_name = (payload.get("tool_name") or "").strip()
    if tool_name == "Task":
        project_root_for_swarm = Path(payload.get("cwd") or os.getcwd()).expanduser()
        if task_swarm_guard.is_task_swarm_active(project_root_for_swarm):
            subagent_type = (payload.get("tool_input") or {}).get("subagent_type") or ""
            decision, msg = task_swarm_guard.check_inv7_subagent_type(subagent_type)
            if decision == "deny":
                _audit("PreToolUse", payload, "deny-INV-7", subagent_type)
                return deny(msg)
            _audit("PreToolUse", payload, "ok-INV-7", subagent_type)
        return ok()

    if info is None or target is None:
        _audit("PreToolUse", payload, "ok-no-target", "")
        return ok()

    spec_dir = Path(info["spec_dir"])
    ledger = spec_sync.read_ledger(spec_dir)
    project_root = _resolve_project_root(payload, ledger)
    current_phase = info.get("current_phase") or "unknown"
    session_id = info.get("session_id") or _prefer_session_id()
    slug = info.get("spec_slug") or spec_dir.name

    # ---- INV-8: subagent @writes boundary ----
    subagent_ws = _enclosing_subagent_workspace(target, project_root)
    if subagent_ws is not None and task_swarm_guard.is_task_swarm_active(project_root):
        decision, msg = task_swarm_guard.check_inv8_writes_boundary(target, subagent_ws, project_root, spec_dir)
        if decision == "deny":
            _audit("PreToolUse", payload, "deny-INV-8", str(target))
            return deny(msg)
        # Inside a subagent workspace — internal swarm artifact, not project
        # source. Bypass spec_sync INV-1/INV-6 checks.
        _audit("PreToolUse", payload, "ok-swarm-internal", str(target))
        return ok()

    # ---- INV-9: protect tasks.md during task-swarm ----
    if task_swarm_guard.is_task_swarm_active(project_root) and task_swarm_guard.is_tasks_md(target, spec_dir):
        tool_input = payload.get("tool_input") or {}
        old_string = tool_input.get("old_string")
        new_string = tool_input.get("new_string")
        if old_string is not None and new_string is not None:
            try:
                full_text = target.read_text(encoding="utf-8")
                # Apply the Edit hypothetically to get the post-edit text.
                if tool_input.get("replace_all"):
                    new_text = full_text.replace(old_string, new_string)
                else:
                    new_text = full_text.replace(old_string, new_string, 1)
                decision, msg = task_swarm_guard.check_inv9_tasks_md_diff(full_text, new_text)
                if decision == "deny":
                    _audit("PreToolUse", payload, "deny-INV-9", str(target))
                    return deny(msg)
            except OSError:
                pass
        elif tool_name == "Write":
            # Full file overwrite — compare against current text if exists.
            try:
                old_text = target.read_text(encoding="utf-8") if target.exists() else ""
                new_text = tool_input.get("content") or ""
                decision, msg = task_swarm_guard.check_inv9_tasks_md_diff(old_text, new_text)
                if decision == "deny":
                    _audit("PreToolUse", payload, "deny-INV-9", str(target))
                    return deny(msg)
            except OSError:
                pass

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
    if os.environ.get("SPECODE_GUARD", "").lower() == "off":
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
