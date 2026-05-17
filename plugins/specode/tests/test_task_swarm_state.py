"""Unit tests for task_swarm_state state machine.

Covers three core scenarios from references/task-swarm.md:
  1. Two-stage sequential (stage 1 → checkpoint 2 → done)
  2. P0 reviewer loop (coder → reviewer P0 → fix-coder → reviewer approve → converged)
  3. Validator fail + loop-warning early termination
Plus: MAX_ROUNDS termination, parallelism, file-conflict serialization,
coder-only stage skips reviewer/validator.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import task_swarm_parse_md as P  # noqa: E402
import task_swarm_state as S  # noqa: E402


def make_state(
    text: str,
    parallel: int = 3,
    max_rounds: int = 3,
    reviewer_rounds: int | None = None,
    validator_rounds: int | None = None,
) -> dict:
    plan = P.parse_tasks_md(text).to_dict()
    return S.build_initial_state(
        run_id="test-run",
        tasks_path=Path("/tmp/tasks.md"),
        spec_dir=Path("/tmp/spec"),
        project_root=Path("/tmp/proj"),
        plan=plan,
        parallel=parallel,
        max_rounds=max_rounds,
        reviewer_max_rounds=reviewer_rounds,
        validator_max_rounds=validator_rounds,
    )


# ---------- scenario 1: two-stage sequential ----------

TWO_STAGE = """\
- [ ] 1. 实现登录
  - [ ] 1.1 写 model
    - 文件：src/m.py
    - _需求：1.1_

- [ ] 2. 检查点
  - 运行 pytest
"""


def test_two_stage_sequential_happy_path():
    state = make_state(TWO_STAGE)
    # First action: fork stage 1 coder
    a = S.next_action(state)
    assert a.kind == "fork"
    assert a.payload["stage"] == 1
    assert a.payload["role"] == "coder"
    assert a.payload["round"] == 1
    S.mark_in_flight(state, 1, "coder", 1)

    # Coder finishes ok → reviewer
    S.advance(state, 1, "coder", 1, "ok")
    a = S.next_action(state)
    assert a.kind == "fork"
    assert (a.payload["stage"], a.payload["role"]) == (1, "reviewer")
    S.mark_in_flight(state, 1, "reviewer", 1)

    # Reviewer approves → stage 1 converges → writeback before further forks
    S.advance(state, 1, "reviewer", 1, "approved")
    a = S.next_action(state)
    assert a.kind == "writeback"
    assert a.payload["stage"] == 1
    S.mark_written_back(state, 1)

    # Now stage 2 (checkpoint) can run, deps satisfied
    a = S.next_action(state)
    assert a.kind == "fork"
    assert (a.payload["stage"], a.payload["role"]) == (2, "validator")
    S.mark_in_flight(state, 2, "validator", 1)

    S.advance(state, 2, "validator", 1, "pass")
    a = S.next_action(state)
    assert a.kind == "writeback"
    S.mark_written_back(state, 2)

    a = S.next_action(state)
    assert a.kind == "done"


def test_checkpoint_blocked_until_upstream_writeback():
    state = make_state(TWO_STAGE)
    # Try to dispatch stage 2 before stage 1 converges — should not be offered.
    S.mark_in_flight(state, 1, "coder", 1)
    S.advance(state, 1, "coder", 1, "ok")
    # reviewer not done yet — checkpoint must wait
    a = S.next_action(state)
    assert a.payload.get("stage") == 1  # not 2


# ---------- scenario 2: P0 reviewer loop ----------

SINGLE_STAGE = """\
- [ ] 1. T
  - [ ] 1.1 x
    - 文件：src/x.py
    - _需求：1.1_
