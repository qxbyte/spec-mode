#!/usr/bin/env python3
"""task_swarm_state.py — task-swarm 状态机（state.json 单一事实源；详见 references/task-swarm.md §7）。

负责：
    - state.json 的 load/save（atomic write + fsync）
    - phase 状态机推进（references/task-swarm.md §3）
    - 死循环检测（连续 3 轮同 fail 签名 → group failed-deadloop）

state.json schema 见 references/task-swarm.md §7 关键不变量。本模块只管"事实源"，
派发 / 解析在 task_swarm.py 主 CLI 里。

stdlib-only。
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


# -------------------------------------------------------------------------
# 时间 / 原子写
# -------------------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            with contextlib.suppress(OSError):
                os.fsync(fh.fileno())
        os.replace(tmp, path)
        with contextlib.suppress(OSError):
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


# -------------------------------------------------------------------------
# 数据模型
# -------------------------------------------------------------------------

@dataclass
class StageEntry:
    """state.json 里一条 stage 记录。"""
    number: int
    title: str
    writes: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    depends_on: list[int] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    header_line_no: int = 0
    end_line_no: int = 0


# Phase 枚举（同 references/task-swarm.md §3）
PHASES = {
    "init", "coding", "review", "p0-fix",
    "validation", "v-fix", "writeback", "done", "error",
}

GROUP_STATUS_VALUES = {
    "pending", "coding", "review", "p0-fix", "validation",
    "v-fix", "writeback", "done", "failed", "failed-deadloop",
}

# 连续相同 fail 签名达到此值 → 整个 group 标 failed-deadloop
DEADLOOP_THRESHOLD = 3


# -------------------------------------------------------------------------
# StateMachine
# -------------------------------------------------------------------------

@dataclass
class StateMachine:
    run_id: str
    tasks_md: str
    run_dir: str
    max_parallel: int = 4
    max_rounds: int = 6
    session_id: Optional[str] = None
    spec_dir: Optional[str] = None
    spec_id: Optional[str] = None

    # group 数据
    groups: list[list[StageEntry]] = field(default_factory=list)
    current_group_index: int = 0
    group_status: list[str] = field(default_factory=list)  # 与 groups 平行

    # 当前 phase 信息
    phase: str = "init"
    round: int = 0  # 当前 phase 已完成的轮号（v-fix 用得最多）

    # 在飞 / 已返回 subagent
    coder_in_flight: list[str] = field(default_factory=list)
    coder_done: list[str] = field(default_factory=list)
    reviewer_done: bool = False
    p0_in_flight: list[str] = field(default_factory=list)
    p0_done: list[str] = field(default_factory=list)
    validator_in_flight: bool = False
    vfix_in_flight: list[str] = field(default_factory=list)
    vfix_done: list[str] = field(default_factory=list)

    # findings & validator 历史（per group）
    findings: list[dict] = field(default_factory=list)  # reviewer 输出（含降级）
    p0_pending: list[dict] = field(default_factory=list)  # 带证据 P0 项
    fix_targets: list[dict] = field(default_factory=list)  # validator fail 时的修复目标
    validator_history: list[dict] = field(default_factory=list)  # 历轮 verdict + signature
    fail_signature: str = ""  # 上一轮 fail 签名

    # 状态
    started_at: str = ""
    last_activity_at: str = ""
    completed_at: Optional[str] = None
    failed_status: Optional[str] = None  # failed-deadloop / failed / done
    events: list[dict] = field(default_factory=list)

    # -----------------------------------------------------------------
    # 文件 IO
    # -----------------------------------------------------------------

    @staticmethod
    def state_path(run_dir: Path) -> Path:
        return run_dir / "state.json"

    @classmethod
    def load(cls, run_dir: Path) -> "StateMachine":
        sp = cls.state_path(run_dir)
        if not sp.exists():
            raise FileNotFoundError(f"state.json 不存在：{sp}")
        with sp.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        # 反序列化 groups
        groups: list[list[StageEntry]] = []
        for g in data.get("groups", []):
            gg: list[StageEntry] = []
            for s in g:
                gg.append(StageEntry(**s))
            groups.append(gg)
        sm = cls(
            run_id=data["run_id"],
            tasks_md=data["tasks_md"],
            run_dir=str(run_dir),
            max_parallel=data.get("max_parallel", 4),
            max_rounds=data.get("max_rounds", 6),
            session_id=data.get("session_id") or data.get("claude_session_id"),
            spec_dir=data.get("spec_dir"),
            spec_id=data.get("spec_id"),
            groups=groups,
            current_group_index=data.get("current_group_index", 0),
            group_status=data.get("group_status", ["pending"] * len(groups)),
            phase=data.get("phase", "init"),
            round=data.get("round", 0),
            coder_in_flight=data.get("coder_in_flight", []),
            coder_done=data.get("coder_done", []),
            reviewer_done=data.get("reviewer_done", False),
            p0_in_flight=data.get("p0_in_flight", []),
            p0_done=data.get("p0_done", []),
            validator_in_flight=data.get("validator_in_flight", False),
            vfix_in_flight=data.get("vfix_in_flight", []),
            vfix_done=data.get("vfix_done", []),
            findings=data.get("findings", []),
            p0_pending=data.get("p0_pending", []),
            fix_targets=data.get("fix_targets", []),
            validator_history=data.get("validator_history", []),
            fail_signature=data.get("fail_signature", ""),
            started_at=data.get("started_at", ""),
            last_activity_at=data.get("last_activity_at", ""),
            completed_at=data.get("completed_at"),
            failed_status=data.get("failed_status"),
            events=data.get("events", []),
        )
        return sm

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "tasks_md": self.tasks_md,
            "run_dir": self.run_dir,
            "max_parallel": self.max_parallel,
            "max_rounds": self.max_rounds,
            "session_id": self.session_id,
            "spec_dir": self.spec_dir,
            "spec_id": self.spec_id,
            "groups": [[asdict(s) for s in g] for g in self.groups],
            "current_group_index": self.current_group_index,
            "group_status": list(self.group_status),
            "phase": self.phase,
            "round": self.round,
            "coder_in_flight": list(self.coder_in_flight),
            "coder_done": list(self.coder_done),
            "reviewer_done": self.reviewer_done,
            "p0_in_flight": list(self.p0_in_flight),
            "p0_done": list(self.p0_done),
            "validator_in_flight": self.validator_in_flight,
            "vfix_in_flight": list(self.vfix_in_flight),
            "vfix_done": list(self.vfix_done),
            "findings": list(self.findings),
            "p0_pending": list(self.p0_pending),
            "fix_targets": list(self.fix_targets),
            "validator_history": list(self.validator_history),
            "fail_signature": self.fail_signature,
            "started_at": self.started_at,
            "last_activity_at": self.last_activity_at,
            "completed_at": self.completed_at,
            "failed_status": self.failed_status,
            "events": list(self.events),
        }

    def save(self) -> None:
        self.last_activity_at = _now_iso()
        _atomic_write_json(self.state_path(Path(self.run_dir)), self.to_dict())

    # -----------------------------------------------------------------
    # 事件
    # -----------------------------------------------------------------

    def events_append(self, event: dict) -> None:
        e = dict(event)
        e.setdefault("at", _now_iso())
        self.events.append(e)

    # -----------------------------------------------------------------
    # 当前 group 视图
    # -----------------------------------------------------------------

    def current_group(self) -> list[StageEntry]:
        if self.current_group_index >= len(self.groups):
            return []
        return self.groups[self.current_group_index]

    def is_group_complete(self) -> bool:
        return self.current_group_index >= len(self.groups)

    def current_group_done(self) -> bool:
        if self.current_group_index >= len(self.group_status):
            return False
        return self.group_status[self.current_group_index] in ("done", "failed", "failed-deadloop")

    # -----------------------------------------------------------------
    # phase 推进
    # -----------------------------------------------------------------

    def begin_coding(self) -> None:
        """进入新 group 的 coding phase。"""
        if self.current_group_index >= len(self.groups):
            self.phase = "done"
            self.completed_at = _now_iso()
            return
        self.phase = "coding"
        self.round = 1
        # 设置 in_flight keys（命名规则见 references/task-swarm.md §4）
        gi = self.current_group_index
        self.coder_in_flight = [
            f"coder-g{gi + 1}-s{s.number}-r1" for s in self.current_group()
        ]
        self.coder_done = []
        self.reviewer_done = False
        self.p0_in_flight = []
        self.p0_done = []
        self.validator_in_flight = False
        self.vfix_in_flight = []
        self.vfix_done = []
        self.findings = []
        self.p0_pending = []
        self.fix_targets = []
        self.validator_history = []
        self.fail_signature = ""
        self.group_status[gi] = "coding"
        self.events_append({"type": "phase", "phase": "coding", "group": gi + 1})

    def mark_coder_done(self, agent_key: str) -> None:
        if agent_key in self.coder_in_flight:
            self.coder_in_flight.remove(agent_key)
        if agent_key not in self.coder_done:
            self.coder_done.append(agent_key)

    def all_coders_returned(self) -> bool:
        return not self.coder_in_flight

    def begin_review(self) -> None:
        self.phase = "review"
        self.reviewer_done = False
        gi = self.current_group_index
        self.group_status[gi] = "review"
        self.events_append({"type": "phase", "phase": "review", "group": gi + 1})

    def mark_reviewer_done(self) -> None:
        self.reviewer_done = True

    def begin_p0_fix(self, p0_pending: list[dict]) -> None:
        self.phase = "p0-fix"
        self.round = 1
        gi = self.current_group_index
        self.p0_pending = list(p0_pending)
        # 按文件分组：每个不同 file → 一个 fix agent
        files: list[str] = []
        for p in p0_pending:
            f = (p.get("file_hint") or "unknown").strip()
            if f not in files:
                files.append(f)
        self.p0_in_flight = [f"coder-p0fix-g{gi + 1}-r1-f{i}" for i in range(len(files))]
        self.p0_done = []
        self.group_status[gi] = "p0-fix"
        self.events_append({"type": "phase", "phase": "p0-fix", "group": gi + 1,
                            "files": files})

    def mark_p0_done(self, agent_key: str) -> None:
        if agent_key in self.p0_in_flight:
            self.p0_in_flight.remove(agent_key)
        if agent_key not in self.p0_done:
            self.p0_done.append(agent_key)

    def all_p0_returned(self) -> bool:
        return not self.p0_in_flight

    def begin_validation(self) -> None:
        self.phase = "validation"
        self.validator_in_flight = True
        gi = self.current_group_index
        self.group_status[gi] = "validation"
        self.events_append({"type": "phase", "phase": "validation",
                            "group": gi + 1, "round": self.round})

    def mark_validator_done(self) -> None:
        self.validator_in_flight = False

    def record_round_signature(self, fail_sig: str) -> None:
        """记录本轮 fail 签名到 history。"""
        gi = self.current_group_index
        self.validator_history.append({
            "group": gi + 1,
            "round": self.round,
            "verdict": "fail" if fail_sig else "pass",
            "signature": fail_sig,
            "at": _now_iso(),
        })
        self.fail_signature = fail_sig

    def detect_deadloop(self) -> bool:
        """连续 3 轮同 fail 签名 → 死循环。"""
        gi = self.current_group_index
        sigs = [h["signature"] for h in self.validator_history
                if h.get("group") == gi + 1 and h.get("verdict") == "fail" and h.get("signature")]
        if len(sigs) < DEADLOOP_THRESHOLD:
            return False
        return all(s == sigs[-1] for s in sigs[-DEADLOOP_THRESHOLD:])

    def begin_v_fix(self, fix_targets: list[dict]) -> None:
        self.phase = "v-fix"
        self.round += 1
        gi = self.current_group_index
        # 按文件分组
        files: list[str] = []
        for t in fix_targets:
            f = (t.get("file_path") or "unknown").strip()
            if f and f not in files:
                files.append(f)
        if not files:
            files = ["unknown"]
        self.fix_targets = list(fix_targets)
        self.vfix_in_flight = [
            f"coder-vfix-g{gi + 1}-r{self.round}-f{i}" for i in range(len(files))
        ]
        self.vfix_done = []
        self.group_status[gi] = "v-fix"
        self.events_append({"type": "phase", "phase": "v-fix",
                            "group": gi + 1, "round": self.round, "files": files})

    def mark_vfix_done(self, agent_key: str) -> None:
        if agent_key in self.vfix_in_flight:
            self.vfix_in_flight.remove(agent_key)
        if agent_key not in self.vfix_done:
            self.vfix_done.append(agent_key)

    def all_vfix_returned(self) -> bool:
        return not self.vfix_in_flight

    def begin_writeback(self) -> None:
        self.phase = "writeback"
        gi = self.current_group_index
        self.group_status[gi] = "writeback"

    def finalize_group(self, status: str = "done") -> None:
        gi = self.current_group_index
        if gi < len(self.group_status):
            self.group_status[gi] = status
        self.events_append({"type": "group-done", "group": gi + 1, "status": status})
        # 进入下一 group
        self.current_group_index += 1
        if self.current_group_index >= len(self.groups):
            self.phase = "done"
            self.completed_at = _now_iso()
            self.failed_status = self.failed_status or "done"
            self.events_append({"type": "run-done", "status": self.failed_status})
        else:
            # 不自动 begin_coding；由 advance 调用
            self.phase = "init"

    def fail_group_deadloop(self) -> None:
        gi = self.current_group_index
        if gi < len(self.group_status):
            self.group_status[gi] = "failed-deadloop"
        self.failed_status = "failed-deadloop"
        self.phase = "error"
        self.events_append({"type": "group-failed", "group": gi + 1, "reason": "deadloop"})


# -------------------------------------------------------------------------
# 模块自测
# -------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import sys
    if len(sys.argv) < 2:
        print("usage: task_swarm_state.py <run_dir>")
        raise SystemExit(2)
    sm = StateMachine.load(Path(sys.argv[1]))
    print(json.dumps(sm.to_dict(), ensure_ascii=False, indent=2))
