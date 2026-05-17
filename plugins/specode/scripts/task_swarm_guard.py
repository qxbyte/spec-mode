"""task-swarm hook-side detectors.

Provides the INV-7/8/9/10 invariants exercised by spec_guard.py hooks:

  INV-7  Task tool: subagent_type must carry `specode:task-swarm-*` prefix
         when task-swarm is active.
  INV-8  Edit/Write inside a subagent: target must live in the subagent's
         @writes declaration (parsed from its task.md), and never inside
         the spec_dir.
  INV-9  Edit on tasks.md during task-swarm: diff must only change checkbox
         markers or insert `> ` annotation lines. Anything else (traceability,
         metadata, headings, indent) is rejected.
  INV-10 subagent Stop: outbox file(s) must satisfy the schema parsers in
         task_swarm_outbox; schema-error → deny with explanation.

Each check returns (decision: "ok"|"deny", message). The host hook chooses
how to enforce (PreToolUse deny → return 2; Stop deny → return 2 with msg).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
import sys

sys.path.insert(0, str(SCRIPTS_DIR))

import task_swarm_outbox as outbox_mod  # noqa: E402
import task_swarm_writeback as wb_mod  # noqa: E402


RUNS_DIRNAME = ".task-swarm"
ACTIVE_RUN_FILE = "active-run"
SUBAGENT_PREFIX = "specode:task-swarm-"
VALID_SUBAGENT_TYPES = {
    f"{SUBAGENT_PREFIX}coder",
    f"{SUBAGENT_PREFIX}reviewer",
    f"{SUBAGENT_PREFIX}validator",
    f"{SUBAGENT_PREFIX}planner",
}


# ---------- run discovery ----------

def find_active_run(project_root: Path) -> Optional[Path]:
    pointer = project_root / RUNS_DIRNAME / ACTIVE_RUN_FILE
    if not pointer.exists():
        return None
    run_id = pointer.read_text(encoding="utf-8").strip()
    if not run_id:
        return None
    run_dir = project_root / RUNS_DIRNAME / "runs" / run_id
    return run_dir if run_dir.exists() else None


def is_task_swarm_active(project_root: Path) -> bool:
    return find_active_run(project_root) is not None


# ---------- INV-7 ----------

INV7_MSG = (
    "task-swarm 守卫 (INV-7): subagent_type 必须使用 `{prefix}*` 前缀。\n"
    "当前 subagent_type=`{got}` 在 task-swarm 运行期不被接受。\n"
    "请改为下列之一: {valid}.\n"
    "原因: 没有 plugin 前缀的 agent (如 general-purpose) 会让角色隔离失效。"
)


def check_inv7_subagent_type(subagent_type: str) -> tuple[str, str]:
    if not subagent_type:
        return "deny", INV7_MSG.format(
            prefix=SUBAGENT_PREFIX, got="(空)", valid=", ".join(sorted(VALID_SUBAGENT_TYPES))
        )
    if subagent_type in VALID_SUBAGENT_TYPES:
        return "ok", ""
    return "deny", INV7_MSG.format(
        prefix=SUBAGENT_PREFIX,
        got=subagent_type,
        valid=", ".join(sorted(VALID_SUBAGENT_TYPES)),
    )


# ---------- INV-8 ----------

WRITES_LINE_RE = re.compile(r"^- @writes（你只能修改这些路径）:\s*(.+)$", re.MULTILINE)


def _parse_writes_from_task_md(task_md: Path) -> Optional[list[str]]:
    if not task_md.exists():
        return None
    text = task_md.read_text(encoding="utf-8", errors="replace")
    m = WRITES_LINE_RE.search(text)
    if not m:
        return None
    raw = m.group(1).strip()
    if raw.startswith("(") or "无 @writes" in raw:
        return []
    out = []
    for piece in re.split(r"[,，]", raw):
        piece = piece.strip().strip("`").strip()
        if piece:
            out.append(piece)
    return out


INV8_MSG = (
    "task-swarm 守卫 (INV-8): subagent 越界写入。\n"
    "目标文件 `{target}` 不在本 subagent 声明的 @writes 范围内: {writes}.\n"
    "原因: 物理隔离要求 subagent 只能改自己负责的文件; 越界会污染其他角色的工作区或 spec。"
)

INV8_SPEC_MSG = (
    "task-swarm 守卫 (INV-8): subagent 禁止修改 spec 文档。\n"
    "目标 `{target}` 在 spec 目录 `{spec_dir}` 内。\n"
    "原因: spec 文档由主编排器持锁; subagent 只动业务代码。"
)


def check_inv8_writes_boundary(
    target: Path,
    workspace: Path,
    project_root: Path,
    spec_dir: Path,
) -> tuple[str, str]:
    """target should be either inside workspace/outbox (free) or inside
    project_root and in the workspace's @writes list. Spec dir is always
    forbidden.
    """
    try:
        target_resolved = target.resolve()
    except OSError:
        target_resolved = target

    # workspace outbox is always writable for the subagent
    try:
        target_resolved.relative_to((workspace / "outbox").resolve())
        return "ok", ""
    except (ValueError, OSError):
        pass

    # spec dir is forbidden
    try:
        target_resolved.relative_to(spec_dir.resolve())
        return "deny", INV8_SPEC_MSG.format(target=target, spec_dir=spec_dir)
    except (ValueError, OSError):
        pass

    # outside project_root: ignore (not our jurisdiction)
    try:
        rel = target_resolved.relative_to(project_root.resolve())
    except (ValueError, OSError):
        return "ok", ""

    writes = _parse_writes_from_task_md(workspace / "task.md")
    if writes is None:
        # No task.md or no writes clause — defensive ok (caller may further check)
        return "ok", ""
    rel_str = str(rel)
    for entry in writes:
        if _writes_entry_matches(rel_str, entry):
            return "ok", ""
    return "deny", INV8_MSG.format(target=rel_str, writes=", ".join(writes) or "(无)")


def _writes_entry_matches(rel: str, entry: str) -> bool:
    """Match a target path against an @writes entry.

    Supported forms:
      - exact file:        `src/api/login.py`
      - directory (slash): `src/api/`        → any path under src/api/
      - directory (glob):  `src/api/**`      → any path under src/api/

    Comparison is purely string-based on the POSIX-style relative path; leading
    `./` and trailing whitespace are normalized away.
    """
    e = entry.strip().lstrip("./").strip()
    if not e:
        return False
    if e == rel:
        return True
    if e.endswith("/**"):
        prefix = e[:-3]
        if not prefix.endswith("/"):
            prefix += "/"
        return rel.startswith(prefix)
    if e.endswith("/"):
        return rel.startswith(e)
    return False


# ---------- INV-9 ----------

INV9_MSG = (
    "task-swarm 守卫 (INV-9): 禁止直接编辑 tasks.md。\n"
    "当前差异不安全: {reason}\n"
    "在 task-swarm 运行期回写 tasks.md 必须走 `task_swarm.py writeback`,\n"
    "脚本内部只放行 checkbox 切换 + `> ` 注释行追加, 并自动 verify-lock + heartbeat。"
)


def check_inv9_tasks_md_diff(old_text: str, new_text: str) -> tuple[str, str]:
    safe, reason = wb_mod.diff_safe_line_by_line(old_text, new_text)
    if safe:
        return "ok", ""
    return "deny", INV9_MSG.format(reason=reason)


# ---------- INV-10 (deprecated alias) ----------
#
# Canonical implementation now lives in task_swarm_outbox.validate_outbox_schema.
# INV-10 is enforced by the CLI `parse` subcommand (not a Stop hook). These
# names are preserved to avoid breaking external callers / pre-existing tests.

INV10_MSG = outbox_mod.SCHEMA_ERROR_MSG


def check_inv10_outbox_schema(role: str, outbox_dir: Path) -> tuple[str, str]:
    return outbox_mod.validate_outbox_schema(role, outbox_dir)


# ---------- helpers used by spec_guard handlers ----------

def is_tasks_md(target: Path, spec_dir: Path) -> bool:
    try:
        return target.resolve() == (spec_dir / "tasks.md").resolve()
    except OSError:
        return False
