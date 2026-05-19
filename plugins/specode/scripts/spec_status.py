#!/usr/bin/env python3
"""spec_status.py — `/specode:status` 命令入口。

读 ~/.specode/sessions/<session>.json + active spec 的 .config.json，
输出可读摘要（人类友好 + JSON 数据块）。

用法：
  spec_status.py --session <id>

stdlib-only。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from spec_session import read_session, read_spec_config, _session_short, _is_lock_stale  # type: ignore  # noqa: E402

# 0.10.0+ 日志（defensive import）
try:
    from spec_log import write_event as _log_event  # type: ignore
except Exception:
    def _log_event(event: str, payload: Optional[dict] = None,
                   session_id: Optional[str] = None) -> None:
        return None


CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[(.)\]\s+", re.MULTILINE)


def _count_tasks(tasks_md: Optional[str]) -> dict:
    if not tasks_md:
        return {"total": 0, "done": 0, "in_progress": 0, "pending": 0}
    total = done = in_prog = pending = 0
    for m in CHECKBOX_RE.finditer(tasks_md):
        ch = m.group(1).strip().lower()
        total += 1
        if ch == "x":
            done += 1
        elif ch == "~":
            in_prog += 1
        else:
            pending += 1
    return {"total": total, "done": done, "in_progress": in_prog, "pending": pending}


def _read_text(p: Path) -> Optional[str]:
    try:
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="spec_status.py", description="show specode session/spec status")
    parser.add_argument("--session", required=True, help="会话 id（宿主注入的 session_id）")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = parser.parse_args(argv)

    sess = read_session(args.session)
    if sess is None:
        msg = {
            "ok": False,
            "reason": "session_not_found",
            "session_id": args.session,
        }
        sys.stdout.write(json.dumps(msg, ensure_ascii=False, indent=2) + "\n")
        return 0

    spec_dir_str = sess.get("active_spec_dir")
    cfg = None
    task_counts = None
    lock_state_detail = None
    if spec_dir_str:
        try:
            cfg = read_spec_config(Path(spec_dir_str))
        except Exception:
            cfg = None
        tasks_md = _read_text(Path(spec_dir_str) / "tasks.md") if spec_dir_str else None
        task_counts = _count_tasks(tasks_md)
        if cfg:
            lock = cfg.get("lock") or {}
            holder = lock.get("holder")
            if not holder:
                lock_state_detail = "released"
            elif holder == args.session:
                lock_state_detail = "ok (held by current session)"
            elif _is_lock_stale(lock):
                lock_state_detail = f"stale (holder={_session_short(holder)})"
            else:
                lock_state_detail = f"held by other (holder={_session_short(holder)})"

    payload = {
        "ok": True,
        "session": sess,
        "spec_config": cfg,
        "task_counts": task_counts,
        "lock_state_detail": lock_state_detail,
    }
    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return 0

    # 可读摘要
    lines: list[str] = []
    lines.append("=== specode status ===")
    sid_for_show = sess.get("session_id") or sess.get("claude_session_id")
    lines.append(f"session_id     : {sid_for_show}")
    lines.append(f"session(short) : {_session_short(sid_for_show)}")
    lines.append(f"mode           : {sess.get('mode')}")
    lines.append(f"started_at     : {sess.get('started_at')}")
    lines.append(f"last_activity  : {sess.get('last_activity_at')}")
    if sess.get("ended_at"):
        lines.append(f"ended_at       : {sess.get('ended_at')}")
    lines.append(f"active spec    : {sess.get('active_spec_slug') or '(none)'}")
    lines.append(f"spec_dir       : {spec_dir_str or '(none)'}")
    lines.append(f"phase          : {sess.get('phase') or '(none)'}")
    lines.append(f"pending_select : {sess.get('pending_selector') or '(none)'}")
    lines.append(f"lock_state     : {sess.get('lock_state')}")
    if lock_state_detail:
        lines.append(f"lock_detail    : {lock_state_detail}")
    if task_counts:
        lines.append(
            f"tasks          : total={task_counts['total']} done={task_counts['done']} "
            f"in_progress={task_counts['in_progress']} pending={task_counts['pending']}"
        )
    if sess.get("task_swarm_run_id"):
        lines.append(f"task_swarm_run : {sess.get('task_swarm_run_id')}")

    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _log_wrap_main(argv: Optional[list[str]] = None) -> int:
    import contextlib as _cl
    argv_list = list(sys.argv[1:]) if argv is None else list(argv)
    sid = None
    for i, a in enumerate(argv_list):
        if a == "--session" and i + 1 < len(argv_list):
            sid = argv_list[i + 1]
            break
    with _cl.suppress(Exception):
        _log_event("cli_call", {"script": "spec_status.py", "argv_len": len(argv_list)}, session_id=sid)
    rc = main(argv)
    with _cl.suppress(Exception):
        _log_event("cli_exit", {"script": "spec_status.py", "exit_code": rc}, session_id=sid)
    return rc


if __name__ == "__main__":
    try:
        sys.exit(_log_wrap_main())
    except KeyboardInterrupt:
        sys.exit(130)
