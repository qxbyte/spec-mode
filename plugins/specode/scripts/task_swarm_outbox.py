"""Outbox schema parsers for task-swarm.

Parses the three subagent outputs into structured verdicts:
  - result.md   (coder)      → judgment ∈ {ok, failed, blocked}
  - review.md   (reviewer)   → judgment ∈ {approved, p0, loop}
  - validation.md (validator)→ judgment ∈ {pass, fail, loop}

When a required section is missing the judgment becomes "schema-error".
The orchestrator should re-fork the subagent with a clarifying note rather
than try to interpret malformed output.

This is the moat between subagent-side hallucination and orchestrator-side
state machine: any output that doesn't fit the schema is rejected upstream.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# ---------- shared ----------

STATUS_TAIL_RE = re.compile(r"^STATUS:\s*(ok|failed|blocked)(?::\s*(.+))?\s*$", re.IGNORECASE)
LOOP_WARNING_RE = re.compile(r"^## 进入死循环风险\s*$", re.MULTILINE)


def _last_status_line(text: str) -> tuple[str | None, str]:
    """Return (status, reason) parsed from the *strict last non-empty line*.

    STATUS must appear on the last meaningful line. If the last non-empty line
    doesn't match, return (None, ""). Even if STATUS appears earlier in the
    body, we don't accept it — avoids "STATUS in the middle" leakage.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None, ""
    m = STATUS_TAIL_RE.match(lines[-1].strip())
    if not m:
        return None, ""
    return m.group(1).lower(), (m.group(2) or "").strip()


def _section(text: str, heading: str) -> str | None:
    """Return body text of `## heading` section, or None if absent.

    Body ends at the next `## ` heading or end of file.
    """
    pat = re.compile(rf"^## {re.escape(heading)}\s*$", re.MULTILINE)
    m = pat.search(text)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"^## ", text[start:], re.MULTILINE)
    end = start + nxt.start() if nxt else len(text)
    return text[start:end].strip()


# ---------- result.md (coder) ----------

SUBTASK_LINE_RE = re.compile(
    r"^[-*]\s*(?P<num>\d+\.\d+)\s+(?P<title>.+?):\s*(?P<status>done|failed|skipped)(?:\s*[—-]\s*(?P<note>.+))?\s*$"
)


@dataclass
class ResultVerdict:
    judgment: str       # ok | failed | blocked | schema-error
    status_reason: str = ""
    subtasks: list[dict] = field(default_factory=list)
    fix_files: list[str] = field(default_factory=list)   # parsed from "P0 修复清单" or note paths
    raw_errors: list[str] = field(default_factory=list)


def parse_result(text: str) -> ResultVerdict:
    errs: list[str] = []
    status, reason = _last_status_line(text)
    if status is None:
        errs.append("缺少末行 STATUS: ok|failed|blocked")

    subtasks_section = _section(text, "子任务状态")
    if subtasks_section is None:
        errs.append("缺少 `## 子任务状态` 节")
        subtasks = []
    else:
        subtasks = []
        for line in subtasks_section.splitlines():
            m = SUBTASK_LINE_RE.match(line.strip())
            if m:
                subtasks.append({
                    "num": m.group("num"),
                    "title": m.group("title").strip(),
                    "status": m.group("status").lower(),
                    "note": (m.group("note") or "").strip(),
                })
        if not subtasks:
            errs.append("`## 子任务状态` 节为空或格式不符 `- N.M 标题: done|failed|skipped`")

    # Fix files from "P0 修复清单" (修复轮 result.md) — best effort.
    fix_files: list[str] = []
    p0_section = _section(text, "P0 修复清单")
    if p0_section:
        for line in p0_section.splitlines():
            m = re.search(r"`?([\w./\\_-]+\.[a-zA-Z0-9]+)(?::\d+)?`?", line)
            if m:
                fix_files.append(m.group(1))

    if errs:
        return ResultVerdict(judgment="schema-error", raw_errors=errs, subtasks=subtasks, fix_files=fix_files)

    return ResultVerdict(
        judgment=status or "schema-error",
        status_reason=reason,
        subtasks=subtasks,
        fix_files=fix_files,
    )


# ---------- review.md (reviewer) ----------

