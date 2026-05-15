#!/usr/bin/env python3
"""Spec-mode status: thin wrapper around `spec_session.py load --json`.

Historically this script duplicated TASK_RE / LABELS / task_section. Per the
P2 refactor it now delegates to `spec_session.py load --json`, parses the
JSON output, and renders the task-progress view. All shared regex/label
definitions live in `spec_session`.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import spec_session
from spec_session import TASK_RE, TASK_LABELS, task_section


SCRIPT_DIR = Path(__file__).resolve().parent


def _run_load(spec_dir: Path, session_id: str) -> dict:
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "spec_session.py"),
        "load",
        str(spec_dir),
        "--session",
        session_id,
        "--json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(proc.returncode)
    return json.loads(proc.stdout)


def _collect_tasks(spec_dir: Path) -> tuple[dict[str, int], list[dict[str, str]]]:
    """Read tasks.md once locally for the task-list view. spec_session.load
    only returns counts; we need title-level data here for the table output."""
    counts = {label: 0 for label in TASK_LABELS.values()}
    tasks: list[dict[str, str]] = []
    tasks_path = spec_dir / "tasks.md"
    if not tasks_path.exists():
        return counts, tasks
    text = task_section(tasks_path.read_text(encoding="utf-8"))
    for match in TASK_RE.finditer(text):
        marker = match.group(1)
        title = match.group(2).strip()
        status = TASK_LABELS[marker]
        counts[status] += 1
        tasks.append({"status": status, "title": title})
    return counts, tasks


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize spec-mode status.")
    parser.add_argument("spec_dir", type=Path, nargs="?")
    parser.add_argument("--root", help="Document root. Required when spec_dir is omitted.")
    parser.add_argument("--session", help="Window/thread/session id. Defaults to $TERM_SESSION_ID or 'default'.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    session_id = spec_session.normalize_session_id(args.session)
    if args.spec_dir:
        spec_dir = args.spec_dir.expanduser().resolve()
    else:
        if not args.root:
            raise SystemExit("status without spec_dir requires --root")
        spec_dir, _config, _entry = spec_session.resolve_active(
            Path(args.root).expanduser().resolve(),
            session_id,
        )

    load_data = _run_load(spec_dir, session_id)
    counts, tasks = _collect_tasks(spec_dir)

    lock = load_data.get("lock")
    result = {
        "specDir": load_data["specDir"],
        "requirementName": load_data.get("requirementName"),
        "specId": load_data.get("specId"),
        "sessionId": session_id,
        "currentPhase": load_data.get("currentPhase"),
        "iterationRound": load_data.get("iterationRound"),
        "sessionStatus": load_data.get("sessionStatus"),
        "lock": lock,
        "lockHeldBy": (lock or {}).get("sessionId"),
        "lockOwnedByCurrentSession": load_data.get("lockOwnedByCurrentSession", False),
        "checklistStale": load_data.get("checklistStale", False),
        "counts": counts,
        "tasks": tasks,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"Spec: {result['requirementName'] or Path(spec_dir).name}")
    print(f"Path: {spec_dir}")
    print(f"Session: {session_id}")
    print(f"Phase: {result['currentPhase'] or 'unknown'}")
    if result["iterationRound"]:
        print(f"Iteration round: {result['iterationRound']}")
    if lock:
        owner = "本会话" if result["lockOwnedByCurrentSession"] else f"其他: {result['lockHeldBy']}"
        print(f"Lock: {owner}")
    else:
        print("Lock: 空闲")
    if result["checklistStale"]:
        print("⚠ acceptance-checklist.md 落后于 requirements.md，请在同 turn 重写")
    c = result["counts"]
    print(
        "Tasks: "
        f"{c['completed']} completed, {c['in_progress']} in progress, "
        f"{c['pending']} pending, {c['optional']} optional, {c['skipped']} skipped"
    )
    if tasks:
        print()
        for task in tasks:
            marker = {"pending": "[ ]", "completed": "[x]", "in_progress": "[~]", "optional": "[*]", "skipped": "[-]"}[task["status"]]
            print(f"  {marker} {task['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