"""


# R3: reviewer is now advisory — it never fails a stage or loops back to coder.

def test_reviewer_p0_is_advisory_stage_converges():
    """Even when reviewer reports P0, the stage converges; advisory annotation
    is the only side effect. No coder fix round is scheduled."""
    state = make_state(SINGLE_STAGE)
    S.mark_in_flight(state, 1, "coder", 1)
    S.advance(state, 1, "coder", 1, "ok")
    S.mark_in_flight(state, 1, "reviewer", 1)
    S.advance(state, 1, "reviewer", 1, "p0", extra={"p0_count": 3})

    stage = S.get_stage(state, 1)
    assert stage["phase"] == "converged"
    # And the next action is writeback (not another fork)
    a = S.next_action(state)
    assert a.kind == "writeback"


def test_reviewer_approved_converges_normal_stage():
    state = make_state(SINGLE_STAGE)
    S.advance(state, 1, "coder", 1, "ok")
    a = S.next_action(state)
    assert (a.payload["role"], a.payload["round"]) == ("reviewer", 1)
    S.advance(state, 1, "reviewer", 1, "approved")
    assert S.get_stage(state, 1)["phase"] == "converged"


def test_reviewer_loop_does_not_fail_stage():
    """A reviewer loop verdict no longer fails the stage — advisory only."""
    state = make_state(SINGLE_STAGE)
    S.advance(state, 1, "coder", 1, "ok")
    S.advance(state, 1, "reviewer", 1, "loop")
    stage = S.get_stage(state, 1)
    assert stage["phase"] == "converged"


# ---------- scenario 3: validator fail loop ----------

CHECKPOINT_ONLY = """\
- [ ] 1. 检查点
  - 运行 pytest
"""


def test_validator_fail_and_loop_warning():
    state = make_state(CHECKPOINT_ONLY)
    a = S.next_action(state)
    assert (a.payload["role"], a.payload["round"]) == ("validator", 1)
    S.mark_in_flight(state, 1, "validator", 1)
    S.advance(state, 1, "validator", 1, "loop")
    stage = S.get_stage(state, 1)
    assert stage["phase"] == "failed"


def test_validator_max_rounds():
    state = make_state(CHECKPOINT_ONLY, max_rounds=2)
    S.advance(state, 1, "validator", 1, "fail")
    S.advance(state, 1, "coder", 2, "ok")
    S.advance(state, 1, "validator", 2, "fail")
    stage = S.get_stage(state, 1)
    assert stage["phase"] == "failed"


def test_validator_recovers_within_budget():
    """R2: checkpoint must converge on validator pass, not reviewer.
    coder-fix → validator re-run (no reviewer post-fix in between).
    """
    state = make_state(CHECKPOINT_ONLY, max_rounds=3)
    S.advance(state, 1, "validator", 1, "fail")
    a = S.next_action(state)
    assert (a.payload["role"], a.payload["round"], a.payload["scope"]) == ("coder", 2, "validator-fail-fix")
    S.advance(state, 1, "coder", 2, "ok")
    # checkpoint coder fix → validator re-run directly (no reviewer)
    a = S.next_action(state)
    assert a.payload["role"] == "validator"
    assert a.payload["scope"] == "re-run"
    S.advance(state, 1, "validator", a.payload["round"], "pass")
    stage = S.get_stage(state, 1)
    assert stage["phase"] == "converged"


# ---------- coder-only stage skips reviewer ----------

CODER_ONLY_STAGE = """\
- [*] 5. 优化
  - [ ] 5.1 x @swarm:coder-only
    - 文件：src/x.py
"""


def test_coder_only_stage_no_reviewer():
    state = make_state(CODER_ONLY_STAGE)
    a = S.next_action(state)
    assert (a.payload["stage"], a.payload["role"]) == (5, "coder")
    S.advance(state, 5, "coder", 1, "ok")
    stage = S.get_stage(state, 5)
    assert stage["phase"] == "converged"
    a = S.next_action(state)
    assert a.kind == "writeback"
    assert a.payload["stage"] == 5


# ---------- parallelism ----------

TWO_INDEP = """\
- [ ] 1. A
  - [ ] 1.1 a
    - 文件：src/a.py
    - _需求：1.1_
- [ ] 3. B
  - [ ] 3.1 b
    - 文件：src/b.py
    - _需求：3.1_