@dataclass
class ReviewVerdict:
    judgment: str           # approved | p0 | loop | schema-error
    p0_count: int = 0
    p0_items: list[str] = field(default_factory=list)
    advisory_p0_count: int = 0
    advisory_p0_items: list[str] = field(default_factory=list)
    loop_warning: bool = False
    conclusion: str = ""
    raw_errors: list[str] = field(default_factory=list)


# Evidence tag regex for blocking P0 items. Only P0 lines carrying one of
# these tags trigger the coder fix loop; everything else is recorded as an
# advisory (audit-only) so reviewer's subjective opinions can't kick off an
# adversarial bounce-back loop.
P0_EVIDENCE_TAG_RE = re.compile(
    r"\[(req:[\w.\-]+|security|contract)\]",
    re.IGNORECASE,
)


def parse_review(text: str) -> ReviewVerdict:
    errs: list[str] = []
    loop = bool(LOOP_WARNING_RE.search(text))

    status, _ = _last_status_line(text)
    if status != "ok":
        errs.append("缺少末行 STATUS: ok")

    conclusion_section = _section(text, "结论")
    if conclusion_section is None:
        errs.append("缺少 `## 结论` 节")
        conclusion = ""
    else:
        conclusion = conclusion_section.splitlines()[0].strip() if conclusion_section else ""

    p0_section = _section(text, "P0 — 阻塞，coder 必须修复（修完才能进 validator）")
    if p0_section is None:
        # Try short heading variants subagent might use.
        for alt in ("P0 — 阻塞", "P0"):
            p0_section = _section(text, alt)
            if p0_section is not None:
                break
    if p0_section is None:
        errs.append("缺少 `## P0` 节（即便无 P0 也需写 (none)）")
        all_p0_items: list[str] = []
    else:
        all_p0_items = []
        for line in p0_section.splitlines():
            s = line.strip()
            if not s or s.startswith("("):
                # `(none)` and blank lines skip
                continue
            if s.startswith("- "):
                all_p0_items.append(s[2:].strip())

    # Split P0 items into blocking (with evidence tag) vs advisory (no tag).
    # Only blocking P0 triggers the coder fix loop — advisory P0 is recorded
    # for audit but doesn't bounce code back to coder.
    blocking_p0: list[str] = []
    advisory_p0: list[str] = []
    for item in all_p0_items:
        if P0_EVIDENCE_TAG_RE.search(item):
            blocking_p0.append(item)
        else:
            advisory_p0.append(item)

    if errs:
        return ReviewVerdict(
            judgment="schema-error",
            loop_warning=loop,
            conclusion=conclusion,
            p0_count=len(blocking_p0),
            p0_items=blocking_p0,
            advisory_p0_count=len(advisory_p0),
            advisory_p0_items=advisory_p0,
            raw_errors=errs,
        )

    if loop:
        return ReviewVerdict(
            judgment="loop",
            loop_warning=True,
            conclusion=conclusion,
            p0_count=len(blocking_p0),
            p0_items=blocking_p0,
            advisory_p0_count=len(advisory_p0),
            advisory_p0_items=advisory_p0,
        )

    return ReviewVerdict(
        judgment="p0" if blocking_p0 else "approved",
        loop_warning=False,
        conclusion=conclusion,
        p0_count=len(blocking_p0),
        p0_items=blocking_p0,
        advisory_p0_count=len(advisory_p0),
        advisory_p0_items=advisory_p0,
    )


# ---------- validation.md (validator) ----------

JUDGMENT_LINE_RE = re.compile(r"^(pass|fail)\b", re.IGNORECASE)


@dataclass
class ValidationVerdict:
    judgment: str         # pass | fail | loop | schema-error
    loop_warning: bool = False
    fix_files: list[str] = field(default_factory=list)
    fix_guidance: str = ""
    raw_errors: list[str] = field(default_factory=list)


