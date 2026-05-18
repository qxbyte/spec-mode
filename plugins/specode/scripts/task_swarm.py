"""task-swarm orchestrator CLI.

Subcommands:
  init       — parse tasks.md, build run workspace + state.json
  next       — return JSON instruction for the next step (fork|writeback|wait|done)
  parse      — read a subagent's outbox, return structured verdict
  advance    — record verdict into state.json
  writeback  — safely flip tasks.md checkboxes for a converged/failed stage
  status     — print human-readable run status
  resolve    — resolve a run (latest run for spec or by id)

The orchestrator (main Claude session) only calls these subcommands and
acts on their JSON output — it doesn't recreate state-machine logic or
parse tasks.md itself.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import task_swarm_parse_md as plan_mod  # noqa: E402
import task_swarm_state as state_mod  # noqa: E402
import task_swarm_outbox as outbox_mod  # noqa: E402
import task_swarm_prompt as prompt_mod  # noqa: E402
import task_swarm_writeback as wb_mod  # noqa: E402


RUNS_DIRNAME = ".task-swarm"

# Emitted commands consumed by the orchestrating model (run via Bash). Use the
# python launcher so Windows hosts that only ship `python` / `py` still work.
SELF_CMD = (
    "sh ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh "
    "${CLAUDE_PLUGIN_ROOT}/scripts/" + Path(__file__).name
)


# ---------- run discovery ----------

def runs_root(project_root: Path) -> Path:
    return project_root / RUNS_DIRNAME / "runs"


def resolve_run_dir(project_root: Path, run_id: Optional[str]) -> Path:
    root = runs_root(project_root)
    if run_id:
        return root / run_id
    if not root.exists():
        raise FileNotFoundError("尚无任何 task-swarm 运行 (.task-swarm/runs/ 不存在)")
    candidates = sorted(p for p in root.iterdir() if p.is_dir())
    if not candidates:
        raise FileNotFoundError("尚无任何 task-swarm 运行")
    # latest by name (timestamp prefix sorts correctly)
    return candidates[-1]


def _print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ---------- init ----------

def cmd_init(args: argparse.Namespace) -> int:
    tasks_path = Path(args.tasks).expanduser().resolve()
    if not tasks_path.exists():
        _print_json({"error": f"tasks.md 不存在: {tasks_path}"})
        return 2
    spec_dir = tasks_path.parent
    project_root = Path(args.project_root or os.getcwd()).expanduser().resolve()
    session_id = args.session or os.environ.get("TERM_SESSION_ID") or ""

    text = tasks_path.read_text(encoding="utf-8")
    plan = plan_mod.parse_tasks_md(text).to_dict()

    run_id = state_mod.new_run_id()
    run_dir = runs_root(project_root) / run_id
    (run_dir / "agents").mkdir(parents=True, exist_ok=True)

    state = state_mod.build_initial_state(
        run_id=run_id,
        tasks_path=tasks_path,
        spec_dir=spec_dir,
        project_root=project_root,
        plan=plan,
        parallel=int(args.parallel),
        max_rounds=int(args.max_rounds),
        reviewer_max_rounds=int(args.reviewer_rounds) if args.reviewer_rounds is not None else None,
        validator_max_rounds=int(args.validator_rounds) if args.validator_rounds is not None else None,
        session_id=session_id,
    )
    state_mod.save_state(run_dir, state)

    # Touch active-run pointer for UserPromptSubmit hook discovery.
    pointer_dir = project_root / RUNS_DIRNAME
    pointer_dir.mkdir(parents=True, exist_ok=True)
    (pointer_dir / "active-run").write_text(run_id, encoding="utf-8")

    _print_json({
        "run_id": run_id,
        "run_dir": str(run_dir),
        "tasks_path": str(tasks_path),
        "spec_dir": str(spec_dir),
        "project_root": str(project_root),
        "stages": [
            {"num": s["num"], "title": s["title"], "kind": s["kind"], "phase": s["phase"]}
            for s in state["stages"]
        ],
        "warnings": state.get("warnings") or [],
        "next": f"{SELF_CMD} next --run {run_id}",
    })
    return 0


# ---------- next ----------

def cmd_next(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root or os.getcwd()).expanduser().resolve()
    run_dir = resolve_run_dir(project_root, args.run)
    state = state_mod.load_state(run_dir)
    action = state_mod.next_action(state)
    payload = action.to_dict()

    if action.kind == "fork":
        stage_num = payload["stage"]
        role = payload["role"]
        round_no = payload["round"]
        stage = state_mod.get_stage(state, stage_num)
        ws = prompt_mod.prepare_workspace(run_dir, stage_num, role, round_no)

        # Relay upstream artifacts into inbox per role.
        sources: list[tuple[int, str, int, str]] = []
        if role == "reviewer":
            # Advisory reviewer: just needs the latest coder output.
            sources.append((stage_num, "coder", round_no, "result.md"))
        elif role == "validator":
            # Checkpoint: pull latest coder output of the validated stage.
            if stage["kind"] == "checkpoint" and stage.get("checkpoint_for"):
                src_stage = stage["checkpoint_for"]
                sources.append((src_stage, "coder", _max_round_for(state, src_stage, "coder"), "upstream-result.md"))
            else:
                # Normal-stage validator path (kept for forward-compat; not
                # currently scheduled by next_action).
                sources.append((stage_num, "coder", round_no, "result.md"))
            if round_no > 1:
                # Re-run after a coder fix — give validator the previous fail report
                sources.append((stage_num, "validator", round_no - 1, "prev-validation.md"))
        elif role == "coder" and round_no > 1:
            # fix round — only validator-fail-fix exists post-R3
            sources.append((stage_num, "coder", round_no - 1, "prev-result.md"))
            if payload.get("scope") == "validator-fail-fix":
                sources.append((stage_num, "validator", round_no - 1, "validation.md"))

        prompt_mod.relay_inbox(run_dir, ws, sources)

        ctx = prompt_mod.StageContext(
            stage_num=stage_num,
            stage_title=stage["title"],
            stage_kind=stage["kind"],
            leaves=stage["leaves"],
            spec_dir=Path(state["spec_dir"]),
            project_root=Path(state["project_root"]),
            workspace=ws,
            round_no=round_no,
            scope=payload.get("scope") or "",
        )
        task_md = prompt_mod.write_task_file(ctx, role)

        # Mark in-flight
        state_mod.mark_in_flight(state, stage_num, role, round_no)
        state_mod.save_state(run_dir, state)

        payload.update({
            "subagent_type": f"specode:task-swarm-{role}",
            "description": f"阶段 {stage_num} {role}{('-r' + str(round_no)) if round_no > 1 else ''}: {stage['title']}",
            "workspace": str(ws),
            "prompt_file": str(task_md),
            "after_fork": (
                f"{SELF_CMD} parse "
                f"--run {state['run_id']} --stage {stage_num} --role {role} --round {round_no}"
            ),
        })

    elif action.kind == "writeback":
        payload["cmd"] = (
            f"{SELF_CMD} writeback "
            f"--run {state['run_id']} --stage {payload['stage']}"
        )

    _print_json(payload)
    return 0


def _max_round_for(state: dict, stage_num: int, role: str) -> int:
    stage = state_mod.get_stage(state, stage_num)
    rounds = [h["round"] for h in stage.get("history", []) if h["role"] == role]
    return max(rounds) if rounds else 1


# ---------- parse ----------

def cmd_parse(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root or os.getcwd()).expanduser().resolve()
    run_dir = resolve_run_dir(project_root, args.run)
    ws = prompt_mod.agent_workspace(run_dir, int(args.stage), args.role, int(args.round))
    outbox = ws / "outbox"
    result = outbox_mod.parse_outbox(args.role, outbox)
    result["workspace"] = str(ws)

    if result.get("judgment") == "schema-error":
        # R5: on schema-error, snapshot the malformed outbox + clear it so the
        # next fork starts from a clean slate. Do NOT include advance_cmd —
        # subagent must be re-forked at the same stage/role/round.
        snapshot: dict[str, str] = {}
        if outbox.exists():
            for f in sorted(outbox.iterdir()):
                if not f.is_file():
                    continue
                try:
                    snapshot[f.name] = f.read_text(encoding="utf-8", errors="replace")[:2000]
                except OSError:
                    continue
                try:
                    f.unlink()
                except OSError:
                    pass
        # Clear in_flight so the next `next` call can re-dispatch this fork.
        state = state_mod.load_state(run_dir)
        stage = state_mod.get_stage(state, int(args.stage))
        if stage.get("in_flight"):
            stage["in_flight"] = None
            state_mod.save_state(run_dir, state)
        result["retry"] = True
        result["outbox_snapshot"] = snapshot
        result["next"] = (
            f"重新派发 subagent: stage={args.stage} role={args.role} round={args.round}. "
            f"outbox 已清空、in_flight 已重置。下一次 `next` 会再次给出同一 fork 指令；"
            f"把 outbox_snapshot 中的内容粘到 subagent prompt 提示它上次为何被拒。"
        )
    else:
        result["advance_cmd"] = (
            f"{SELF_CMD} advance "
            f"--run {state_mod.load_state(run_dir)['run_id']} "
            f"--stage {args.stage} --role {args.role} --round {args.round} "
            f"--judgment {result['judgment']}"
        )

    _print_json(result)
    return 0


# ---------- advance ----------

def cmd_advance(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root or os.getcwd()).expanduser().resolve()
    run_dir = resolve_run_dir(project_root, args.run)
    state = state_mod.load_state(run_dir)

    extra: dict = {}
    if args.note:
        extra["note"] = args.note
    if args.reason:
        extra["reason"] = args.reason

    # Re-parse outbox so the history record captures role-specific detail
    # (subtasks for coder, p0_items / advisory_p0 for reviewer, fix_files for
    # validator). writeback uses these to render annotations.
    try:
        ws = prompt_mod.agent_workspace(run_dir, int(args.stage), args.role, int(args.round))
        parsed = outbox_mod.parse_outbox(args.role, ws / "outbox")
        for key in (
            "subtasks", "fix_files", "p0_items", "p0_count",
            "advisory_p0_items", "advisory_p0_count", "conclusion",
            "fix_guidance", "loop_warning",
        ):
            if key in parsed and parsed[key] not in (None, "", [], 0):
                extra[key] = parsed[key]
    except Exception:
        # If outbox is missing or unreadable, fall through with bare verdict.
        pass

    try:
        stage = state_mod.advance(
            state, int(args.stage), args.role, int(args.round), args.judgment, extra
        )
    except ValueError as e:
        _print_json({"error": str(e)})
        return 2

    state_mod.save_state(run_dir, state)
    _print_json({
        "stage": stage["num"],
        "phase": stage["phase"],
        "rounds": stage["rounds"],
        "last": stage["last"],
        "fail_reason": stage.get("fail_reason"),
        "next": f"{SELF_CMD} next --run {state['run_id']}",
    })
    return 0


# ---------- writeback ----------

def cmd_writeback(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root or os.getcwd()).expanduser().resolve()
    run_dir = resolve_run_dir(project_root, args.run)
    state = state_mod.load_state(run_dir)
    stage = state_mod.get_stage(state, int(args.stage))
    tasks_path = Path(state["tasks_path"])
    if not tasks_path.exists():
        _print_json({"error": f"tasks.md 不存在: {tasks_path}"})
        return 2

    if stage["phase"] not in {"converged", "failed"}:
        _print_json({"error": f"stage {stage['num']} 尚未收敛 (phase={stage['phase']})", "skip": True})
        return 2

    # Verify-lock + heartbeat before write.
    warnings: list[str] = []
    try:
        import spec_session
        verify = spec_session.verify_and_heartbeat(
            Path(state["spec_dir"]), state.get("session_id") or ""
        )
        if verify.get("status") == "evicted":
            _print_json({"error": "lock evicted", "verify": verify})
            return 3
    except SystemExit as e:
        # spec_session.load_config raises SystemExit when .config.json absent —
        # treat as "no lock model" and proceed.
        warnings.append(f"verify-lock skipped (no spec config): {e}")
    except Exception as e:
        # non-fatal — lock model may not be in effect for this spec
        warnings.append(f"verify-lock skipped: {e}")

    # Build writeback plan from history.
    leaves_status: dict[str, str] = {}
    reviewer_summary: dict | None = None
    for record in stage.get("history", []):
        if record["role"] == "coder" and record.get("subtasks"):
            for st in record["subtasks"]:
                leaves_status[st["num"]] = st["status"]
        elif record["role"] == "coder" and stage["phase"] == "converged":
            # No subtask data — assume all done.
            for leaf in stage["leaves"]:
                if leaf.get("policy") != "skip":
                    leaves_status.setdefault(leaf["num"], "done")
        # R3: capture the latest reviewer verdict (advisory) for annotation.
        if record["role"] == "reviewer":
            reviewer_summary = {
                "judgment": record.get("judgment"),
                "p0_count": record.get("p0_count", 0),
                "p0_items": record.get("p0_items", []),
                "advisory_p0_count": record.get("advisory_p0_count", 0),
                "advisory_p0_items": record.get("advisory_p0_items", []),
                "conclusion": record.get("conclusion", ""),
            }

    plan = wb_mod.WritebackPlan(
        stage_num=stage["num"],
        stage_phase=stage["phase"],
        rounds=stage["rounds"],
        leaves_status=leaves_status,
        fail_reason=stage.get("fail_reason") or "",
        reviewer_summary=reviewer_summary,
    )

    text = tasks_path.read_text(encoding="utf-8")
    new_text = wb_mod.apply_writeback(text, plan)

    safe, reason = wb_mod.diff_safe_line_by_line(text, new_text)
    if not safe:
        _print_json({"error": f"writeback diff 不安全: {reason}"})
        return 2

    tmp = tasks_path.with_suffix(".md.swarm.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(tasks_path)

    state_mod.mark_written_back(state, stage["num"])
    state_mod.save_state(run_dir, state)

    payload = {
        "stage": stage["num"],
        "phase": stage["phase"],
        "written": True,
        "next": f"{SELF_CMD} next --run {state['run_id']}",
    }
    if warnings:
        payload["warnings"] = warnings
    _print_json(payload)
    return 0


# ---------- status ----------

def cmd_status(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root or os.getcwd()).expanduser().resolve()
    try:
        run_dir = resolve_run_dir(project_root, args.run)
    except FileNotFoundError as e:
        _print_json({"error": str(e)})
        return 2
    state = state_mod.load_state(run_dir)
    if args.json:
        _print_json(state_mod.summarize(state))
        return 0
    print(f"task-swarm run: {state['run_id']}")
    print(f"tasks_path:     {state['tasks_path']}")
    print(f"spec_dir:       {state['spec_dir']}")
    print(f"max_rounds:     {state['config']['max_rounds']}  parallel: {state['config']['parallel']}")
    print()
    for s in state["stages"]:
        marker = {
            "pending": "○",
            "running": "▶",
            "converged": "✔",
            "failed": "✗",
            "skipped": "—",
        }.get(s["phase"], "?")
        rounds = s["rounds"]
        rstr = f"r:{rounds.get('reviewer',0)} v:{rounds.get('validator',0)}"
        print(f"  {marker} stage {s['num']:>2} [{s['kind']:<10}] {s['phase']:<10} {rstr}  — {s['title']}")
        if s.get("fail_reason"):
            print(f"      fail: {s['fail_reason']}")
    return 0


# ---------- reset-in-flight ----------

def cmd_reset_in_flight(args: argparse.Namespace) -> int:
    """Clear in_flight on one or all stages — recovery after a subagent vanished.

    Without --stage: clears every stage's in_flight marker.
    """
    project_root = Path(args.project_root or os.getcwd()).expanduser().resolve()
    try:
        run_dir = resolve_run_dir(project_root, args.run)
    except FileNotFoundError as e:
        _print_json({"error": str(e)})
        return 2
    state = state_mod.load_state(run_dir)

    cleared: list[dict] = []
    if args.stage is None:
        for s in state["stages"]:
            if s.get("in_flight"):
                cleared.append({"stage": s["num"], "prev": s["in_flight"]})
                s["in_flight"] = None
    else:
        stage = state_mod.get_stage(state, int(args.stage))
        if stage.get("in_flight"):
            cleared.append({"stage": stage["num"], "prev": stage["in_flight"]})
            stage["in_flight"] = None
    state_mod.save_state(run_dir, state)
    _print_json({"cleared": cleared, "count": len(cleared)})
    return 0


# ---------- resolve ----------

def cmd_resolve(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root or os.getcwd()).expanduser().resolve()
    try:
        run_dir = resolve_run_dir(project_root, args.run)
    except FileNotFoundError as e:
        _print_json({"error": str(e)})
        return 2
    _print_json({
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "exists": run_dir.exists(),
    })
    return 0


# ---------- main ----------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="task_swarm.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="解析 tasks.md，初始化 run + state.json")
    p.add_argument("--tasks", required=True, help="tasks.md 绝对路径")
    p.add_argument("--project-root", default=None)
    p.add_argument("--parallel", default=3, type=int)
    p.add_argument(
        "--max-rounds", default=3, type=int,
        help="所有循环角色（目前仅 validator）的 fallback 上限",
    )
    p.add_argument(
        "--reviewer-rounds", default=None, type=int,
        help="（已弃用）reviewer 现在是 advisory，不再参与修复循环。保留参数仅为兼容旧脚本",
    )
    p.add_argument(
        "--validator-rounds", default=3, type=int,
        help="validator fail 修复循环上限（默认 3 — 测试驱动的修复有客观信号）",
    )
    p.add_argument("--session", default=None, help="spec session id（缺省取 TERM_SESSION_ID）")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("next", help="返回下一步指令 JSON")
    p.add_argument("--run", default=None, help="run id（缺省取最新）")
    p.add_argument("--project-root", default=None)
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("parse", help="解析 subagent outbox，返回结构化判定")
    p.add_argument("--run", default=None)
    p.add_argument("--stage", required=True)
    p.add_argument("--role", required=True, choices=["coder", "reviewer", "validator"])
    p.add_argument("--round", required=True)
    p.add_argument("--project-root", default=None)
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("advance", help="记录 verdict 到 state.json")
    p.add_argument("--run", default=None)
    p.add_argument("--stage", required=True)
    p.add_argument("--role", required=True, choices=["coder", "reviewer", "validator"])
    p.add_argument("--round", required=True)
    p.add_argument("--judgment", required=True)
    p.add_argument("--note", default="")
    p.add_argument("--reason", default="")
    p.add_argument("--project-root", default=None)
    p.set_defaults(func=cmd_advance)

    p = sub.add_parser("writeback", help="安全回写 tasks.md")
    p.add_argument("--run", default=None)
    p.add_argument("--stage", required=True)
    p.add_argument("--project-root", default=None)
    p.set_defaults(func=cmd_writeback)

    p = sub.add_parser("status", help="打印 run 状态")
    p.add_argument("--run", default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--project-root", default=None)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("resolve", help="解析 run dir 路径")
    p.add_argument("--run", default=None)
    p.add_argument("--project-root", default=None)
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser(
        "reset-in-flight",
        help="清理 in_flight 标记（subagent 崩溃/超时后的恢复手段）",
    )
    p.add_argument("--run", default=None)
    p.add_argument("--stage", default=None, help="留空清理所有 stage")
    p.add_argument("--project-root", default=None)
    p.set_defaults(func=cmd_reset_in_flight)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
