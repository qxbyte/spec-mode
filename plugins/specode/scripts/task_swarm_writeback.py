"""Safe tasks.md writeback for task-swarm.

The orchestrator must NOT directly Edit tasks.md during a swarm run.
This module performs the only sanctioned mutation:

  - flip checkbox state on a known stage / leaf line
  - append `> ` comment lines (audit trail)

Anything else (metadata lines, traceability, headings, indentation) is
preserved bit-for-bit. The matching INV-9 hook acts as a backstop in case
the model bypasses this module.

Convergence semantics:
  - stage.phase == "converged":
      → checkbox for every leaf with subtask_status=done becomes [x]
      → stage's top-level checkbox becomes [x] (if every non-skip leaf done)
      → append `> ✔ 第 R 轮收敛` annotation
  - stage.phase == "failed":
      → leaf checkboxes stay [ ] (or [~] if partially done)
      → top-level stage gets [~]
      → append `> ✗ 已达 N 轮上限仍未收敛` annotation
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


STAGE_LINE_RE = re.compile(r"^(- \[)([ x~*\-])(\] )(\d+)(\. .+)$")
LEAF_LINE_RE = re.compile(r"^(  - \[)([ x~*\-])(\] )(\d+\.\d+)( .+)$")
ANNOTATION_LINE_RE = re.compile(r"^\s*> ")


def _new_marker(stage_phase: str, leaf_status: str | None, optional: bool) -> str:
    """Return the character that should be inside `[ ]` after writeback."""
    if optional:
        return "*"
    if stage_phase == "skipped":
        return "*"
    if leaf_status == "done":
        return "x"
    if leaf_status == "skipped":
        return "*"
    if leaf_status == "failed":
        return " "
    if stage_phase == "converged":
        return "x"
    if stage_phase == "failed":
        return "~"
    return " "


@dataclass
class WritebackPlan:
    stage_num: int
    stage_phase: str
    rounds: dict
    leaves_status: dict[str, str]   # "1.1" → "done" | "failed" | "skipped"
    fail_reason: str = ""
    annotation: str = ""
    # R3: surface reviewer advisory verdict as a `> ⚠️` annotation block.
    # Optional — populated by task_swarm.cmd_writeback when reviewer ran.
    reviewer_summary: dict | None = None


def _annotate(plan: WritebackPlan) -> str:
    if plan.annotation:
        return plan.annotation
    r_val = plan.rounds.get("validator", 0)
    if plan.stage_phase == "converged":
        rounds_str = ""
        if r_val > 1:
            rounds_str = f"（validator {r_val} 轮）"
        return f"> ✔ task-swarm 收敛{rounds_str}"
    if plan.stage_phase == "failed":
        reason = f": {plan.fail_reason}" if plan.fail_reason else ""
        return f"> ✗ task-swarm 未收敛{reason}"
    return ""


def _reviewer_annotations(summary: dict | None) -> list[str]:
    """Build `> ⚠️ ...` annotation lines from a reviewer verdict summary.

    Expected shape (from task_swarm_outbox.parse_review):
      {
        "judgment": "approved" | "p0" | ...,
        "p0_count": int,
        "p0_items": [str, ...],
        "advisory_p0_count": int,
        "advisory_p0_items": [str, ...],
        "conclusion": str,
      }
    Returns one or more annotation lines (each prefixed with `> `), or empty
    list when nothing worth surfacing.
    """
    if not summary:
        return []
    p0 = summary.get("p0_items") or []
    adv = summary.get("advisory_p0_items") or []
    if not p0 and not adv:
        return []
    lines: list[str] = []
    header = "> ⚠️ 评审建议（advisory）："
    counts = []
    if p0:
        counts.append(f"{len(p0)} 条带证据 P0")
    if adv:
        counts.append(f"{len(adv)} 条 advisory")
    lines.append(header + "、".join(counts))
    for item in p0[:5]:
        lines.append(f">   • {item}")
    for item in adv[:3]:
        lines.append(f">   • (adv) {item}")
    if len(p0) > 5 or len(adv) > 3:
        lines.append(">   …（更多见 review.md 完整内容）")
    return lines


def apply_writeback(text: str, plan: WritebackPlan) -> str:
    """Apply plan to tasks.md text. Returns new text.

    Only mutates checkbox characters and appends annotation lines. Never
    touches metadata, headings, traceability, or indentation.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []

    in_stage = False
    stage_block_end_idx: int | None = None
    annotation = _annotate(plan)
    annotation_appended = False

    # First pass: find indices for the target stage block.
    stage_start: int | None = None
    block_end: int | None = None
    for idx, raw in enumerate(lines):
        m = STAGE_LINE_RE.match(raw.rstrip("\n"))
        if m and int(m.group(4)) == plan.stage_num:
            stage_start = idx
            continue
        if stage_start is not None:
            m2 = STAGE_LINE_RE.match(raw.rstrip("\n"))
            if m2:  # next stage starts
                block_end = idx
                break
    if stage_start is None:
        # Stage not found — return text unchanged (defensive).
        return text
    if block_end is None:
        block_end = len(lines)

    for idx, raw in enumerate(lines):
        line_no_nl = raw.rstrip("\n")
        newline = raw[len(line_no_nl):]

        # Stage top-level line
        if idx == stage_start:
            m = STAGE_LINE_RE.match(line_no_nl)
            optional = (m.group(2) == "*")
            # Determine stage marker based on phase + whether any non-failed leaves
            new_marker = _new_marker(plan.stage_phase, None, optional)
            new_line = f"{m.group(1)}{new_marker}{m.group(3)}{m.group(4)}{m.group(5)}{newline}"
            out.append(new_line)
            continue

        # Inside the stage block: handle leaves
        if stage_start < idx < block_end:
            m_leaf = LEAF_LINE_RE.match(line_no_nl)
            if m_leaf:
                leaf_num = m_leaf.group(4)
                leaf_status = plan.leaves_status.get(leaf_num)
                optional = (m_leaf.group(2) == "*")
                new_marker = _new_marker(plan.stage_phase, leaf_status, optional)
                new_line = f"{m_leaf.group(1)}{new_marker}{m_leaf.group(3)}{m_leaf.group(4)}{m_leaf.group(5)}{newline}"
                out.append(new_line)
                continue

        out.append(raw)

    # Append annotation(s) immediately after the stage's last content line.
    annotations_to_insert: list[str] = []
    block_text = "".join(out[stage_start:block_end])
    if annotation and annotation.strip() not in block_text:
        annotations_to_insert.append("  " + annotation + "\n")
    for rev_line in _reviewer_annotations(plan.reviewer_summary):
        if rev_line.strip() not in block_text:
            annotations_to_insert.append("  " + rev_line + "\n")

    if annotations_to_insert:
        insertion_idx = block_end - 1
        while insertion_idx > stage_start and not out[insertion_idx].strip():
            insertion_idx -= 1
        # Insert in reverse so the order in final output matches the list.
        for line in reversed(annotations_to_insert):
            out.insert(insertion_idx + 1, line)

    return "".join(out)


