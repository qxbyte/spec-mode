#!/usr/bin/env python3
"""task_swarm_writeback.py — tasks.md line-safe diff writeback（详见 references/task-swarm.md §5）。

只允许：
    - 在指定 stage 范围内：`- [ ] N.M ...` → `- [x] N.M ...`（仅替换 checkbox 字符）
    - 在该 stage 段末追加 `> ` 注释块

越界 / 修改已有非 checkbox 字符 → WriteBackError → exit 1。

stdlib-only。
"""
from __future__ import annotations

import contextlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# -------------------------------------------------------------------------
# 错误
# -------------------------------------------------------------------------

class WriteBackError(Exception):
    """越界 / 不安全 diff。"""


# -------------------------------------------------------------------------
# 数据结构
# -------------------------------------------------------------------------

@dataclass
class StageFinding:
    """一条 finding（reviewer / advisory），含 fix 状态。"""
    severity: str  # p0 / p1 / p2 / advisory
    text: str  # 原文（不含 leading '- '）
    fix_status: str  # 已修复 / 未修复


@dataclass
class GroupFindings:
    """writeback 时传入的本 group 全部 finding 与 validator 历史。"""
    group_index: int  # 0-based
    stages: list[int]  # group 内 stage 编号
    findings: list[StageFinding] = field(default_factory=list)
    validator_history: list[dict] = field(default_factory=list)
    final_verdict: str = "pass"  # pass / fail / failed-deadloop
    reproduce_cmd: str = ""


@dataclass
class WriteBackResult:
    tasks_md_path: Path
    stages_checked: list[int]
    findings_count: int
    new_text: str


# -------------------------------------------------------------------------
# 原子写
# -------------------------------------------------------------------------

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
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# -------------------------------------------------------------------------
# writeback 核心
# -------------------------------------------------------------------------

_STAGE_HEADER_RE = re.compile(r"^\s*##\s+阶段\s+(\d+)\s*[:：]")
_ITEM_RE = re.compile(r"^(\s*-\s+\[)([ xX])(\]\s+\d+(?:\.\d+)+\s+.*)$")


def _find_stage_ranges(lines: list[str], target_stages: list[int]) -> dict[int, tuple[int, int]]:
    """返回 {stage_number: (start_line_no, end_line_no)}，行号 1-based。"""
    ranges: dict[int, tuple[int, int]] = {}
    current_num: Optional[int] = None
    current_start = 0
    for idx, line in enumerate(lines, start=1):
        m = _STAGE_HEADER_RE.match(line)
        if m:
            n = int(m.group(1))
            if current_num is not None and current_num in target_stages:
                ranges[current_num] = (current_start, idx - 1)
            current_num = n
            current_start = idx
    if current_num is not None and current_num in target_stages:
        ranges[current_num] = (current_start, len(lines))
    return ranges


def _format_findings_block(gf: GroupFindings) -> list[str]:
    """生成单 stage 末尾追加的 `> ` 注释块（list of lines, no trailing newline）。

    格式见 references/task-swarm.md §5 示例。
    """
    out: list[str] = []
    out.append("")  # 空行
    # 顶部 validator 最终结论
    last_pass = None
    for h in gf.validator_history:
        if h.get("verdict") == "pass":
            last_pass = h
            break
    if gf.final_verdict == "pass":
        round_text = ""
        if last_pass is not None:
            round_text = f" g{last_pass.get('group')}-r{last_pass.get('round')}"
        cmd = gf.reproduce_cmd or ""
        cmd_text = f": `{cmd}`" if cmd else ""
        out.append(f"> ✅ validator{round_text} pass{cmd_text}")
    elif gf.final_verdict == "failed-deadloop":
        out.append("> ⚠️ validator failed-deadloop（连续 3 轮同一 fail 签名）；本 group 标 failed")
    else:
        out.append(f"> ❌ validator 最终结论：{gf.final_verdict}")
    out.append(">")
    if gf.findings:
        out.append("> 评审建议（task-swarm reviewer）：")
        for f in gf.findings:
            if f.severity == "advisory":
                tag = f"[adv {f.fix_status}]"
            else:
                tag = f"[{f.severity.upper()} {f.fix_status}]"
            out.append(f"> - {tag} {f.text}")
        out.append(">")
    if gf.validator_history:
        out.append("> validator 历轮：")
        for h in gf.validator_history:
            verdict = h.get("verdict", "?")
            sig = h.get("signature", "")
            tail = ""
            if verdict == "fail" and sig:
                tail = f" — fail signature {sig}"
            out.append(f"> - g{h.get('group')}-r{h.get('round')}: {verdict}{tail}")
    return out