def parse_validation(text: str) -> ValidationVerdict:
    errs: list[str] = []
    loop = bool(LOOP_WARNING_RE.search(text))

    status, _ = _last_status_line(text)
    if status != "ok":
        errs.append("缺少末行 STATUS: ok")

    judg_section = _section(text, "判定")
    if judg_section is None:
        errs.append("缺少 `## 判定` 节")
        verdict = None
    else:
        first = judg_section.splitlines()[0].strip().lower() if judg_section else ""
        m = JUDGMENT_LINE_RE.match(first)
        if not m:
            errs.append("`## 判定` 首行必须是 `pass` 或 `fail`")
            verdict = None
        else:
            verdict = m.group(1).lower()

    repro = _section(text, "复现命令")
    if repro is None:
        errs.append("缺少 `## 复现命令` 节")

    fix_files: list[str] = []
    fix_guidance = ""
    if verdict == "fail":
        guidance = _section(text, "给 coder 的修复指引（必填）") or _section(text, "给 coder 的修复指引")
        if not guidance:
            errs.append("validator fail 时必须有 `## 给 coder 的修复指引` 节")
        else:
            fix_guidance = guidance
            for line in guidance.splitlines():
                m = re.match(r"^[-*]\s*(?:文件|file)[：:]\s*(.+)$", line.strip(), re.IGNORECASE)
                if m:
                    fix_files.append(m.group(1).strip().strip("`"))

    if errs:
        return ValidationVerdict(
            judgment="schema-error",
            loop_warning=loop,
            fix_files=fix_files,
            fix_guidance=fix_guidance,
            raw_errors=errs,
        )
    if loop:
        return ValidationVerdict(
            judgment="loop",
            loop_warning=True,
            fix_files=fix_files,
            fix_guidance=fix_guidance,
        )
    return ValidationVerdict(
        judgment=verdict or "schema-error",
        loop_warning=False,
        fix_files=fix_files,
        fix_guidance=fix_guidance,
    )


# ---------- dispatch ----------

SCHEMA_ERROR_MSG = (
    "task-swarm 守卫 (INV-10): subagent outbox 不符合 schema。\n"
    "{role}/{fname}:\n  {errors}\n"
    "请重新生成 outbox/{fname} —— 主编排器靠固定 schema 解析判定, 偏离会导致状态机误判。"
)


def validate_outbox_schema(role: str, outbox_dir: Path) -> tuple[str, str]:
    """Schema gate used by the CLI parse subcommand (R1: CLI-side enforcement
    of INV-10, not a hook).

    Returns ("ok", "") when the outbox parses cleanly, ("deny", message) when
    schema-error is raised. Keep the function signature compatible with the
    deprecated task_swarm_guard alias for backward compatibility.
    """
    fname_map = {"coder": "result.md", "reviewer": "review.md", "validator": "validation.md"}
    fname = fname_map.get(role)
    if not fname:
        return "ok", ""
    result = parse_outbox(role, outbox_dir)
    if result.get("judgment") != "schema-error":
        return "ok", ""
    errors = result.get("errors") or ["未知错误"]
    return "deny", SCHEMA_ERROR_MSG.format(role=role, fname=fname, errors="\n  ".join(errors))


def parse_outbox(role: str, outbox_dir: Path) -> dict:
    """Parse the appropriate file in outbox_dir for the given role.

    Returns a JSON-safe dict with `judgment`, `errors`, and role-specific fields.
    Missing file → schema-error with explanatory message.
    """
    filename_map = {
        "coder": "result.md",
        "reviewer": "review.md",
        "validator": "validation.md",
    }
    fname = filename_map.get(role)
    if not fname:
        return {"judgment": "schema-error", "errors": [f"未知角色: {role}"]}
    fpath = outbox_dir / fname
    if not fpath.exists():
        return {"judgment": "schema-error", "errors": [f"未找到 outbox/{fname}"]}
    text = fpath.read_text(encoding="utf-8", errors="replace")

    if role == "coder":
        v = parse_result(text)
        return {
            "role": "coder",
            "judgment": v.judgment,
            "errors": v.raw_errors,
            "status_reason": v.status_reason,
            "subtasks": v.subtasks,
            "fix_files": v.fix_files,
        }
    if role == "reviewer":
        v = parse_review(text)
        return {
            "role": "reviewer",
            "judgment": v.judgment,
            "errors": v.raw_errors,
            "p0_count": v.p0_count,
            "p0_items": v.p0_items,
            "advisory_p0_count": v.advisory_p0_count,
            "advisory_p0_items": v.advisory_p0_items,
            "loop_warning": v.loop_warning,
            "conclusion": v.conclusion,
        }
    if role == "validator":
        v = parse_validation(text)
        return {
            "role": "validator",
            "judgment": v.judgment,
            "errors": v.raw_errors,
            "fix_files": v.fix_files,
            "fix_guidance": v.fix_guidance,
            "loop_warning": v.loop_warning,
        }
    return {"judgment": "schema-error", "errors": [f"未知角色: {role}"]}
