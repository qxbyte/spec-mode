"""Audit log rotation + reader CLI tests."""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import spec_guard
import spec_state


def _reset_audit(tmp_path: Path, max_bytes: int) -> Path:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    spec_guard.AUDIT_DIR = audit_dir
    spec_guard.AUDIT_MAX_BYTES = max_bytes
    spec_guard._truncate_checked = False
    spec_state.AUDIT_DIR = audit_dir
    return audit_dir


def test_truncate_keeps_tail_when_over_cap(tmp_path):
    audit_dir = _reset_audit(tmp_path, max_bytes=4096)
    today = spec_guard.datetime.now(spec_guard.timezone.utc).strftime("%Y-%m-%d")
    log_file = audit_dir / f"{today}.log"

    line = json.dumps({"ts": "x", "event": "Pad", "decision": "ok"}) + "\n"
    with log_file.open("w", encoding="utf-8") as f:
        for _ in range(500):
            f.write(line)
    pre_size = log_file.stat().st_size
    assert pre_size > 4096

    spec_guard._audit("SessionStart", {"session_id": "s1"}, "ok", "after-truncate")

    post_size = log_file.stat().st_size
    assert post_size <= 4096 + 1024
    contents = log_file.read_text(encoding="utf-8").splitlines()
    assert any('"event": "_truncate"' in line or '"event":"_truncate"' in line for line in contents)
    assert any('"decision": "ok"' in line and "after-truncate" in line for line in contents)


def test_no_truncate_under_cap(tmp_path):
    audit_dir = _reset_audit(tmp_path, max_bytes=1024 * 1024)
    spec_guard._audit("SessionStart", {"session_id": "s1"}, "ok", "small")
    spec_guard._audit("Stop", {"session_id": "s1"}, "ok-conserved", "small")
    today = spec_guard.datetime.now(spec_guard.timezone.utc).strftime("%Y-%m-%d")
    contents = (audit_dir / f"{today}.log").read_text(encoding="utf-8")
    assert "_truncate" not in contents
    assert contents.count("\n") == 2


def test_truncate_runs_only_once_per_process(tmp_path):
    audit_dir = _reset_audit(tmp_path, max_bytes=2048)
    today = spec_guard.datetime.now(spec_guard.timezone.utc).strftime("%Y-%m-%d")
    log_file = audit_dir / f"{today}.log"
    line = json.dumps({"ts": "x", "event": "Pad", "decision": "ok"}) + "\n"
    with log_file.open("w", encoding="utf-8") as f:
        for _ in range(500):
            f.write(line)

    spec_guard._audit("E1", {}, "ok", "first")
    after_first = log_file.read_text(encoding="utf-8")
    truncate_markers_first = after_first.count('"event": "_truncate"')

    # Pad again past the cap; truncation should NOT fire again (one-shot per process).
    with log_file.open("a", encoding="utf-8") as f:
        for _ in range(500):
            f.write(line)
    spec_guard._audit("E2", {}, "ok", "second")
    after_second = log_file.read_text(encoding="utf-8")
    assert after_second.count('"event": "_truncate"') == truncate_markers_first
    assert log_file.stat().st_size > 2048  # grew past cap, deliberately not re-truncated


def test_audit_tail_pretty_and_json(tmp_path):
    _reset_audit(tmp_path, max_bytes=1024 * 1024)
    spec_guard._audit("UserPromptSubmit", {"session_id": "s1", "tool_name": "Edit"}, "injected", "slug-a")
    spec_guard._audit("Stop", {"session_id": "s1"}, "deny-INV-2", "")

    today = spec_guard.datetime.now(spec_guard.timezone.utc).strftime("%Y-%m-%d")
    import argparse
    ns_pretty = argparse.Namespace(n=10, date=today, follow=False, json=False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        spec_state._cmd_audit_tail(ns_pretty)
    out = buf.getvalue()
    assert "UserPromptSubmit" in out and "deny-INV-2" in out and "slug-a" in out

    ns_json = argparse.Namespace(n=10, date=today, follow=False, json=True)
    buf = io.StringIO()
    with redirect_stdout(buf):
        spec_state._cmd_audit_tail(ns_json)
    json_lines = [json.loads(l) for l in buf.getvalue().splitlines() if l.strip()]
    assert [r["event"] for r in json_lines] == ["UserPromptSubmit", "Stop"]


def test_audit_summary_counts_and_denies(tmp_path):
    _reset_audit(tmp_path, max_bytes=1024 * 1024)
    spec_guard._audit("PreToolUse", {"session_id": "s1", "tool_name": "Edit"}, "ok-code-allowed", "a.py")
    spec_guard._audit("PreToolUse", {"session_id": "s1", "tool_name": "Edit"}, "deny-INV-1", "b.py")
    spec_guard._audit("Stop", {"session_id": "s1"}, "ok-conserved", "")

    import argparse
    ns = argparse.Namespace(days=0, show_deny=5)
    buf = io.StringIO()
    with redirect_stdout(buf):
        spec_state._cmd_audit_summary(ns)
    out = buf.getvalue()
    assert "3 records" in out
    assert "PreToolUse" in out and "Stop" in out
    assert "deny-INV-1" in out
    assert "b.py" in out  # surfaced via "recent denies"
