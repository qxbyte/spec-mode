"""Tests for spec_log.py (0.10.0+) — write_event / replay / status / redact / disable."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def _log_file(home: Path, sid: str) -> Path:
    return home / ".specode" / "logs" / f"{sid}.jsonl"


def _read_log(home: Path, sid: str) -> list[dict]:
    p = _log_file(home, sid)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def test_write_event_creates_jsonl(run_script, fake_home, make_session_id):
    sid = make_session_id()
    cp = run_script("spec_log.py", "write-event", "--event", "test_evt",
                    "--session", sid, "--payload", '{"k":"v"}')
    assert cp.returncode == 0, cp.stderr
    log = _read_log(fake_home, sid)
    assert len(log) == 1
    assert log[0]["event"] == "test_evt"
    assert log[0]["payload"]["k"] == "v"
    assert "ts" in log[0]


def test_disabled_via_env_var(run_script, fake_home, make_session_id):
    sid = make_session_id()
    cp = run_script("spec_log.py", "write-event", "--event", "test_evt",
                    "--session", sid, "--payload", "{}",
                    extra_env={"SPECODE_LOG": "off"})
    assert cp.returncode == 0
    # File should NOT exist because logging is off
    assert not _log_file(fake_home, sid).exists()


def test_disabled_via_config(run_script, fake_home, make_session_id):
    sid = make_session_id()
    config_dir = fake_home / ".config" / "specode"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(
        json.dumps({"logging": False}), encoding="utf-8")
    cp = run_script("spec_log.py", "write-event", "--event", "test_evt",
                    "--session", sid, "--payload", "{}")
    assert cp.returncode == 0
    assert not _log_file(fake_home, sid).exists()


def test_redact_default_keys(run_script, fake_home, make_session_id):
    sid = make_session_id()
    payload = json.dumps({
        "api_key": "sk-secret-123",
        "password": "hunter2",
        "harmless": "ok",
    })
    cp = run_script("spec_log.py", "write-event", "--event", "test_evt",
                    "--session", sid, "--payload", payload)
    assert cp.returncode == 0
    log = _read_log(fake_home, sid)
    p = log[0]["payload"]
    assert p["api_key"] == "<redacted>"
    assert p["password"] == "<redacted>"
    assert p["harmless"] == "ok"


def test_redact_extended_via_config(run_script, fake_home, make_session_id):
    sid = make_session_id()
    config_dir = fake_home / ".config" / "specode"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(
        json.dumps({"redact_keys": ["custom_key"]}), encoding="utf-8")
    payload = json.dumps({"custom_key": "leaked", "ok": "fine"})
    cp = run_script("spec_log.py", "write-event", "--event", "test_evt",
                    "--session", sid, "--payload", payload)
    assert cp.returncode == 0
    log = _read_log(fake_home, sid)
    p = log[0]["payload"]
    assert p["custom_key"] == "<redacted>"
    assert p["ok"] == "fine"


def test_truncate_long_string(run_script, fake_home, make_session_id):
    sid = make_session_id()
    long_str = "x" * 1000
    payload = json.dumps({"big": long_str})
    cp = run_script("spec_log.py", "write-event", "--event", "test_evt",
                    "--session", sid, "--payload", payload)
    assert cp.returncode == 0
    log = _read_log(fake_home, sid)
    big = log[0]["payload"]["big"]
    assert len(big) < 1000
    assert big.endswith("...<truncated>")


def test_replay_outputs_events(run_script, fake_home, make_session_id):
    sid = make_session_id()
    for i in range(3):
        run_script("spec_log.py", "write-event", "--event", f"evt_{i}",
                   "--session", sid, "--payload", "{}")
    cp = run_script("spec_log.py", "replay", "--session", sid)
    assert cp.returncode == 0, cp.stderr
    assert "evt_0" in cp.stdout
    assert "evt_1" in cp.stdout
    assert "evt_2" in cp.stdout


def test_replay_missing_session(run_script, fake_home, make_session_id):
    sid = make_session_id()
    cp = run_script("spec_log.py", "replay", "--session", sid)
    assert cp.returncode == 3
    assert "no log for session" in cp.stderr


def test_status_reports_empty_when_no_logs(run_script, fake_home):
    cp = run_script("spec_log.py", "status")
    assert cp.returncode == 0
    info = json.loads(cp.stdout)
    assert info["enabled"] is True  # default
    assert info["exists"] is False or info.get("session_log_files", 0) == 0


def test_status_reports_after_writes(run_script, fake_home, make_session_id):
    sid = make_session_id()
    run_script("spec_log.py", "write-event", "--event", "evt",
               "--session", sid, "--payload", "{}")
    cp = run_script("spec_log.py", "status")
    assert cp.returncode == 0
    info = json.loads(cp.stdout)
    assert info["enabled"] is True
    assert info["session_log_files"] >= 1
    assert info["total_bytes"] > 0


def test_status_reflects_env_disabled(run_script, fake_home):
    cp = run_script("spec_log.py", "status", extra_env={"SPECODE_LOG": "off"})
    assert cp.returncode == 0
    info = json.loads(cp.stdout)
    assert info["enabled"] is False
    assert "env:SPECODE_LOG=off" in info["switch_source"]


def test_hook_invocation_writes_log(run_script, fake_home, make_session_id):
    """spec_session.py hook 触发时应在 _safe_hook 里写一条 hook_invoked event."""
    sid = make_session_id()
    cp = run_script("spec_session.py", "on-session-start",
                    stdin=json.dumps({"session_id": sid}))
    assert cp.returncode == 0
    # hook_invoked event 是 session_id=None（_safe_hook 不传 sid），落 _orphan.jsonl
    orphan = _log_file(fake_home, "_orphan")
    assert orphan.exists(), "hook_invoked should land in _orphan.jsonl"
    events = _read_log(fake_home, "_orphan")
    assert any(e["event"] == "hook_invoked" for e in events)


def test_cli_call_writes_log(run_script, fake_home, doc_root, make_session_id):
    """spec_session.py 业务命令 main() 应记 cli_call + cli_exit."""
    sid = make_session_id()
    # 先 init 一个 spec 让后续命令有 spec_dir 可用
    cp = run_script(
        "spec_init.py",
        "--name", "logtest", "--requirement-name", "Log Test",
        "--source-text", "test", "--session", sid,
    )
    assert cp.returncode == 0
    # spec_init 应有 cli_call + cli_exit
    events = _read_log(fake_home, sid)
    assert any(e["event"] == "cli_call" and e["payload"].get("script") == "spec_init.py" for e in events)
    assert any(e["event"] == "cli_exit" and e["payload"].get("script") == "spec_init.py" for e in events)