def writeback_to_file(path: Path, plan: WritebackPlan) -> None:
    text = path.read_text(encoding="utf-8")
    new_text = apply_writeback(text, plan)
    if new_text != text:
        tmp = path.with_suffix(path.suffix + ".swarm.tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)


# ---------- diff helper for INV-9 ----------

def _is_checkbox_swap(old: str, new: str) -> bool:
    m_old = re.match(r"^(\s*- \[)[ x~*\-](\].*)$", old)
    m_new = re.match(r"^(\s*- \[)[ x~*\-](\].*)$", new)
    if not m_old or not m_new:
        return False
    return m_old.group(1) == m_new.group(1) and m_old.group(2) == m_new.group(2)


def diff_safe_line_by_line(old_text: str, new_text: str) -> tuple[bool, str]:
    """Simpler line-by-line diff: pair lines positionally where possible.

    Strategy: compute a unified diff with `difflib.SequenceMatcher`, then for
    each `replace` block ensure every (old, new) pair is a checkbox swap; for
    every `insert` ensure inserted lines are annotation/blank; for every
    `delete` reject if any line is non-blank-non-annotation.
    """
    import difflib
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            old_chunk = old_lines[i1:i2]
            new_chunk = new_lines[j1:j2]
            if len(old_chunk) != len(new_chunk):
                return False, f"禁止替换段长度不一致: {old_chunk!r} → {new_chunk!r}"
            for o, n in zip(old_chunk, new_chunk):
                if o == n:
                    continue
                if _is_checkbox_swap(o, n):
                    continue
                return False, f"禁止改动非 checkbox 内容: {o!r} → {n!r}"
        elif tag == "insert":
            for n in new_lines[j1:j2]:
                if not n.strip():
                    continue
                if ANNOTATION_LINE_RE.match(n):
                    continue
                return False, f"禁止插入非注释行: {n!r}"
        elif tag == "delete":
            for o in old_lines[i1:i2]:
                if not o.strip():
                    continue
                if ANNOTATION_LINE_RE.match(o):
                    continue
                return False, f"禁止删除非空非注释行: {o!r}"
    return True, ""


if __name__ == "__main__":
    sys.stderr.write("task_swarm_writeback is a library module; use task_swarm.py CLI.\n")
    sys.exit(0)
