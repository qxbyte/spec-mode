"""Tests for spec_telemetry: opt-in gate, emit, rotation, summary."""
from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import spec_state
import spec_telemetry


def _telemetry_path(tmp_path: Path, monkeypatch) -> Path:
    p = tmp_path / "telemetry.jsonl"
    monkeypatch.setenv(spec_telemetry._ENV_PATH, str(p))
    return p


def test_opt_out_is_noop(tmp_path, monkeypatch):
    """Without SPECODE_TELEMETRY=on, emit writes nothing."""
    p = _telemetry_path(tmp_path, monkeypatch)
    monkeypatch.delenv(spec_telemetry._ENV_FLAG, raising=False)
    spec_telemetry.emit("test.event", k="v")
    assert not p.exists()


def test_opt_in_emits_record(tmp_path, monkeypatch):
    p = _telemetry_path(tmp_path, monkeypatch)
    monkeypatch.setenv(spec_telemetry._ENV_FLAG, "on")
    spec_telemetry.emit("spec.init", spec_slug="foo", workflow="requirements-first")

    assert p.exists()
    lines = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    rec = lines[0]
    assert rec["event"] == "spec.init"
    assert rec["spec_slug"] == "foo"
    assert rec["workflow"] == "requirements-first"
    assert "ts" in rec


def test_flag_truthy_values(tmp_path, monkeypatch):
    p = _telemetry_path(tmp_path, monkeypatch)
    for val in ["on", "1", "true", "YES", "Y"]:
        monkeypatch.setenv(spec_telemetry._ENV_FLAG, val)
        assert spec_telemetry.is_enabled(), f"{val!r} should enable telemetry"
    for val in ["off", "0", "false", "no", ""]:
        monkeypatch.setenv(spec_telemetry._ENV_FLAG, val)
        assert not spec_telemetry.is_enabled(), f"{val!r} should keep telemetry off"


def test_rotation_when_over_cap(tmp_path, monkeypatch):
    p = _telemetry_path(tmp_path, monkeypatch)
    monkeypatch.setenv(spec_telemetry._ENV_FLAG, "on")
    monkeypatch.setenv(spec_telemetry._ENV_MAX, "512")

    # Fill past the cap.
    pad = "x" * 60
    for i in range(20):
        spec_telemetry.emit("pad", i=i, junk=pad)
    assert p.exists()

    # One more emit triggers rotation since file now > cap.
    spec_telemetry.emit("spec.init", spec_slug="after-rotate")

    rotated = spec_telemetry._rotated_for(p)
    assert rotated.exists(), "rotated .0 file should exist"
    # New file contains only the post-rotation record.
    post = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(post) == 1
    assert post[0]["spec_slug"] == "after-rotate"


