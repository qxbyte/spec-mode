"""task-swarm state machine.

Holds the run-level state.json and provides pure functions to compute the
next dispatch action. The orchestrator (task_swarm.py) calls `next_action()`
and `advance()` — never tries to "remember" round counters or convergence
status itself.

state.json shape:
{
  "run_id": "20260517-153012-ab12cd",
  "tasks_path": "/abs/path/tasks.md",
  "spec_dir":   "/abs/path/spec-dir",
  "project_root": "/abs/path",
  "session_id": "...",
  "config": {"parallel": 3, "max_rounds": 3},
  "stages": [
    {
      "num": 1, "title": "...", "kind": "stage|checkpoint",
      "deps": [..], "files_union": [..], "optional": bool,
      "checkpoint_for": int|null,
      "leaves": [ {"num":"1.1", "policy":"full|default|coder-only|skip", ...}, ... ],
      "phase": "pending|running|converged|failed|skipped",
      "rounds": {"reviewer": 0, "validator": 0},
      "last": {"role": "coder|reviewer|validator", "round": N, "judgment": "ok|approved|p0|pass|fail|loop|schema-error"},
      "history": [ {...advance records...} ],
      "in_flight": null | {"role":..., "round":..., "started_at":...}
    }, ...
  ],
  "started_at": "...",
  "updated_at": "..."
}

Phase transitions:
  pending → running → converged | failed

Action types returned by next_action():
  {"action": "fork", "stage": N, "role": R, "round": K, ...}
  {"action": "writeback", "stage": N, "status": "converged|failed"}
  {"action": "wait"}    — there's work in-flight, model should not fork more
  {"action": "done", "summary": {...}}
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------- io ----------

STATE_FILENAME = "state.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_path(run_dir: Path) -> Path:
    return run_dir / STATE_FILENAME


def load_state(run_dir: Path) -> dict:
    p = state_path(run_dir)
    if not p.exists():
        raise FileNotFoundError(f"state.json not found at {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(run_dir: Path, state: dict) -> None:
    state["updated_at"] = _now()
    p = state_path(run_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


# ---------- construction ----------

def new_run_id() -> str:
    """Deterministic-ish run id: YYYYMMDD-HHMMSS-<6hex>."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{uuid.uuid4().hex[:6]}"


def build_initial_state(
    run_id: str,
    tasks_path: Path,
    spec_dir: Path,
    project_root: Path,
    plan: dict,
    parallel: int = 3,
    max_rounds: int = 3,
    reviewer_max_rounds: int | None = None,
    validator_max_rounds: int | None = None,
    session_id: str = "",
) -> dict:
    """Build the initial state.json structure.

    Rounds policy: reviewer and validator loops are counted independently.
    By default reviewer loops are tight (1 round) since reviewer P0 is a
    subjective signal — repeated 'I think this could be better' bounces
    waste budget. Validator fails are objective (test ran, test failed)
    so they get the full 3 rounds.

    If `reviewer_max_rounds` / `validator_max_rounds` are None, both fall
    back to `max_rounds` for backward compatibility.
    """
    rev_max = reviewer_max_rounds if reviewer_max_rounds is not None else max_rounds
    val_max = validator_max_rounds if validator_max_rounds is not None else max_rounds
    stages = []
    for s in plan["stages"]:
        stages.append({
            "num": s["num"],
            "title": s["title"],
            "kind": s["kind"],
            "deps": list(s.get("deps") or []),
            "files_union": list(s.get("files_union") or []),
            "optional": bool(s.get("optional")),
            "checkpoint_for": s.get("checkpoint_for"),
            "leaves": [dict(l) for l in s.get("leaves") or []],
            "phase": "pending",
            "rounds": {"reviewer": 0, "validator": 0},
            "last": None,
            "history": [],
            "in_flight": None,
        })

    # Pre-skip stages whose every leaf is skip, or stage marked optional with
    # only coder-only leaves and no requirement (still kept as `pending` if
    # has coder leaves — we only auto-skip when ALL leaves are skip).
    for st in stages:
        if st["kind"] == "stage":
            non_skip = [l for l in st["leaves"] if l.get("policy") != "skip"]
            if not non_skip:
                st["phase"] = "skipped"

    return {
        "version": 1,
        "run_id": run_id,
        "tasks_path": str(tasks_path),
        "spec_dir": str(spec_dir),
        "project_root": str(project_root),
        "session_id": session_id,
        "config": {
            "parallel": int(parallel),
            "max_rounds": int(max_rounds),
            "reviewer_max_rounds": int(rev_max),
            "validator_max_rounds": int(val_max),
        },
        "stages": stages,
        "warnings": list(plan.get("warnings") or []),
        "started_at": _now(),
        "updated_at": _now(),
    }