"""


def test_parallel_dispatch_offers_both_stages():
    state = make_state(TWO_INDEP, parallel=2)
    # First action: fork stage 1
    a = S.next_action(state)
    assert a.payload["stage"] == 1
    S.mark_in_flight(state, 1, "coder", 1)
    # Second action with stage 1 still in flight: should offer stage 3
    a = S.next_action(state)
    assert a.kind == "fork"
    assert a.payload["stage"] == 3


def test_file_conflict_serializes():
    text = (
        "- [ ] 1. A\n  - [ ] 1.1 a\n    - 文件：src/shared.py\n    - _需求：1.1_\n"
        "- [ ] 3. B\n  - [ ] 3.1 b\n    - 文件：src/shared.py\n    - _需求：3.1_\n"
    )
    state = make_state(text, parallel=3)
    a = S.next_action(state)
    assert a.payload["stage"] == 1
    S.mark_in_flight(state, 1, "coder", 1)
    # stage 3 shares src/shared.py with in-flight stage 1 → must wait
    a = S.next_action(state)
    assert a.kind == "wait"


def test_parallel_cap_respected():
    text = (
        "- [ ] 1. A\n  - [ ] 1.1 a\n    - 文件：src/a.py\n    - _需求：1.1_\n"
        "- [ ] 3. B\n  - [ ] 3.1 b\n    - 文件：src/b.py\n    - _需求：3.1_\n"
        "- [ ] 5. C\n  - [ ] 5.1 c\n    - 文件：src/c.py\n    - _需求：5.1_\n"
    )
    state = make_state(text, parallel=2)
    a = S.next_action(state)
    S.mark_in_flight(state, a.payload["stage"], "coder", 1)
    a = S.next_action(state)
    S.mark_in_flight(state, a.payload["stage"], "coder", 1)
    # 2 in-flight, cap=2 → third stage blocked
    a = S.next_action(state)
    assert a.kind == "wait"


# ---------- skipped stages ----------

ALL_SKIP_STAGE = """\
- [ ] 1. 全跳过
  - [ ] 1.1 a @swarm:skip
    - 文件：src/a.py
"""


def test_all_skip_stage_marked_skipped():
    state = make_state(ALL_SKIP_STAGE)
    stage = S.get_stage(state, 1)
    assert stage["phase"] == "skipped"
    a = S.next_action(state)
    assert a.kind == "done"


# ---------- json safety ----------

def test_state_is_json_serializable():
    import json
    state = make_state(TWO_STAGE)
    json.dumps(state)


# ---------- R3: reviewer is advisory; only validator_rounds matters ----------

def test_validator_rounds_cap_terminates_checkpoint():
    """validator fail at cap → failed (advisory reviewer no longer involved)."""
    state = make_state(CHECKPOINT_ONLY, validator_rounds=2)
    S.advance(state, 1, "validator", 1, "fail")
    S.advance(state, 1, "coder", 2, "ok")
    S.advance(state, 1, "validator", 2, "fail")
    stage = S.get_stage(state, 1)
    assert stage["phase"] == "failed"
    assert "validator FAIL" in stage["fail_reason"]


def test_max_rounds_fallback_when_validator_cap_unspecified():
    """When only --max-rounds is given, validator uses it."""
    state = make_state(SINGLE_STAGE, max_rounds=2)
    cfg = state["config"]
    assert cfg["validator_max_rounds"] == 2


def test_legacy_state_without_caps_falls_back():
    """state.json missing validator_max_rounds (legacy) falls back to max_rounds."""
    state = make_state(CHECKPOINT_ONLY, max_rounds=3)
    del state["config"]["validator_max_rounds"]
    state["config"].pop("reviewer_max_rounds", None)
    S.advance(state, 1, "validator", 1, "fail")
    a = S.next_action(state)
    assert (a.payload["role"], a.payload["round"]) == ("coder", 2)


def test_reviewer_rounds_param_is_deprecated_no_op():
    """Passing --reviewer-rounds doesn't gate anything anymore (R3)."""
    state = make_state(SINGLE_STAGE, reviewer_rounds=1, validator_rounds=3)
    S.advance(state, 1, "coder", 1, "ok")
    # reviewer with any judgment still converges the stage
    S.advance(state, 1, "reviewer", 1, "p0")
    assert S.get_stage(state, 1)["phase"] == "converged"
