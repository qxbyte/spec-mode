#!/usr/bin/env python3
"""task_swarm.py — task-swarm 编排主 CLI（详见 references/task-swarm.md）。

子命令：
    init      --tasks <abs> [--max-parallel N] [--max-rounds N] [--session <id>] [--spec <dir>]
    status    --run <run_id>
    plan      --run <run_id>
    advance   --run <run_id> --phase <coding|review|p0-fix|validation|v-fix> --round <n>
    writeback --run <run_id> --group <N>
    heartbeat --run <run_id>
    resolve   --run <run_id> [--abort]

主代理通过 plan→fork→advance 循环驱动；本脚本只负责"确定性查询 / 状态推进 /
outbox 解析 / tasks.md line-safe diff 写回"。

stdlib-only。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import string
import sys
import time
from pathlib import Path
from typing import Any, Optional

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from task_swarm_parse_md import parse_tasks_md, group_by_file_conflict  # noqa: E402

# 0.10.0+ 日志（defensive import）
try:
    from spec_log import write_event as _log_event  # type: ignore
except Exception:
    def _log_event(event: str, payload: Optional[dict] = None,
                   session_id: Optional[str] = None) -> None:
        return None
from task_swarm_state import StateMachine, StageEntry, _atomic_write_json  # noqa: E402
from task_swarm_outbox import (  # noqa: E402
    ParseError, parse_coder_result, parse_reviewer_review, parse_validator_validation,
)
from task_swarm_prompt import (  # noqa: E402
    render_coder_prompt, render_reviewer_prompt, render_validator_prompt,
)
from task_swarm_writeback import (  # noqa: E402
    GroupFindings, StageFinding, WriteBackError, writeback_tasks_md,
)


# -------------------------------------------------------------------------
# 工具
# -------------------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _gen_run_id() -> str:
    """YYYYMMDD-HHMMSS-<6 位随机>，与 spec-mode 0.3.0 一致。"""
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{ts}-{rand}"


def _emit(payload: Any) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _sessions_dir() -> Path:
    return Path.home() / ".specode" / "sessions"


def _session_path(session_id: str) -> Path:
    return _sessions_dir() / f"{session_id}.json"


def _read_session(session_id: str) -> Optional[dict]:
    p = _session_path(session_id)
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _write_session(session_id: str, data: dict) -> None:
    _atomic_write_json(_session_path(session_id), data)


def _runs_root_for(tasks_md: Path, spec_dir: Optional[Path]) -> Path:
    """决定 .task-swarm/runs/ 根目录。

    优先级：
    1. spec_dir/.task-swarm/runs/
    2. tasks_md.parent/.task-swarm/runs/
    """
    if spec_dir is not None:
        return spec_dir / ".task-swarm" / "runs"
    return tasks_md.parent / ".task-swarm" / "runs"


def _resolve_run_dir(run_id: str, hint_dirs: list[Path]) -> Path:
    """根据 run_id 查找 run_dir。

    hint_dirs：可能的 .task-swarm/runs/ 父目录候选。
    若 run_id 是绝对路径直接返回。
    """
    p = Path(run_id)
    if p.is_absolute() and p.exists():
        return p
    # 在 hint_dirs 下查找
    for hd in hint_dirs:
        candidate = hd / run_id
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"找不到 run_dir for run_id={run_id}（hints={hint_dirs}）")


def _collect_run_dirs() -> list[Path]:
    """扫描当前目录 / 当前目录上层若干层下的 .task-swarm/runs/*。"""
    candidates: list[Path] = []
    cwd = Path.cwd()
    for base in (cwd, cwd.parent, cwd.parent.parent):
        runs = base / ".task-swarm" / "runs"
        if runs.exists():
            candidates.append(runs)
    return candidates


def _find_run_dir(run_id: str) -> Path:
    # 第一步：如果 run_id 是绝对路径直接用
    p = Path(run_id)
    if p.is_absolute() and p.exists():
        return p
    # 第二步：扫描 sessions 找到 spec_dir
    sessions_dir = _sessions_dir()
    spec_dirs: list[Path] = []
    if sessions_dir.exists():
        for sf in sessions_dir.glob("*.json"):
            try:
                with sf.open("r", encoding="utf-8") as fh:
                    sess = json.load(fh)
                sd = sess.get("active_spec_dir")
                if sd:
                    spec_dirs.append(Path(sd))
            except Exception:
                continue
    hint_dirs: list[Path] = []
    for sd in spec_dirs:
        hint_dirs.append(sd / ".task-swarm" / "runs")
    hint_dirs.extend(_collect_run_dirs())
    return _resolve_run_dir(run_id, hint_dirs)


# -------------------------------------------------------------------------
# init
# -------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    tasks_md = Path(args.tasks).resolve()
    if not tasks_md.exists():
        sys.stderr.write(f"tasks.md 不存在：{tasks_md}\n")
        return 1

    spec_dir: Optional[Path] = None
    spec_id: Optional[str] = None
    if args.spec:
        spec_dir = Path(args.spec).resolve()
    else:
        # 推断：tasks.md 所在目录就是 spec_dir
        if (tasks_md.parent / ".config.json").exists():
            spec_dir = tasks_md.parent
    if spec_dir is not None:
        cfg_path = spec_dir / ".config.json"
        if cfg_path.exists():
            try:
                with cfg_path.open("r", encoding="utf-8") as fh:
                    cfg = json.load(fh)
                spec_id = cfg.get("specId")
            except Exception:
                pass

    stages = parse_tasks_md(tasks_md)
    if not stages:
        sys.stderr.write("tasks.md 中未解析出任何 `## 阶段 N:` 段；请确认格式\n")
        return 1
    groups_raw = group_by_file_conflict(stages, max_parallel=args.max_parallel)
    if not groups_raw:
        sys.stderr.write("group 切分结果为空\n")
        return 1

    run_id = _gen_run_id()
    runs_root = _runs_root_for(tasks_md, spec_dir)
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "agents").mkdir(parents=True, exist_ok=True)

    # 转换为 StageEntry
    groups: list[list[StageEntry]] = []
    for g in groups_raw:
        gg: list[StageEntry] = []
        for s in g:
            items_dict = [
                {
                    "number": it.number,
                    "title": it.title,
                    "writes": list(it.writes),
                    "reads": list(it.reads),
                    "depends_on": list(it.depends_on),
                    "requirements": list(it.requirements),
                    "raw_line": it.raw_line,
                    "checkbox": it.checkbox,
                    "line_no": it.line_no,
                }
                for it in s.items
            ]
            gg.append(StageEntry(
                number=s.number,
                title=s.title,
                writes=list(s.writes),
                reads=list(s.reads),
                depends_on=list(s.depends_on),
                requirements=[r for it in s.items for r in it.requirements],
                items=items_dict,
                header_line_no=s.header_line_no,
                end_line_no=s.end_line_no,
            ))
        groups.append(gg)

    sm = StateMachine(
        run_id=run_id,
        tasks_md=str(tasks_md),
        run_dir=str(run_dir),
        max_parallel=args.max_parallel,
        max_rounds=args.max_rounds,
        session_id=args.session,
        spec_dir=str(spec_dir) if spec_dir else None,
        spec_id=spec_id,
        groups=groups,
        current_group_index=0,
        group_status=["pending"] * len(groups),
        phase="init",
        round=0,
        started_at=_now_iso(),
        last_activity_at=_now_iso(),
    )
    sm.events_append({"type": "init", "tasks_md": str(tasks_md), "groups": len(groups)})
    sm.save()

    # 同步 sessions/<id>.json.task_swarm_run_id
    if args.session:
        sess = _read_session(args.session) or {}
        sess["task_swarm_run_id"] = run_id
        sess["last_activity_at"] = _now_iso()
        with contextlib.suppress(Exception):
            _write_session(args.session, sess)

    out = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "tasks_md": str(tasks_md),
        "spec_dir": str(spec_dir) if spec_dir else None,
        "spec_id": spec_id,
        "groups": [
            [{"stage": s.number, "title": s.title, "writes": s.writes,
              "depends_on": s.depends_on} for s in g]
            for g in groups
        ],
    }
    _emit(out)
    return 0


# -------------------------------------------------------------------------
# status
# -------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    try:
        run_dir = _find_run_dir(args.run)
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        return 1
    sm = StateMachine.load(run_dir)
    gi = sm.current_group_index
    pending = []
    if gi < len(sm.groups):
        for s in sm.groups[gi]:
            pending.append({"stage": s.number, "title": s.title, "writes": s.writes})
    payload = {
        "run_id": sm.run_id,
        "tasks_md": sm.tasks_md,
        "phase": sm.phase,
        "group": gi + 1 if gi < len(sm.groups) else None,
        "round": sm.round,
        "total_groups": len(sm.groups),
        "group_status": sm.group_status,
        "coder_in_flight": sm.coder_in_flight,
        "reviewer_done": sm.reviewer_done,
        "p0_in_flight": sm.p0_in_flight,
        "validator_in_flight": sm.validator_in_flight,
        "vfix_in_flight": sm.vfix_in_flight,
        "pending_stages": pending,
        "failed_status": sm.failed_status,
        "completed_at": sm.completed_at,
    }
    _emit(payload)
    return 0


# -------------------------------------------------------------------------
# plan
# -------------------------------------------------------------------------

PLAN_TEMPLATES = {
    "coding-waiting": "coding phase 仍有 {n} 个 coder 未返回，等齐后再判断。",
    "coding-fork": "本 group 开始 coding。请按下面 {n} 个 coder agent_key fork（同 message 内并发）。",
    "review-fork": "本 group coder 已全部返回。请 fork **1 个** `task-swarm-reviewer`。",
    "p0-fix-fork": "reviewer 提了 {n} 个带证据 P0。请按 P0 涉及文件 fork **{n}** 个 `task-swarm-coder`（p0-fix）。注意：reviewer 修复**只触发一次**，不 re-review。",
    "validation-fork": "请 fork **1 个** `task-swarm-validator`。",
    "validation-fork-after-p0": "p0-fix coder 已返回。请 fork **1 个** `task-swarm-validator`。",
    "writeback": "validator pass。请调 `task_swarm.py writeback --run {run} --group {g}` 回写 tasks.md，然后进入下一 group。",
    "v-fix-fork": "validator fail。请按 validation.md 的 fix_targets 各文件 fork **{n}** 个 `task-swarm-coder`（v-fix）。注意：validator fail 循环修复直到 pass。本轮是 g{g}-r{r}。",
    "validation-after-vfix": "v-fix coder 已返回。请 fork **1 个** `task-swarm-validator` 验证。",
    "deadloop": "⚠️ 死循环检测：g{g} 已连续 3 轮同一 fail 签名。建议停止本 group，向用户报告 `failed-deadloop`，让用户介入。",
    "all-done": "全部 group 已完成。请按 SKILL.md 退出 task-swarm 模式，回到 spec-mode acceptance phase。",
}


def _plan_for(sm: StateMachine) -> dict:
    """根据 state 推导下一步建议（确定性查询，不改 state）。"""
    if sm.failed_status == "failed-deadloop":
        gi = sm.current_group_index
        return {
            "phase": sm.phase,
            "action": "deadloop",
            "message": PLAN_TEMPLATES["deadloop"].format(g=gi + 1),
            "fork": [],
        }
    if sm.is_group_complete() or sm.phase == "done":
        return {
            "phase": "done",
            "action": "all-done",
            "message": PLAN_TEMPLATES["all-done"],
            "fork": [],
        }

    gi = sm.current_group_index
    g = sm.current_group()

    # 1. phase=init：尚未开始本 group coding，建议 fork coder
    if sm.phase == "init":
        forks = []
        run_dir = Path(sm.run_dir)
        for s in g:
            key = f"coder-g{gi + 1}-s{s.number}-r1"
            task_md = run_dir / "agents" / key / "task.md"
            forks.append({
                "agent": "task-swarm-coder",
                "agent_key": key,
                "task_md": str(task_md),
                "stage": s.number,
                "writes": s.writes,
            })
        return {
            "phase": "coding",
            "action": "coding-fork",
            "message": PLAN_TEMPLATES["coding-fork"].format(n=len(forks)),
            "fork": forks,
            "group": gi + 1,
        }

    # 2. coding 进行中
    if sm.phase == "coding":
        if sm.coder_in_flight and not sm.coder_done:
            forks = []
            run_dir = Path(sm.run_dir)
            for s in g:
                key = f"coder-g{gi + 1}-s{s.number}-r1"
                forks.append({
                    "agent": "task-swarm-coder",
                    "agent_key": key,
                    "task_md": str(run_dir / "agents" / key / "task.md"),
                    "stage": s.number,
                    "writes": s.writes,
                })
            return {
                "phase": "coding",
                "action": "coding-fork",
                "message": PLAN_TEMPLATES["coding-fork"].format(n=len(forks)),
                "fork": forks,
                "in_flight": list(sm.coder_in_flight),
                "group": gi + 1,
            }
        if sm.coder_in_flight:
            return {
                "phase": "coding",
                "action": "coding-waiting",
                "message": PLAN_TEMPLATES["coding-waiting"].format(n=len(sm.coder_in_flight)),
                "fork": [],
                "in_flight": list(sm.coder_in_flight),
                "group": gi + 1,
            }
        # 全部返回 → 建议 fork reviewer
        key = f"reviewer-g{gi + 1}-r1"
        return {
            "phase": "review",
            "action": "review-fork",
            "message": PLAN_TEMPLATES["review-fork"],
            "fork": [{
                "agent": "task-swarm-reviewer",
                "agent_key": key,
                "task_md": str(Path(sm.run_dir) / "agents" / key / "task.md"),
            }],
            "group": gi + 1,
        }

    # 3. review 完成 → 看是否有 P0
    if sm.phase == "review":
        if not sm.reviewer_done:
            # 仍未 fork → 给 fork 建议
            key = f"reviewer-g{gi + 1}-r1"
            return {
                "phase": "review",
                "action": "review-fork",
                "message": PLAN_TEMPLATES["review-fork"],
                "fork": [{
                    "agent": "task-swarm-reviewer",
                    "agent_key": key,
                    "task_md": str(Path(sm.run_dir) / "agents" / key / "task.md"),
                }],
                "group": gi + 1,
            }
        if sm.p0_pending:
            # 按文件分组
            files: list[str] = []
            for p in sm.p0_pending:
                f = (p.get("file_hint") or "unknown").strip()
                if f not in files:
                    files.append(f)
            forks = []
            for i, f in enumerate(files):
                key = f"coder-p0fix-g{gi + 1}-r1-f{i}"
                forks.append({
                    "agent": "task-swarm-coder",
                    "agent_key": key,
                    "task_md": str(Path(sm.run_dir) / "agents" / key / "task.md"),
                    "file": f,
                })
            return {
                "phase": "p0-fix",
                "action": "p0-fix-fork",
                "message": PLAN_TEMPLATES["p0-fix-fork"].format(n=len(forks)),
                "fork": forks,
                "group": gi + 1,
            }
        # 无 P0 → 直接 validator
        key = f"validator-g{gi + 1}-r1"
        return {
            "phase": "validation",
            "action": "validation-fork",
            "message": PLAN_TEMPLATES["validation-fork"],
            "fork": [{
                "agent": "task-swarm-validator",
                "agent_key": key,
                "task_md": str(Path(sm.run_dir) / "agents" / key / "task.md"),
            }],
            "group": gi + 1,
        }

    # 4. p0-fix 阶段
    if sm.phase == "p0-fix":
        if sm.p0_in_flight and not sm.p0_done:
            files: list[str] = []
            for p in sm.p0_pending:
                f = (p.get("file_hint") or "unknown").strip()
                if f not in files:
                    files.append(f)
            forks = []
            for i, f in enumerate(files):
                key = f"coder-p0fix-g{gi + 1}-r1-f{i}"
                forks.append({
                    "agent": "task-swarm-coder",
                    "agent_key": key,
                    "task_md": str(Path(sm.run_dir) / "agents" / key / "task.md"),
                    "file": f,
                })
            return {
                "phase": "p0-fix",
                "action": "p0-fix-fork",
                "message": PLAN_TEMPLATES["p0-fix-fork"].format(n=len(forks)),
                "fork": forks,
                "group": gi + 1,
            }
        if sm.p0_in_flight:
            return {
                "phase": "p0-fix",
                "action": "p0-fix-waiting",
                "message": f"p0-fix 仍有 {len(sm.p0_in_flight)} 个 coder 未返回。",
                "fork": [],
                "group": gi + 1,
            }
        key = f"validator-g{gi + 1}-r1"
        return {
            "phase": "validation",
            "action": "validation-fork-after-p0",
            "message": PLAN_TEMPLATES["validation-fork-after-p0"],
            "fork": [{
                "agent": "task-swarm-validator",
                "agent_key": key,
                "task_md": str(Path(sm.run_dir) / "agents" / key / "task.md"),
            }],
            "group": gi + 1,
        }

    # 5. validation 阶段
    if sm.phase == "validation":
        if sm.validator_in_flight:
            # 计算目标 round：第一次进 validation→1；v-fix 后再 validation→sm.round
            target_round = sm.round if sm.round > 0 else 1
            key = f"validator-g{gi + 1}-r{target_round}"
            if sm.validator_history:
                msg = PLAN_TEMPLATES["validation-after-vfix"]
                action = "validation-after-vfix"
            elif sm.p0_done:
                msg = PLAN_TEMPLATES["validation-fork-after-p0"]
                action = "validation-fork-after-p0"
            else:
                msg = PLAN_TEMPLATES["validation-fork"]
                action = "validation-fork"
            return {
                "phase": "validation",
                "action": action,
                "message": msg,
                "fork": [{
                    "agent": "task-swarm-validator",
                    "agent_key": key,
                    "task_md": str(Path(sm.run_dir) / "agents" / key / "task.md"),
                }],
                "group": gi + 1,
                "round": target_round,
            }
        # 看 fix_targets 决定 pass / fail
        if sm.fix_targets:
            # 死循环检测
            if sm.detect_deadloop():
                return {
                    "phase": sm.phase,
                    "action": "deadloop",
                    "message": PLAN_TEMPLATES["deadloop"].format(g=gi + 1),
                    "fork": [],
                    "group": gi + 1,
                }
            files: list[str] = []
            for t in sm.fix_targets:
                f = (t.get("file_path") or "").strip()
                if f and f not in files:
                    files.append(f)
            forks = []
            for i, f in enumerate(files):
                key = f"coder-vfix-g{gi + 1}-r{sm.round + 1}-f{i}"
                forks.append({
                    "agent": "task-swarm-coder",
                    "agent_key": key,
                    "task_md": str(Path(sm.run_dir) / "agents" / key / "task.md"),
                    "file": f,
                })
            return {
                "phase": "v-fix",
                "action": "v-fix-fork",
                "message": PLAN_TEMPLATES["v-fix-fork"].format(
                    n=len(forks), g=gi + 1, r=sm.round + 1,
                ),
                "fork": forks,
                "group": gi + 1,
            }
        # pass
        return {
            "phase": "writeback",
            "action": "writeback",
            "message": PLAN_TEMPLATES["writeback"].format(run=sm.run_id, g=gi + 1),
            "fork": [],
            "group": gi + 1,
        }

    # 6. v-fix 阶段
    if sm.phase == "v-fix":
        if sm.vfix_in_flight and not sm.vfix_done:
            files: list[str] = []
            for t in sm.fix_targets:
                f = (t.get("file_path") or "").strip()
                if f and f not in files:
                    files.append(f)
            forks = []
            for i, f in enumerate(files):
                key = f"coder-vfix-g{gi + 1}-r{sm.round}-f{i}"
                forks.append({
                    "agent": "task-swarm-coder",
                    "agent_key": key,
                    "task_md": str(Path(sm.run_dir) / "agents" / key / "task.md"),
                    "file": f,
                })
            return {
                "phase": "v-fix",
                "action": "v-fix-fork",
                "message": PLAN_TEMPLATES["v-fix-fork"].format(
                    n=len(forks), g=gi + 1, r=sm.round,
                ),
                "fork": forks,
                "group": gi + 1,
            }
        if sm.vfix_in_flight:
            return {
                "phase": "v-fix",
                "action": "v-fix-waiting",
                "message": f"v-fix 仍有 {len(sm.vfix_in_flight)} 个 coder 未返回。",
                "fork": [],
                "group": gi + 1,
            }
        key = f"validator-g{gi + 1}-r{sm.round + 1}"
        return {
            "phase": "validation",
            "action": "validation-after-vfix",
            "message": PLAN_TEMPLATES["validation-after-vfix"],
            "fork": [{
                "agent": "task-swarm-validator",
                "agent_key": key,
                "task_md": str(Path(sm.run_dir) / "agents" / key / "task.md"),
            }],
            "group": gi + 1,
        }

    if sm.phase == "writeback":
        return {
            "phase": "writeback",
            "action": "writeback",
            "message": PLAN_TEMPLATES["writeback"].format(run=sm.run_id, g=gi + 1),
            "fork": [],
            "group": gi + 1,
        }

    if sm.phase == "error":
        return {
            "phase": "error",
            "action": "deadloop",
            "message": PLAN_TEMPLATES["deadloop"].format(g=gi + 1),
            "fork": [],
            "group": gi + 1,
        }

    return {
        "phase": sm.phase,
        "action": "unknown",
        "message": f"未知 phase={sm.phase}，请检查 state.json",
        "fork": [],
    }


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        run_dir = _find_run_dir(args.run)
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        return 1
    sm = StateMachine.load(run_dir)
    # 若 phase=init，主动渲染 prompt 文件并把 phase 推进到 coding；
    # 渲染好的 prompts 让主代理可以直接 fork。
    if sm.phase == "init":
        _materialize_prompts_for_coding(sm)
        sm.begin_coding()
        sm.save()
        # 第一次 plan：把刚切到 coding 的 in-flight 列表当作"待 fork"列表返回
        gi = sm.current_group_index
        forks = []
        for s in sm.current_group():
            key = f"coder-g{gi + 1}-s{s.number}-r1"
            forks.append({
                "agent": "task-swarm-coder",
                "agent_key": key,
                "task_md": str(Path(sm.run_dir) / "agents" / key / "task.md"),
                "stage": s.number,
                "writes": s.writes,
            })
        plan = {
            "phase": "coding",
            "action": "coding-fork",
            "message": PLAN_TEMPLATES["coding-fork"].format(n=len(forks)),
            "fork": forks,
            "group": gi + 1,
        }
        _emit(plan)
        return 0
    if sm.phase == "coding" and not sm.coder_in_flight and sm.coder_done:
        # 全部返回 → 渲染 reviewer prompt
        _materialize_prompt_reviewer(sm)
    elif sm.phase == "review" and sm.reviewer_done and sm.p0_pending:
        _materialize_prompts_p0_fix(sm)
    elif sm.phase == "review" and sm.reviewer_done and not sm.p0_pending:
        _materialize_prompt_validator(sm)
    elif sm.phase == "p0-fix" and not sm.p0_in_flight and sm.p0_done:
        _materialize_prompt_validator(sm)
    elif sm.phase == "validation" and not sm.validator_in_flight and sm.fix_targets:
        if not sm.detect_deadloop():
            _materialize_prompts_v_fix(sm)
    elif sm.phase == "v-fix" and not sm.vfix_in_flight and sm.vfix_done:
        _materialize_prompt_validator(sm)
    plan = _plan_for(sm)
    _emit(plan)
    return 0


def _materialize_prompts_for_coding(sm: StateMachine) -> None:
    gi = sm.current_group_index
    for s in sm.current_group():
        render_coder_prompt(
            stage=s,
            run_dir=Path(sm.run_dir),
            run_id=sm.run_id,
            spec_id=sm.spec_id or "",
            spec_dir=sm.spec_dir or "",
            group=gi + 1,
            round_=1,
            mode="initial",
        )


def _materialize_prompt_reviewer(sm: StateMachine) -> None:
    gi = sm.current_group_index
    coder_outboxes: list[Path] = []
    run_dir = Path(sm.run_dir)
    for s in sm.current_group():
        outbox = run_dir / "agents" / f"coder-g{gi + 1}-s{s.number}-r1" / "outbox" / "result.md"
        coder_outboxes.append(outbox)
    render_reviewer_prompt(
        group_stages=sm.current_group(),
        coder_outboxes=coder_outboxes,
        run_dir=run_dir,
        run_id=sm.run_id,
        spec_id=sm.spec_id or "",
        spec_dir=sm.spec_dir or "",
        group=gi + 1,
        round_=1,
    )


def _materialize_prompts_p0_fix(sm: StateMachine) -> None:
    gi = sm.current_group_index
    files: list[str] = []
    for p in sm.p0_pending:
        f = (p.get("file_hint") or "unknown").strip()
        if f not in files:
            files.append(f)
    for i, f in enumerate(files):
        # 找到对应 stage（best effort：按文件路径匹配 stage.writes）
        match_stage = None
        for s in sm.current_group():
            if f in s.writes:
                match_stage = s
                break
        if match_stage is None and sm.current_group():
            match_stage = sm.current_group()[0]
        if match_stage is None:
            continue
        render_coder_prompt(
            stage=match_stage,
            run_dir=Path(sm.run_dir),
            run_id=sm.run_id,
            spec_id=sm.spec_id or "",
            spec_dir=sm.spec_dir or "",
            group=gi + 1,
            round_=1,
            mode="p0-fix",
            fix_targets=[p for p in sm.p0_pending
                         if (p.get("file_hint") or "").strip() == f],
            file_idx=i,
        )


def _materialize_prompts_v_fix(sm: StateMachine) -> None:
    gi = sm.current_group_index
    files: list[str] = []
    for t in sm.fix_targets:
        f = (t.get("file_path") or "").strip()
        if f and f not in files:
            files.append(f)
    if not files:
        files = ["unknown"]
    for i, f in enumerate(files):
        match_stage = None
        for s in sm.current_group():
            if f in s.writes:
                match_stage = s
                break
        if match_stage is None and sm.current_group():
            match_stage = sm.current_group()[0]
        if match_stage is None:
            continue
        ftargets = [t for t in sm.fix_targets
                    if (t.get("file_path") or "").strip() == f]
        render_coder_prompt(
            stage=match_stage,
            run_dir=Path(sm.run_dir),
            run_id=sm.run_id,
            spec_id=sm.spec_id or "",
            spec_dir=sm.spec_dir or "",
            group=gi + 1,
            round_=sm.round + 1,
            mode="v-fix",
            fix_targets=ftargets,
            file_idx=i,
        )


def _materialize_prompt_validator(sm: StateMachine) -> None:
    gi = sm.current_group_index
    if sm.phase == "v-fix":
        next_round = sm.round + 1
    elif sm.phase in ("review", "p0-fix"):
        next_round = 1
    else:
        next_round = sm.round if sm.round > 0 else 1
    prev_validation: Optional[Path] = None
    if sm.validator_history:
        last = sm.validator_history[-1]
        prev_validation = (Path(sm.run_dir) / "agents"
                           / f"validator-g{gi + 1}-r{last.get('round')}"
                           / "outbox" / "validation.md")
        if not prev_validation.exists():
            prev_validation = None
    render_validator_prompt(
        group_stages=sm.current_group(),
        run_dir=Path(sm.run_dir),
        run_id=sm.run_id,
        spec_id=sm.spec_id or "",
        spec_dir=sm.spec_dir or "",
        group=gi + 1,
        round_=next_round,
        prev_validation=prev_validation,
    )


# -------------------------------------------------------------------------
# advance
# -------------------------------------------------------------------------

def _coder_outbox_paths(sm: StateMachine, keys: list[str]) -> list[Path]:
    out: list[Path] = []
    for k in keys:
        out.append(Path(sm.run_dir) / "agents" / k / "outbox" / "result.md")
    return out


def cmd_advance(args: argparse.Namespace) -> int:
    try:
        run_dir = _find_run_dir(args.run)
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        return 1
    sm = StateMachine.load(run_dir)
    phase = args.phase
    errors: list[str] = []
    next_msg = ""

    if phase == "coding":
        # 解析 coder_in_flight + coder_done 全部 outbox
        all_keys = list(sm.coder_in_flight) + list(sm.coder_done)
        if not all_keys:
            all_keys = [f"coder-g{sm.current_group_index + 1}-s{s.number}-r1"
                        for s in sm.current_group()]
        any_failed = False
        for k in all_keys:
            p = Path(sm.run_dir) / "agents" / k / "outbox" / "result.md"
            try:
                res = parse_coder_result(p)
                sm.mark_coder_done(k)
                if res.status != "ok":
                    any_failed = True
                    errors.append(f"{k}: STATUS={res.status} {res.status_reason}")
            except ParseError as e:
                errors.append(f"{k}: parse error: {e}")
                any_failed = True
        sm.events_append({"type": "advance", "phase": "coding", "errors": errors})
        if any_failed:
            sm.failed_status = sm.failed_status or "failed"
            sm.group_status[sm.current_group_index] = "failed"
            sm.save()
            _emit({
                "ok": False,
                "phase": sm.phase,
                "errors": errors,
                "next": "report-failed-group",
            })
            return 0
        sm.begin_review()
        # 渲染 reviewer prompt
        _materialize_prompt_reviewer(sm)
        next_msg = "下一步：fork reviewer（agent_key=reviewer-g{}-r1）".format(
            sm.current_group_index + 1)

    elif phase == "review":
        gi = sm.current_group_index
        path = Path(sm.run_dir) / "agents" / f"reviewer-g{gi + 1}-r1" / "outbox" / "review.md"
        try:
            rev = parse_reviewer_review(path)
            sm.mark_reviewer_done()
            # 落 findings
            sm.findings = []
            for f in rev.p0_items:
                sm.findings.append({
                    "severity": "p0",
                    "text": f.text,
                    "evidence_tags": f.evidence_tags,
                    "file_hint": f.file_hint,
                    "fix_status": "未修复",
                })
            for f in rev.advisory_items:
                sm.findings.append({
                    "severity": "advisory",
                    "text": f.text,
                    "evidence_tags": [],
                    "file_hint": f.file_hint,
                    "fix_status": "未修复",
                })
            for f in rev.p1_items:
                sm.findings.append({
                    "severity": "p1",
                    "text": f.text,
                    "evidence_tags": [],
                    "file_hint": f.file_hint,
                    "fix_status": "未修复",
                })
            for f in rev.p2_items:
                sm.findings.append({
                    "severity": "p2",
                    "text": f.text,
                    "evidence_tags": [],
                    "file_hint": f.file_hint,
                    "fix_status": "未修复",
                })
            sm.p0_pending = [
                {"text": f.text, "evidence_tags": f.evidence_tags,
                 "file_hint": f.file_hint}
                for f in rev.p0_items
            ]
            sm.events_append({"type": "advance", "phase": "review",
                              "verdict": rev.verdict, "p0": len(rev.p0_items),
                              "advisory": len(rev.advisory_items),
                              "p1": len(rev.p1_items), "p2": len(rev.p2_items)})
            if sm.p0_pending:
                sm.begin_p0_fix(sm.p0_pending)
                _materialize_prompts_p0_fix(sm)
                next_msg = "下一步：fork p0-fix coder（按文件分组）"
            else:
                sm.begin_validation()
                _materialize_prompt_validator(sm)
                next_msg = "下一步：fork validator"
        except ParseError as e:
            errors.append(str(e))

    elif phase == "p0-fix":
        gi = sm.current_group_index
        all_keys = list(sm.p0_in_flight) + list(sm.p0_done)
        if not all_keys:
            # 推断：根据 p0_pending file 数
            files: list[str] = []
            for p in sm.p0_pending:
                f = (p.get("file_hint") or "unknown").strip()
                if f not in files:
                    files.append(f)
            all_keys = [f"coder-p0fix-g{gi + 1}-r1-f{i}" for i in range(len(files))]
        any_failed = False
        for k in all_keys:
            p = Path(sm.run_dir) / "agents" / k / "outbox" / "result.md"
            try:
                res = parse_coder_result(p)
                sm.mark_p0_done(k)
                if res.status != "ok":
                    any_failed = True
                    errors.append(f"{k}: STATUS={res.status} {res.status_reason}")
            except ParseError as e:
                errors.append(f"{k}: parse error: {e}")
                any_failed = True
        # 标记 p0 finding 的 fix_status
        for finding in sm.findings:
            if finding["severity"] == "p0":
                finding["fix_status"] = "未修复" if any_failed else "已修复"
        sm.events_append({"type": "advance", "phase": "p0-fix",
                          "any_failed": any_failed, "errors": errors})
        # 不阻断：进入 validation
        sm.begin_validation()
        _materialize_prompt_validator(sm)
        if any_failed:
            next_msg = "p0-fix 部分失败；继续进入 validation。失败的 P0 将标 '[P0 未修复]'。"
        else:
            next_msg = "p0-fix 全部 ok；进入 validation。"

    elif phase == "validation":
        gi = sm.current_group_index
        # 决定 round：本次 advance 对应的是 sm.round（v-fix 后 sm.round 已经在 begin_v_fix 时 +1，
        # validator 跑完的 round = sm.round）
        round_used = sm.round if sm.round > 0 else 1
        path = (Path(sm.run_dir) / "agents"
                / f"validator-g{gi + 1}-r{round_used}" / "outbox" / "validation.md")
        try:
            val = parse_validator_validation(path)
            sm.mark_validator_done()
            sm.round = round_used
            sig = val.fail_signature()
            sm.record_round_signature(sig)
            sm.events_append({"type": "advance", "phase": "validation",
                              "verdict": val.verdict, "round": round_used,
                              "signature": sig})
            if val.verdict == "pass":
                sm.fix_targets = []
                sm.begin_writeback()
                next_msg = (f"validator pass。请调 `task_swarm.py writeback "
                            f"--run {sm.run_id} --group {gi + 1}` 回写 tasks.md。")
            else:
                # fail
                sm.fix_targets = [
                    {
                        "file_path": t.file_path,
                        "title": t.title,
                        "location": t.location,
                        "problem": t.problem,
                        "suggestion": t.suggestion,
                        "requirements": list(t.requirements),
                    }
                    for t in val.fix_targets
                ]
                # 检测死循环
                if sm.detect_deadloop():
                    sm.fail_group_deadloop()
                    sm.save()
                    _emit({
                        "ok": False,
                        "phase": sm.phase,
                        "deadloop": True,
                        "next": "report-deadloop",
                        "round": sm.round,
                    })
                    return 0
                # 进入 v-fix
                sm.begin_v_fix(sm.fix_targets)
                _materialize_prompts_v_fix(sm)
                next_msg = (f"validator fail。请按 fix_targets 各文件 fork v-fix coder。"
                            f"本轮是 g{gi + 1}-r{sm.round}。")
        except ParseError as e:
            errors.append(str(e))

    elif phase == "v-fix":
        gi = sm.current_group_index
        all_keys = list(sm.vfix_in_flight) + list(sm.vfix_done)
        if not all_keys:
            files: list[str] = []
            for t in sm.fix_targets:
                f = (t.get("file_path") or "").strip()
                if f and f not in files:
                    files.append(f)
            if not files:
                files = ["unknown"]
            all_keys = [f"coder-vfix-g{gi + 1}-r{sm.round}-f{i}"
                        for i in range(len(files))]
        any_failed = False
        for k in all_keys:
            p = Path(sm.run_dir) / "agents" / k / "outbox" / "result.md"
            try:
                res = parse_coder_result(p)
                sm.mark_vfix_done(k)
                if res.status != "ok":
                    any_failed = True
                    errors.append(f"{k}: STATUS={res.status} {res.status_reason}")
            except ParseError as e:
                errors.append(f"{k}: parse error: {e}")
                any_failed = True
        sm.events_append({"type": "advance", "phase": "v-fix",
                          "round": sm.round, "any_failed": any_failed,
                          "errors": errors})
        if any_failed:
            sm.failed_status = sm.failed_status or "failed"
            sm.group_status[sm.current_group_index] = "failed"
            sm.save()
            _emit({
                "ok": False,
                "phase": sm.phase,
                "errors": errors,
                "next": "report-failed-group",
            })
            return 0
        # 进入 validation 下一轮
        sm.begin_validation()
        _materialize_prompt_validator(sm)
        next_msg = (f"v-fix 全部 ok。请 fork validator-g{gi + 1}-r{sm.round + 1}。")

    else:
        sys.stderr.write(f"未知 phase: {phase}\n")
        return 1

    sm.save()
    plan = _plan_for(sm)
    _emit({
        "ok": not errors,
        "phase": sm.phase,
        "group": sm.current_group_index + 1 if sm.current_group_index < len(sm.groups) else None,
        "round": sm.round,
        "errors": errors,
        "next": next_msg,
        "plan": plan,
    })
    return 0


# -------------------------------------------------------------------------
# writeback
# -------------------------------------------------------------------------

def cmd_writeback(args: argparse.Namespace) -> int:
    try:
        run_dir = _find_run_dir(args.run)
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        return 1
    sm = StateMachine.load(run_dir)
    gi = args.group - 1
    if gi < 0 or gi >= len(sm.groups):
        sys.stderr.write(f"--group {args.group} 越界（共 {len(sm.groups)} 个 group）\n")
        return 1
    # 仅允许当前 group 或先前已 done 的 group 重写
    if gi != sm.current_group_index and sm.group_status[gi] not in ("done",):
        sys.stderr.write(f"--group {args.group} 不是当前 group（current={sm.current_group_index + 1}）\n")
        return 1
    # 组装 findings
    stages = sm.groups[gi]
    stage_numbers = [s.number for s in stages]
    findings: list[StageFinding] = []
    for f in sm.findings:
        sev = f["severity"]
        fix_status = f.get("fix_status", "未修复")
        findings.append(StageFinding(severity=sev, text=f["text"], fix_status=fix_status))
    # validator history 与 reproduce_cmd（取最后 pass 的）
    reproduce_cmd = ""
    final_verdict = "pass"
    if sm.group_status[gi] == "failed-deadloop":
        final_verdict = "failed-deadloop"
    else:
        last_pass = None
        for h in sm.validator_history:
            if h.get("group") == gi + 1 and h.get("verdict") == "pass":
                last_pass = h
                break
        if last_pass is None:
            final_verdict = "pass"
        # reproduce_cmd 从 last pass 对应 validator outbox 取（best effort）
        if last_pass is not None:
            vpath = (Path(sm.run_dir) / "agents"
                     / f"validator-g{gi + 1}-r{last_pass.get('round')}"
                     / "outbox" / "validation.md")
            if vpath.exists():
                try:
                    val = parse_validator_validation(vpath)
                    reproduce_cmd = val.reproduce_cmd
                except ParseError:
                    pass
    gf = GroupFindings(
        group_index=gi,
        stages=stage_numbers,
        findings=findings,
        validator_history=[h for h in sm.validator_history if h.get("group") == gi + 1],
        final_verdict=final_verdict,
        reproduce_cmd=reproduce_cmd,
    )
    try:
        result = writeback_tasks_md(Path(sm.tasks_md), gf)
    except WriteBackError as e:
        sys.stderr.write(f"writeback 越界：{e}\n")
        return 1
    except FileNotFoundError as e:
        sys.stderr.write(f"writeback 失败：{e}\n")
        return 1
    sm.events_append({"type": "writeback", "group": gi + 1,
                      "stages": stage_numbers, "findings": len(findings)})
    if gi == sm.current_group_index:
        sm.finalize_group("done" if final_verdict == "pass" else "failed")
    sm.save()
    _emit({
        "ok": True,
        "tasks_md": str(result.tasks_md_path),
        "stages_checked": result.stages_checked,
        "findings_count": result.findings_count,
        "next_group": sm.current_group_index + 1 if sm.current_group_index < len(sm.groups) else None,
        "phase": sm.phase,
    })
    return 0


# -------------------------------------------------------------------------
# heartbeat
# -------------------------------------------------------------------------

def cmd_heartbeat(args: argparse.Namespace) -> int:
    """透传给 spec_session.py heartbeat 保活 spec 锁。

    本命令本身仅刷新 state.json.last_activity_at；spec 锁刷新由调用方主代理
    单独再调 spec_session.py heartbeat 完成（保持 task_swarm/spec_session 互不 import）。
    """
    try:
        run_dir = _find_run_dir(args.run)
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        return 1
    sm = StateMachine.load(run_dir)
    sm.events_append({"type": "heartbeat"})
    sm.save()
    _emit({
        "ok": True,
        "run_id": sm.run_id,
        "spec_dir": sm.spec_dir,
        "session_id": sm.session_id,
        "hint": ("如需保活 spec 锁，请额外调用 spec_session.py heartbeat "
                 "--spec <dir> --session <id>"),
    })
    return 0


# -------------------------------------------------------------------------
# resolve
# -------------------------------------------------------------------------

def cmd_resolve(args: argparse.Namespace) -> int:
    try:
        run_dir = _find_run_dir(args.run)
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        return 1
    sm = StateMachine.load(run_dir)
    if args.abort:
        sm.failed_status = "aborted"
        sm.phase = "done"
        sm.completed_at = _now_iso()
        sm.events_append({"type": "resolve", "status": "aborted"})
    else:
        sm.completed_at = sm.completed_at or _now_iso()
        sm.failed_status = sm.failed_status or "done"
        sm.events_append({"type": "resolve", "status": sm.failed_status})
    sm.save()
    # 清理 sessions.task_swarm_run_id
    if sm.session_id:
        sess = _read_session(sm.session_id)
        if sess is not None and sess.get("task_swarm_run_id") == sm.run_id:
            sess["task_swarm_run_id"] = None
            sess["last_activity_at"] = _now_iso()
            with contextlib.suppress(Exception):
                _write_session(sm.session_id, sess)
    _emit({
        "ok": True,
        "run_id": sm.run_id,
        "status": sm.failed_status,
        "completed_at": sm.completed_at,
    })
    return 0


# -------------------------------------------------------------------------
# argparse
# -------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="task_swarm.py",
                                description="task-swarm 编排主 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init")
    pi.add_argument("--tasks", required=True)
    pi.add_argument("--max-parallel", type=int, default=4)
    pi.add_argument("--max-rounds", type=int, default=6)
    pi.add_argument("--session", default=None)
    pi.add_argument("--spec", default=None)

    ps = sub.add_parser("status")
    ps.add_argument("--run", required=True)

    pp = sub.add_parser("plan")
    pp.add_argument("--run", required=True)

    pa = sub.add_parser("advance")
    pa.add_argument("--run", required=True)
    pa.add_argument("--phase", required=True,
                    choices=["coding", "review", "p0-fix", "validation", "v-fix"])
    pa.add_argument("--round", type=int, default=1)

    pw = sub.add_parser("writeback")
    pw.add_argument("--run", required=True)
    pw.add_argument("--group", type=int, required=True)

    ph = sub.add_parser("heartbeat")
    ph.add_argument("--run", required=True)

    pr = sub.add_parser("resolve")
    pr.add_argument("--run", required=True)
    pr.add_argument("--abort", action="store_true")

    return p


COMMANDS = {
    "init": cmd_init,
    "status": cmd_status,
    "plan": cmd_plan,
    "advance": cmd_advance,
    "writeback": cmd_writeback,
    "heartbeat": cmd_heartbeat,
    "resolve": cmd_resolve,
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    fn = COMMANDS.get(args.cmd)
    if fn is None:
        parser.print_help()
        return 1
    return fn(args) or 0


def _log_wrap_main(argv: Optional[list[str]] = None) -> int:
    argv_list = list(sys.argv[1:]) if argv is None else list(argv)
    sid = None
    for i, a in enumerate(argv_list):
        if a == "--session" and i + 1 < len(argv_list):
            sid = argv_list[i + 1]
            break
    sub_cmd = argv_list[0] if argv_list else "?"
    with contextlib.suppress(Exception):
        _log_event("cli_call", {"script": "task_swarm.py", "cmd": sub_cmd, "argv_len": len(argv_list)}, session_id=sid)
    rc = main(argv)
    with contextlib.suppress(Exception):
        _log_event("cli_exit", {"script": "task_swarm.py", "cmd": sub_cmd, "exit_code": rc}, session_id=sid)
    return rc


if __name__ == "__main__":
    try:
        sys.exit(_log_wrap_main())
    except KeyboardInterrupt:
        sys.exit(130)