def _role_max_rounds(state: dict, role: str) -> int:
    """Return the cap for the given role, with legacy fallback to max_rounds."""
    cfg = state["config"]
    if role == "reviewer":
        return int(cfg.get("reviewer_max_rounds") or cfg.get("max_rounds", 3))
    if role == "validator":
        return int(cfg.get("validator_max_rounds") or cfg.get("max_rounds", 3))
    # Coder rounds are bounded by whichever upstream loop triggered them;
    # use the larger of the two role caps so coder isn't the bottleneck.
    return max(
        int(cfg.get("reviewer_max_rounds") or cfg.get("max_rounds", 3)),
        int(cfg.get("validator_max_rounds") or cfg.get("max_rounds", 3)),
    )


# ---------- state queries ----------

def get_stage(state: dict, num: int) -> dict:
    for s in state["stages"]:
        if s["num"] == num:
            return s
    raise KeyError(f"stage {num} not found")


def stage_completed(stage: dict) -> bool:
    return stage["phase"] in {"converged", "failed", "skipped"}


def deps_satisfied(state: dict, stage: dict) -> bool:
    for dep_num in stage["deps"]:
        try:
            dep = get_stage(state, dep_num)
        except KeyError:
            continue
        if dep["phase"] != "converged":
            return False
    return True


def has_files_conflict(a: dict, b: dict) -> bool:
    fa = set(a.get("files_union") or [])
    fb = set(b.get("files_union") or [])
    return bool(fa & fb)


def in_flight_count(state: dict) -> int:
    return sum(1 for s in state["stages"] if s.get("in_flight"))


# ---------- next_action ----------

@dataclass
class Action:
    kind: str   # fork | writeback | wait | done
    payload: dict

    def to_dict(self) -> dict:
        return {"action": self.kind, **self.payload}


def next_action(state: dict) -> Action:
    """Return the single highest-priority next thing the orchestrator should do.

    Priority order:
      1. Any stage whose loop converged but hasn't been written back → writeback
      2. Any stage in-flight → wait
      3. The first stage that's ready to dispatch its next role → fork
      4. All stages done → done
    """
    # 1. writebacks pending
    for s in state["stages"]:
        if s["phase"] in {"converged", "failed"} and not s.get("written_back"):
            return Action("writeback", {
                "stage": s["num"],
                "status": s["phase"],
                "rounds": dict(s["rounds"]),
                "title": s["title"],
            })

    # 2. in-flight blocks new forks beyond parallel limit
    parallel_cap = state["config"]["parallel"]
    in_flight = in_flight_count(state)

    # 3. find next fork candidate
    candidates = []
    for s in state["stages"]:
        if stage_completed(s):
            continue
        if s.get("in_flight"):
            continue
        if not deps_satisfied(state, s):
            continue
        action = _next_role_for_stage(state, s)
        if action is None:
            continue
        candidates.append((s, action))

    # honor parallel cap + file conflict
    chosen = None
    already_running = [s for s in state["stages"] if s.get("in_flight")]
    for s, action in candidates:
        if in_flight >= parallel_cap and action["round"] == 1 and action["role"] == "coder":
            # don't kick off a brand-new stage while at parallel cap
            continue
        # file conflict with anything in-flight blocks dispatch
        conflict = any(has_files_conflict(s, r) for r in already_running)
        if conflict:
            continue
        chosen = (s, action)
        break

    if chosen is not None:
        s, action = chosen
        return Action("fork", {
            "stage": s["num"],
            "title": s["title"],
            "stage_kind": s["kind"],
            **action,
        })

    if in_flight > 0:
        return Action("wait", {"in_flight": in_flight})

    if any(not s.get("written_back") and s["phase"] in {"converged", "failed"} for s in state["stages"]):
        # shouldn't reach here because we handle writebacks first; defensive
        return Action("wait", {"in_flight": 0})

    return Action("done", {"summary": summarize(state)})


def _next_role_for_stage(state: dict, stage: dict) -> dict | None:
    """For a single stage, determine its next dispatch step (role + round).

    Returns None if the stage is in a state that needs no fork (e.g., it's
    actually done and pending writeback — caller checks that separately).
    """
    rev_max = _role_max_rounds(state, "reviewer")
    val_max = _role_max_rounds(state, "validator")
    last = stage.get("last")
    kind = stage["kind"]

    # Brand new stage
    if stage["phase"] == "pending":
        if kind == "checkpoint":
            return {"role": "validator", "round": 1}
        # regular stage starts with coder
        if not any(l.get("policy") != "skip" for l in stage["leaves"]):
            return None  # nothing to do
        return {"role": "coder", "round": 1}

    if stage["phase"] != "running":
        return None

    if last is None:
        # phase running but no last record — shouldn't normally happen
        return {"role": "coder", "round": 1}

    role = last["role"]
    judgment = last["judgment"]
    round_no = last["round"]

    # --- coder finished ---
    if role == "coder":
        if judgment in {"failed", "blocked"}:
            return None  # caller will have flipped phase to failed; defensive
        if kind == "checkpoint":
            # checkpoint coder fix → re-validate after a reviewer quick check
            return {"role": "reviewer", "round": round_no, "scope": "post-fix"}
        # normal stage: any reviewable leaf → reviewer; else go straight to validator-less convergence
        if any(l.get("policy") in {"full", "default"} for l in stage["leaves"]):
            return {"role": "reviewer", "round": round_no}
        # all leaves are coder-only → stage converges directly (no reviewer, no validator)
        return None

    # --- reviewer finished ---
    if role == "reviewer":
        if judgment == "approved":
            # If this stage has a paired checkpoint elsewhere, validator runs
            # via that checkpoint stage. The stage itself converges here.
            return None
        if judgment == "p0":
            if round_no >= rev_max:
                return None  # caller marks failed
            return {"role": "coder", "round": round_no + 1, "scope": "p0-fix"}
        # loop / schema-error / other → caller decides terminal state
        return None

    # --- validator finished ---
    if role == "validator":
        if judgment == "pass":
            return None  # caller marks converged
        if judgment == "fail":
            if round_no >= val_max:
                return None
            # fork coder fix; next reviewer-quick-check then re-validate
            return {"role": "coder", "round": round_no + 1, "scope": "validator-fail-fix"}
        return None

    return None