def writeback_tasks_md(
    tasks_md_path: Path,
    group_findings: GroupFindings,
) -> WriteBackResult:
    """对 tasks_md_path 做 line-safe diff：

    1. 仅在 group_findings.stages 列出的 stage 范围内做替换
    2. `- [ ] ...` → `- [x] ...`（仅 checkbox 字符）
    3. 在每个 stage 段末追加注释块（findings 内容相同——挂在 group 最后一个 stage 末尾）
    4. 任何其他越界 diff → WriteBackError
    """
    if not tasks_md_path.exists():
        raise WriteBackError(f"tasks.md 不存在：{tasks_md_path}")
    original = tasks_md_path.read_text(encoding="utf-8")
    # 保留末尾换行符
    trailing_newline = original.endswith("\n")
    lines = original.splitlines()

    target_stages = sorted(group_findings.stages)
    ranges = _find_stage_ranges(lines, target_stages)
    missing = [n for n in target_stages if n not in ranges]
    if missing:
        raise WriteBackError(
            f"tasks.md 中找不到目标 stage：{missing}（请确认 tasks.md 未被外部破坏）"
        )

    new_lines = list(lines)
    # 替换 checkbox
    for n in target_stages:
        start, end = ranges[n]
        for i in range(start - 1, end):
            line = new_lines[i]
            m = _ITEM_RE.match(line)
            if m:
                # 只允许 ' ' → 'x'；其它情况保持不变（已 x 不动）
                if m.group(2) == " ":
                    new_lines[i] = f"{m.group(1)}x{m.group(3)}"

    # 在 group 最后一个 stage 段末追加注释块
    last_stage = target_stages[-1]
    _, last_end = ranges[last_stage]
    block_lines = _format_findings_block(group_findings)
    # 插入位置：last_end 之后（即下个 stage header / 文件末尾前）
    insert_at = last_end
    new_lines = new_lines[:insert_at] + block_lines + new_lines[insert_at:]

    new_text = "\n".join(new_lines) + ("\n" if trailing_newline else "")
    # 安全校验：除允许的 diff 外，其余每行必须与原文逐字相等
    _verify_line_safe(original, new_text, group_findings)
    _atomic_write_text(tasks_md_path, new_text)
    return WriteBackResult(
        tasks_md_path=tasks_md_path,
        stages_checked=target_stages,
        findings_count=len(group_findings.findings),
        new_text=new_text,
    )


def _verify_line_safe(original: str, modified: str, gf: GroupFindings) -> None:
    """二次校验：把 modified 与 original 行级对齐，确认越界没发生。

    允许的 diff：
        A. 同号行：原 `- [ ] N.M ...`，新 `- [x] N.M ...`，其余字符完全相同
        B. modified 比 original 多若干行，且新增行全部以 `>`、空字符串或前缀属于 `> ` 注释块格式
    """
    orig_lines = original.splitlines()
    new_lines = modified.splitlines()

    oi = 0
    ni = 0
    o_len = len(orig_lines)
    n_len = len(new_lines)
    while oi < o_len and ni < n_len:
        o = orig_lines[oi]
        n = new_lines[ni]
        if o == n:
            oi += 1
            ni += 1
            continue
        # 允许 A：checkbox toggle
        mo = _ITEM_RE.match(o)
        mn = _ITEM_RE.match(n)
        if mo and mn and mo.group(1) == mn.group(1) and mo.group(3) == mn.group(3):
            if mo.group(2) == " " and mn.group(2).lower() == "x":
                oi += 1
                ni += 1
                continue
            raise WriteBackError(
                f"writeback 越界：line {oi + 1} checkbox 替换非法 '{mo.group(2)}'→'{mn.group(2)}'"
            )
        # 允许 B：modified 多出 `> ` 注释块或空行
        if (n == "" or n.startswith(">")):
            ni += 1
            continue
        raise WriteBackError(
            f"writeback 越界：line {oi + 1}\n  原: {o!r}\n  新: {n!r}"
        )
    # modified 末尾多出的行必须都是 `> ` / 空行
    while ni < n_len:
        n = new_lines[ni]
        if not (n == "" or n.startswith(">")):
            raise WriteBackError(
                f"writeback 越界：末尾出现非注释行 line {ni + 1}: {n!r}"
            )
        ni += 1
    # original 不能多出来（不允许 writeback 删行）
    while oi < o_len:
        o = orig_lines[oi]
        raise WriteBackError(
            f"writeback 越界：删除了原行 line {oi + 1}: {o!r}"
        )


# -------------------------------------------------------------------------
# 模块自测
# -------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import sys
    print("usage: import this module; see writeback_tasks_md", file=sys.stderr)