def test_rotation_overwrites_prior_rotated(tmp_path, monkeypatch):
    p = _telemetry_path(tmp_path, monkeypatch)
    rotated = spec_telemetry._rotated_for(p)
    monkeypatch.setenv(spec_telemetry._ENV_FLAG, "on")
    monkeypatch.setenv(spec_telemetry._ENV_MAX, "256")

    # Prime an old .0 file with stale content.
    rotated.write_text(json.dumps({"event": "stale"}) + "\n", encoding="utf-8")
    pad = "x" * 60
    for i in range(20):
        spec_telemetry.emit("pad", i=i, junk=pad)
    spec_telemetry.emit("trigger.rotate")

    # The new .0 should have replaced the stale one.
    rotated_records = [
        json.loads(l) for l in rotated.read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    assert all(r.get("event") != "stale" for r in rotated_records)


def test_iter_records_reads_rotated_first(tmp_path, monkeypatch):
    p = _telemetry_path(tmp_path, monkeypatch)
    rotated = spec_telemetry._rotated_for(p)
    rotated.write_text(json.dumps({"event": "old", "n": 1}) + "\n", encoding="utf-8")
    p.write_text(json.dumps({"event": "new", "n": 2}) + "\n", encoding="utf-8")
    seq = [r["event"] for r in spec_telemetry.iter_records(p)]
    assert seq == ["old", "new"]


def test_emit_swallows_io_errors(tmp_path, monkeypatch):
    """If the target path can't be written, emit must not raise."""
    bogus = tmp_path / "nonexistent" / "blocker" / "telemetry.jsonl"
    monkeypatch.setenv(spec_telemetry._ENV_PATH, str(bogus))
    monkeypatch.setenv(spec_telemetry._ENV_FLAG, "on")
    # Create a regular file where a dir is expected — parent.mkdir will fail.
    (tmp_path / "nonexistent").write_text("not a dir")
    spec_telemetry.emit("event.that.fails")
    # No assertion needed beyond "did not raise".


def test_iter_records_skips_malformed_lines(tmp_path, monkeypatch):
    p = _telemetry_path(tmp_path, monkeypatch)
    p.write_text(
        json.dumps({"event": "ok1"}) + "\n"
        + "not-json-at-all\n"
        + json.dumps({"event": "ok2"}) + "\n",
        encoding="utf-8",
    )
    seq = [r["event"] for r in spec_telemetry.iter_records(p)]
    assert seq == ["ok1", "ok2"]


def _run_summary(tmp_path, monkeypatch, **kwargs) -> tuple[str, str, int]:
    p = _telemetry_path(tmp_path, monkeypatch)
    if "records" in kwargs:
        for rec in kwargs["records"]:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
    ns = argparse.Namespace(
        days=kwargs.get("days", 0),
        json=kwargs.get("json", False),
        force=kwargs.get("force", True),
    )
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = spec_state._cmd_telemetry_summary(ns)
    return out.getvalue(), err.getvalue(), rc


def test_summary_text_output(tmp_path, monkeypatch):
    monkeypatch.setenv(spec_telemetry._ENV_FLAG, "on")
    records = [
        {"ts": "2026-05-01T00:00:00+00:00", "event": "spec.init", "spec_slug": "alpha"},
        {"ts": "2026-05-01T01:00:00+00:00", "event": "spec.phase_transition",
         "spec_slug": "alpha", "from_phase": "intake", "to_phase": "requirements"},
        {"ts": "2026-05-01T02:00:00+00:00", "event": "inv.violation",
         "inv": "INV-1", "spec_slug": "alpha"},
        {"ts": "2026-05-01T03:00:00+00:00", "event": "inv.violation",
         "inv": "INV-2", "spec_slug": "alpha"},
        {"ts": "2026-05-01T04:00:00+00:00", "event": "inv.violation",
         "inv": "INV-1", "spec_slug": "beta"},
        {"ts": "2026-05-01T05:00:00+00:00", "event": "swarm.run_start", "run_id": "r1"},
        {"ts": "2026-05-01T06:00:00+00:00", "event": "swarm.stage_done",
         "run_id": "r1", "stage": 1, "rounds": {"coder": 2, "reviewer": 2, "validator": 1}},
        {"ts": "2026-05-01T07:00:00+00:00", "event": "swarm.run_end",
         "run_id": "r1", "converged": 1, "failed": 0},
    ]
    out, err, rc = _run_summary(tmp_path, monkeypatch, records=records)
    assert rc == 0
    assert "8 record" in out
    assert "INV-1" in out and "INV-2" in out
    assert "alpha" in out and "beta" in out
    assert "task-swarm: 1 run" in out
    # 2+2+1 = 5 rounds across one stage
    assert "5.00" in out


def test_summary_json_output(tmp_path, monkeypatch):
    monkeypatch.setenv(spec_telemetry._ENV_FLAG, "on")
    records = [
        {"ts": "2026-05-01T00:00:00+00:00", "event": "inv.violation", "inv": "INV-1", "spec_slug": "x"},
        {"ts": "2026-05-01T01:00:00+00:00", "event": "inv.violation", "inv": "INV-1", "spec_slug": "x"},
        {"ts": "2026-05-01T02:00:00+00:00", "event": "inv.violation", "inv": "INV-2", "spec_slug": "y"},
    ]
    out, _, rc = _run_summary(tmp_path, monkeypatch, records=records, json=True)
    assert rc == 0
    data = json.loads(out)
    assert data["total_records"] == 3
    assert data["by_inv"] == {"INV-1": 2, "INV-2": 1}
    assert data["inv_violations_by_spec"] == {"x": 2, "y": 1}


def test_summary_warns_when_disabled(tmp_path, monkeypatch):
    p = _telemetry_path(tmp_path, monkeypatch)
    p.write_text(json.dumps({"ts": "2026-05-01T00:00:00+00:00", "event": "x"}) + "\n", encoding="utf-8")
    monkeypatch.delenv(spec_telemetry._ENV_FLAG, raising=False)
    ns = argparse.Namespace(days=0, json=False, force=False)
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = spec_state._cmd_telemetry_summary(ns)
    assert rc == 0
    assert "telemetry is disabled" in err.getvalue()
    # Still prints the existing file's contents.
    assert "1 record" in out.getvalue()


def test_summary_no_file(tmp_path, monkeypatch):
    _telemetry_path(tmp_path, monkeypatch)
    monkeypatch.setenv(spec_telemetry._ENV_FLAG, "on")
    ns = argparse.Namespace(days=0, json=False, force=False)
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = spec_state._cmd_telemetry_summary(ns)
    assert rc == 0
    assert "no telemetry file" in err.getvalue()


def test_emit_integration_from_spec_init(tmp_path, monkeypatch):
    """spec_init emits a spec.init record (smoke-level integration)."""
    p = _telemetry_path(tmp_path, monkeypatch)
    monkeypatch.setenv(spec_telemetry._ENV_FLAG, "on")
    spec_telemetry.emit(
        "spec.init",
        spec_slug="demo",
        spec_dir=str(tmp_path / "demo"),
        workflow="requirements-first",
        spec_type="feature",
        persistent=False,
        initial_phase=None,
        created_count=4,
    )
    records = [r for r in spec_telemetry.iter_records(p)]
    assert any(r["event"] == "spec.init" and r["spec_slug"] == "demo" for r in records)