# ---------- advance ----------

VALID_JUDGMENTS = {
    "coder": {"ok", "failed", "blocked"},
    "reviewer": {"approved", "p0", "loop", "schema-error"},
    "validator": {"pass", "fail", "loop", "schema-error"},
}


def advance(state: dict, stage_num: int, role: str, round_no: int, judgment: str, extra: dict | None = None) -> dict:
    """Record a subagent's verdict; flip phase if it terminates the stage.

    Returns the updated stage dict. Caller persists via save_state().
    """
    if role not in VALID_JUDGMENTS:
        raise ValueError(f"unknown role: {role}")
    if judgment not in VALID_JUDGMENTS[role]:
        raise ValueError(f"invalid judgment '{judgment}' for role '{role}'")

    stage = get_stage(state, stage_num)
    if stage["phase"] in {"converged", "failed", "skipped"}:
        raise ValueError(f"stage {stage_num} already terminal: {stage['phase']}")

    # Promote to running on first advance
    if stage["phase"] == "pending":
        stage["phase"] = "running"

    # Update round counter (we count the *largest* round seen for that role)
    if role in {"reviewer", "validator"}:
        prev = stage["rounds"].get(role, 0)
        if round_no > prev:
            stage["rounds"][role] = round_no

    record = {
        "role": role,
        "round": round_no,
        "judgment": judgment,
        "at": _now(),
        **(extra or {}),
    }
    stage["history"].append(record)
    stage["last"] = {"role": role, "round": round_no, "judgment": judgment}
    stage["in_flight"] = None

    # Terminal-state inference
    rev_max = _role_max_rounds(state, "reviewer")
    val_max = _role_max_rounds(state, "validator")
    kind = stage["kind"]

    if role == "coder" and judgment in {"failed", "blocked"}:
        stage["phase"] = "failed"
        stage["fail_reason"] = (extra or {}).get("reason") or f"coder {judgment}"
        return stage

    # coder-only stage: ok on coder is terminal convergence (no reviewer/validator)
    if (
        role == "coder"
        and judgment == "ok"
        and kind == "stage"
        and not any(l.get("policy") in {"full", "default"} for l in stage["leaves"])
    ):
        stage["phase"] = "converged"
        return stage

    if role == "reviewer":
        if judgment in {"loop", "schema-error"}:
            stage["phase"] = "failed"
            stage["fail_reason"] = f"reviewer {judgment}"
            return stage
        if judgment == "approved":
            # Stage converges if no separate checkpoint exists; otherwise wait
            # for the checkpoint's validator. We mark the stage converged either
            # way — the checkpoint is a different stage that runs independently.
            stage["phase"] = "converged"
            return stage
        if judgment == "p0" and round_no >= rev_max:
            stage["phase"] = "failed"
            stage["fail_reason"] = f"reviewer P0 after {round_no} rounds (cap={rev_max})"
            return stage

    if role == "validator":
        if judgment in {"loop", "schema-error"}:
            stage["phase"] = "failed"
            stage["fail_reason"] = f"validator {judgment}"
            return stage
        if judgment == "pass":
            stage["phase"] = "converged"
            return stage
        if judgment == "fail" and round_no >= val_max:
            stage["phase"] = "failed"
            stage["fail_reason"] = f"validator FAIL after {round_no} rounds (cap={val_max})"
            return stage

    # coder ok / p0 within budget / fail within budget — stay running
    return stage


def mark_in_flight(state: dict, stage_num: int, role: str, round_no: int) -> None:
    stage = get_stage(state, stage_num)
    stage["in_flight"] = {"role": role, "round": round_no, "started_at": _now()}


def mark_written_back(state: dict, stage_num: int) -> None:
    stage = get_stage(state, stage_num)
    stage["written_back"] = True


# ---------- summary ----------

def summarize(state: dict) -> dict:
    return {
        "run_id": state["run_id"],
        "stages": [
            {
                "num": s["num"],
                "title": s["title"],
                "kind": s["kind"],
                "phase": s["phase"],
                "rounds": dict(s["rounds"]),
                "fail_reason": s.get("fail_reason"),
            }
            for s in state["stages"]
        ],
    }
