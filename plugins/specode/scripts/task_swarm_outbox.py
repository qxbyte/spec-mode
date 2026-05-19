#!/usr/bin/env python3
"""task_swarm_outbox.py — 解析 3 类子代理产物：result.md / review.md / validation.md。

按 references/task-swarm.md §4 schema 严格校验；schema 错误返回 ParseError with 详细 reason。

stdlib-only。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# -------------------------------------------------------------------------
# 异常
# -------------------------------------------------------------------------

class ParseError(Exception):
    """schema 校验错误。message 必须含具体 reason 供主代理决策重派。"""


# -------------------------------------------------------------------------
# 数据结构 — coder
# -------------------------------------------------------------------------

@dataclass
class CoderSubtaskResult:
    number: str  # "1.1"
    title: str
    status: str  # done / failed / skipped
    note: str = ""  # 备注 / 文件路径


@dataclass
class CoderResult:
    path: Path
    status: str  # ok / failed / blocked
    status_reason: str = ""  # failed/blocked 时的原因
    subtasks: list[CoderSubtaskResult] = field(default_factory=list)
    key_changes: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    raw: str = ""


# -------------------------------------------------------------------------
# 数据结构 — reviewer
# -------------------------------------------------------------------------

@dataclass
class ReviewerFinding:
    """reviewer 一条 finding。"""
    severity: str  # p0 / p1 / p2 / advisory
    text: str  # 完整一条原文
    evidence_tags: list[str] = field(default_factory=list)  # 形如 ["req:1.2", "security"]
    file_hint: Optional[str] = None  # 提取的文件路径（best effort，仅展示用）


@dataclass
class ReviewerReview:
    path: Path
    verdict: str  # needs-changes / approved-with-comments / approved
    p0_items: list[ReviewerFinding] = field(default_factory=list)  # 仅"带证据"的 P0
    advisory_items: list[ReviewerFinding] = field(default_factory=list)  # 原 P0 但无证据，降级
    p1_items: list[ReviewerFinding] = field(default_factory=list)
    p2_items: list[ReviewerFinding] = field(default_factory=list)
    summary: str = ""
    status: str = "ok"  # 末行 STATUS
    raw: str = ""


# -------------------------------------------------------------------------
# 数据结构 — validator
# -------------------------------------------------------------------------

@dataclass
class ValidatorSubtaskResult:
    number: str
    title: str
    status: str  # pass / fail
    note: str = ""


@dataclass
class ValidatorFixTarget:
    """validator fail 时按文件分组的修复指引。"""
    file_path: str
    title: str
    location: str = ""
    problem: str = ""
    suggestion: str = ""
    requirements: list[str] = field(default_factory=list)


@dataclass
class ValidatorValidation:
    path: Path
    verdict: str  # pass / fail
    reproduce_cmd: str = ""
    subtask_results: list[ValidatorSubtaskResult] = field(default_factory=list)
    failure_excerpt: str = ""
    fix_targets: list[ValidatorFixTarget] = field(default_factory=list)
    status: str = "ok"
    raw: str = ""

    def fail_signature(self) -> str:
        """fail 签名：从 failure_excerpt 提取测试名 + assertion 文本，做哈希。"""
        if self.verdict != "fail":
            return ""
        text = self.failure_excerpt or ""
        # 试图抓 "FAILED <test>" 行 + 第一条 AssertionError/Error 行
        m = re.search(r"FAILED\s+([^\s]+)", text)
        test_name = m.group(1) if m else ""
        m2 = re.search(r"(AssertionError|Error)[^\n]*", text)
        assertion = m2.group(0) if m2 else text.strip()[:200]
        sig_src = f"{test_name}|{assertion}".strip()
        return hashlib.sha256(sig_src.encode("utf-8")).hexdigest()[:16]


# -------------------------------------------------------------------------
# 工具函数
# -------------------------------------------------------------------------

_STATUS_RE = re.compile(r"^\s*STATUS\s*:\s*([A-Za-z]+)(?:\s*:\s*(.*))?$", re.IGNORECASE)


def _read_text(path: Path) -> str:
    if not path.exists():
        raise ParseError(f"产物文件不存在：{path}")
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        raise ParseError(f"读取产物文件失败：{path}：{e}")


def _split_sections(text: str) -> dict[str, str]:
    """按 `## <name>` 切段，返回 {section_name_lower: body}。第一个 `## ` 之前的部分以空 key 存。"""
    sections: dict[str, str] = {"": ""}
    cur_name = ""
    cur_buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            sections[cur_name] = "\n".join(cur_buf).rstrip()
            cur_name = m.group(1).strip().lower()
            cur_buf = []
        else:
            cur_buf.append(line)
    sections[cur_name] = "\n".join(cur_buf).rstrip()
    return sections


def _extract_status(text: str) -> tuple[str, str]:
    """提取末尾 STATUS 行。返回 (status, reason)。未找到 → ("", "")。"""
    for line in reversed(text.splitlines()):
        s = line.strip()
        if not s:
            continue
        m = _STATUS_RE.match(s)
        if m:
            status = m.group(1).lower()
            # 兼容形如 "STATUS: failed: ImportError" → status=failed, reason=ImportError
            reason = (m.group(2) or "").strip()
            # 若 status 末尾带冒号（罕见），剥掉
            status = status.rstrip(":")
            return status, reason
        # 任何非空非 STATUS 行都跳出（STATUS 必须是末行非空）
        break
    return "", ""


# -------------------------------------------------------------------------
# coder result.md
# -------------------------------------------------------------------------

_CODER_SUBTASK_RE = re.compile(
    r"^\s*-\s+(?P<num>\d+(?:\.\d+)+)\s+(?P<title>.+?)\s*[:：]\s*(?P<status>done|failed|skipped)(?:\s*[—\-]\s*(?P<note>.*))?$",
    re.IGNORECASE,
)


def parse_coder_result(path: Path) -> CoderResult:
    raw = _read_text(path)
    sections = _split_sections(raw)
    status, reason = _extract_status(raw)
    if status not in ("ok", "failed", "blocked"):
        raise ParseError(
            f"coder result 缺末行 STATUS 或 status 非法（应为 ok/failed/blocked）：{path}"
        )

    res = CoderResult(path=path, status=status, status_reason=reason, raw=raw)

    body = sections.get("子任务状态", "")
    for line in body.splitlines():
        m = _CODER_SUBTASK_RE.match(line)
        if m:
            res.subtasks.append(CoderSubtaskResult(
                number=m.group("num"),
                title=m.group("title").strip(),
                status=m.group("status").lower(),
                note=(m.group("note") or "").strip(),
            ))

    kc = sections.get("关键变更", "")
    for line in kc.splitlines():
        s = line.strip()
        if s.startswith("-"):
            res.key_changes.append(s.lstrip("-").strip())

    hints = sections.get("给下游 reviewer 的提示", "") or sections.get("给下游 reviewer 的提示（可选）", "")
    for line in hints.splitlines():
        s = line.strip()
        if s.startswith("-"):
            res.hints.append(s.lstrip("-").strip())

    return res


# -------------------------------------------------------------------------
# reviewer review.md
# -------------------------------------------------------------------------

_EVIDENCE_RE = re.compile(r"\[(req:[^\]]+|security|contract)\]")
_FILE_HINT_RE = re.compile(r"([A-Za-z0-9_./\-]+\.[A-Za-z0-9]+)(?::\d+)?")
_VERDICT_VALUES = {"needs-changes", "approved-with-comments", "approved"}


def _parse_findings(body: str, default_severity: str) -> list[ReviewerFinding]:
    out: list[ReviewerFinding] = []
    for line in body.splitlines():
        s = line.strip()
        if not s or not s.startswith("-"):
            continue
        s2 = s.lstrip("-").strip()
        if s2.lower() in ("(none)", "none", "无"):
            continue
        tags = [m.group(1) for m in _EVIDENCE_RE.finditer(s2)]
        file_hint_m = _FILE_HINT_RE.search(s2)
        file_hint = file_hint_m.group(1) if file_hint_m else None
        out.append(ReviewerFinding(
            severity=default_severity,
            text=s2,
            evidence_tags=tags,
            file_hint=file_hint,
        ))
    return out


def parse_reviewer_review(path: Path) -> ReviewerReview:
    raw = _read_text(path)
    sections = _split_sections(raw)
    verdict_body = sections.get("结论", "").strip().lower()
    verdict_token = ""
    for tok in verdict_body.split():
        if tok in _VERDICT_VALUES:
            verdict_token = tok
            break
    if verdict_token not in _VERDICT_VALUES:
        raise ParseError(
            f"reviewer review 缺合法 '## 结论' 章节（应为 "
            f"needs-changes/approved-with-comments/approved）：{path}"
        )

    # 抓 P0 / P1 / P2 三段（P0 章节标题可能含证据要求附注）
    p0_body = ""
    p1_body = ""
    p2_body = ""
    for k, v in sections.items():
        if k.startswith("p0"):
            p0_body = v
        elif k.startswith("p1"):
            p1_body = v
        elif k.startswith("p2"):
            p2_body = v
    p0_raw = _parse_findings(p0_body, "p0")
    # 按证据标签拆分
    p0_items: list[ReviewerFinding] = []
    advisory: list[ReviewerFinding] = []
    for f in p0_raw:
        if f.evidence_tags:
            p0_items.append(f)
        else:
            f.severity = "advisory"
            advisory.append(f)
    p1_items = _parse_findings(p1_body, "p1")
    p2_items = _parse_findings(p2_body, "p2")
    summary = ""
    for k in ("给使用者的提示", "给使用者的提示（可选）"):
        if k in sections and sections[k]:
            summary = sections[k].strip()
            break

    status, _ = _extract_status(raw)
    if status != "ok":
        raise ParseError(
            f"reviewer review 末行 STATUS 必须是 ok（reviewer 是 advisory）：{path}"
        )

    return ReviewerReview(
        path=path,
        verdict=verdict_token,
        p0_items=p0_items,
        advisory_items=advisory,
        p1_items=p1_items,
        p2_items=p2_items,
        summary=summary,
        status=status,
        raw=raw,
    )


# -------------------------------------------------------------------------
# validator validation.md
# -------------------------------------------------------------------------

_VALIDATOR_SUBTASK_RE = re.compile(
    r"^\s*-\s+\[(?P<box>[ xX])\]\s+(?P<num>\d+(?:\.\d+)+)\s+(?P<title>.+?)\s*[:：]\s*(?P<status>pass|fail)(?:\s*[—\-]\s*(?P<note>.*))?$",
    re.IGNORECASE,
)
_VALIDATOR_VERDICT_VALUES = {"pass", "fail"}


def _parse_fix_targets(body: str) -> list[ValidatorFixTarget]:
    targets: list[ValidatorFixTarget] = []
    cur: Optional[ValidatorFixTarget] = None
    for line in body.splitlines():
        ms = re.match(r"^###\s+(?:修复\s*\d+\s*[—\-]\s*)?(.+?)\s*$", line)
        if ms:
            if cur is not None:
                targets.append(cur)
            cur = ValidatorFixTarget(file_path="", title=ms.group(1).strip())
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("- 文件:") or s.startswith("- 文件："):
            cur.file_path = s.split(":", 1)[1].strip() if ":" in s else s.split("：", 1)[1].strip()
        elif s.startswith("- 位置:") or s.startswith("- 位置："):
            cur.location = s.split(":", 1)[1].strip() if ":" in s else s.split("：", 1)[1].strip()
        elif s.startswith("- 问题:") or s.startswith("- 问题："):
            cur.problem = s.split(":", 1)[1].strip() if ":" in s else s.split("：", 1)[1].strip()
        elif s.startswith("- 建议:") or s.startswith("- 建议："):
            cur.suggestion = s.split(":", 1)[1].strip() if ":" in s else s.split("：", 1)[1].strip()
        elif "需求" in s and "_" in s:
            reqs = re.findall(r"_需求[：:]\s*([^_]+)_", s)
            for r in reqs:
                cur.requirements.extend([x.strip() for x in re.split(r"[,，]", r) if x.strip()])
    if cur is not None:
        targets.append(cur)
    # 校验：fix_target 必须有 file_path
    return [t for t in targets if t.file_path]


def parse_validator_validation(path: Path) -> ValidatorValidation:
    raw = _read_text(path)
    sections = _split_sections(raw)
    verdict_body = sections.get("判定", "").strip().lower()
    verdict_token = ""
    for tok in verdict_body.split():
        if tok in _VALIDATOR_VERDICT_VALUES:
            verdict_token = tok
            break
    if verdict_token not in _VALIDATOR_VERDICT_VALUES:
        raise ParseError(
            f"validator validation 缺合法 '## 判定' 章节（应为 pass / fail）：{path}"
        )

    val = ValidatorValidation(path=path, verdict=verdict_token, raw=raw)
    cmd_body = sections.get("复现命令", "")
    # 抓 fenced block 内容
    m = re.search(r"```(?:bash)?\s*(.*?)```", cmd_body, re.DOTALL)
    if m:
        val.reproduce_cmd = m.group(1).strip()
    else:
        val.reproduce_cmd = cmd_body.strip()

    st_body = sections.get("按子任务的验证结果", "")
    for line in st_body.splitlines():
        m = _VALIDATOR_SUBTASK_RE.match(line)
        if m:
            val.subtask_results.append(ValidatorSubtaskResult(
                number=m.group("num"),
                title=m.group("title").strip(),
                status=m.group("status").lower(),
                note=(m.group("note") or "").strip(),
            ))

    fail_body = sections.get("失败现场（fail 时必填）", "") or sections.get("失败现场", "")
    m = re.search(r"```\s*(.*?)```", fail_body, re.DOTALL)
    if m:
        val.failure_excerpt = m.group(1).strip()
    else:
        val.failure_excerpt = fail_body.strip()

    fix_body = ""
    for key in sections:
        if key.startswith("给 coder 的修复指引") or key.startswith("给 coder 的修复指引（fail 时必填"):
            fix_body = sections[key]
            break
    val.fix_targets = _parse_fix_targets(fix_body)

    # schema 强校验：fail 必须有 failure_excerpt + 至少 1 个 fix_target
    if val.verdict == "fail":
        if not val.failure_excerpt:
            raise ParseError(f"validator fail 但缺 '失败现场'：{path}")
        if not val.fix_targets:
            raise ParseError(f"validator fail 但缺 '给 coder 的修复指引'（按文件分组）：{path}")

    status, _ = _extract_status(raw)
    if status != "ok":
        raise ParseError(
            f"validator validation 末行 STATUS 必须是 ok（pass/fail 是 verdict，不是 status）：{path}"
        )
    val.status = status
    return val


# -------------------------------------------------------------------------
# 模块自测
# -------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import sys
    if len(sys.argv) < 3:
        print("usage: task_swarm_outbox.py <coder|reviewer|validator> <file.md>")
        raise SystemExit(2)
    kind, fp = sys.argv[1], Path(sys.argv[2])
    if kind == "coder":
        r = parse_coder_result(fp)
    elif kind == "reviewer":
        r = parse_reviewer_review(fp)
    elif kind == "validator":
        r = parse_validator_validation(fp)
    else:
        raise SystemExit(2)
    print(r)
