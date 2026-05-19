"""tests for task_swarm_state.py — phase 状态机 + 死循环检测。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path("/Users/xueqiang/Git/specode/plugins/specode/scripts")
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from task_swarm_state import (  # noqa: E402
    StateMachine, StageEntry, DEADLOOP_THRESHOLD,
)


def _make_sm(tmp_path: Path, num_stages: int = 2) -> StateMachine:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    stages = [
        StageEntry(number=i, title=f"S{i}", writes=[f"f{i}.py"], items=[
            {"number": f"{i}.1", "title": "t", "writes": [f"f{i}.py"], "reads": [],
             "depends_on": [], "requirements": [str(i) + ".1"], "raw_line": "",
             "checkbox": " ", "line_no": 0},
        ])
        for i in range(1, num_stages + 1)
    ]
    sm = StateMachine(
        run_id="rid-1", tasks_md=str(tmp_path / "tasks.md"),
        run_dir=str(run_dir),
        groups=[[s] for s in stages],
        group_status=["pending"] * num_stages,
    )
    sm.save()
    return sm


def test_save_and_load_roundtrip(tmp_path):
    sm = _make_sm(tmp_path)
    sm2 = StateMachine.load(Path(sm.run_dir))
    assert sm2.run_id == sm.run_id
    assert len(sm2.groups) == len(sm.groups)
    assert sm2.groups[0][0].number == 1


def test_load_migrates_legacy_claude_session_id(tmp_path):
    """老 state.json 字段名是 claude_session_id；StateMachine.load 应回填到 session_id 字段。"""
    import json
    sm = _make_sm(tmp_path)
    state_path = StateMachine.state_path(Path(sm.run_dir))
    data = json.loads(state_path.read_text())
    # 模拟老 state.json：删除新 key，回退到老 key
    data.pop("session_id", None)
    data["claude_session_id"] = "legacy-sess-xyz"
    state_path.write_text(json.dumps(data), encoding="utf-8")
    sm2 = StateMachine.load(Path(sm.run_dir))
    assert sm2.session_id == "legacy-sess-xyz"


def test_begin_coding_sets_in_flight(tmp_path):
    sm = _make_sm(tmp_path)
    sm.begin_coding()
    assert sm.phase == "coding"
    assert sm.round == 1
    assert sm.coder_in_flight == ["coder-g1-s1-r1"]


def test_mark_coder_done(tmp_path):
    sm = _make_sm(tmp_path)
    sm.begin_coding()
    sm.mark_coder_done("coder-g1-s1-r1")
    assert sm.coder_in_flight == []
    assert sm.coder_done == ["coder-g1-s1-r1"]
    assert sm.all_coders_returned()


def test_begin_review_then_validation(tmp_path):
    sm = _make_sm(tmp_path)
    sm.begin_coding()
    sm.mark_coder_done("coder-g1-s1-r1")
    sm.begin_review()
    assert sm.phase == "review"
    sm.mark_reviewer_done()
    sm.begin_validation()
    assert sm.phase == "validation"


def test_p0_fix_phase(tmp_path):
    sm = _make_sm(tmp_path)
    sm.begin_coding()
    sm.mark_coder_done("coder-g1-s1-r1")
    sm.begin_review()
    sm.mark_reviewer_done()
    pending = [{"text": "x", "evidence_tags": ["req:1.1"], "file_hint": "f1.py"},
               {"text": "y", "evidence_tags": ["security"], "file_hint": "f2.py"}]
    sm.begin_p0_fix(pending)
    assert sm.phase == "p0-fix"
    assert len(sm.p0_in_flight) == 2  # 2 unique files


def test_v_fix_phase_round_increment(tmp_path):
    sm = _make_sm(tmp_path)
    sm.begin_coding()
    sm.mark_coder_done("coder-g1-s1-r1")
    sm.begin_review()
    sm.mark_reviewer_done()
    sm.begin_validation()
    sm.mark_validator_done()
    sm.record_round_signature("fail_sig_1")
    fix_targets = [{"file_path": "f1.py"}]
    initial_round = sm.round
    sm.begin_v_fix(fix_targets)
    assert sm.phase == "v-fix"
    assert sm.round == initial_round + 1
    assert len(sm.vfix_in_flight) == 1


def test_deadloop_threshold(tmp_path):
    sm = _make_sm(tmp_path)
    sm.begin_coding()
    sm.mark_coder_done("coder-g1-s1-r1")
    sm.begin_review()
    sm.mark_reviewer_done()
    # Simulate DEADLOOP_THRESHOLD identical fail rounds
    for i in range(DEADLOOP_THRESHOLD):
        sm.round = i + 1
        sm.record_round_signature("samesig")
    assert sm.detect_deadloop()


def test_deadloop_not_triggered_with_different_sigs(tmp_path):
    sm = _make_sm(tmp_path)
    sm.begin_coding()
    sm.mark_coder_done("coder-g1-s1-r1")
    sm.begin_review()
    sm.mark_reviewer_done()
    for i, sig in enumerate(["a", "b", "c"]):
        sm.round = i + 1
        sm.record_round_signature(sig)
    assert not sm.detect_deadloop()


def test_deadloop_below_threshold(tmp_path):
    sm = _make_sm(tmp_path)
    sm.begin_coding()
    sm.mark_coder_done("coder-g1-s1-r1")
    sm.begin_review()
    sm.mark_reviewer_done()
    for i in range(DEADLOOP_THRESHOLD - 1):
        sm.round = i + 1
        sm.record_round_signature("samesig")
    assert not sm.detect_deadloop()


def test_finalize_group_advances_index(tmp_path):
    sm = _make_sm(tmp_path, num_stages=2)
    sm.begin_coding()
    sm.mark_coder_done("coder-g1-s1-r1")
    sm.finalize_group("done")
    assert sm.current_group_index == 1
    assert sm.group_status[0] == "done"


def test_finalize_last_group_marks_done(tmp_path):
    sm = _make_sm(tmp_path, num_stages=1)
    sm.begin_coding()
    sm.mark_coder_done("coder-g1-s1-r1")
    sm.finalize_group("done")
    assert sm.phase == "done"
    assert sm.failed_status == "done"
    assert sm.completed_at is not None


def test_fail_group_deadloop_sets_failed_status(tmp_path):
    sm = _make_sm(tmp_path)
    sm.begin_coding()
    sm.fail_group_deadloop()
    assert sm.failed_status == "failed-deadloop"
    assert sm.group_status[0] == "failed-deadloop"
    assert sm.phase == "error"


def test_events_append_timestamps(tmp_path):
    sm = _make_sm(tmp_path)
    sm.events_append({"type": "test"})
    assert len(sm.events) == 1
    assert "at" in sm.events[0]


def test_state_save_atomic_then_load(tmp_path):
    sm = _make_sm(tmp_path)
    sm.round = 7
    sm.save()
    sm2 = StateMachine.load(Path(sm.run_dir))
    assert sm2.round == 7
