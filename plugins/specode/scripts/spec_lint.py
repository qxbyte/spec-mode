#!/usr/bin/env python3
"""spec_lint.py — 对当前 spec 目录做轻量 lint。

仅产出 WARNING；所有 lint 一律 exit 0（不阻断模型流程）。

规则：
  1. tasks.md 中的 `_需求：x.y_` 标签必须在 requirements.md / bugfix.md
     找到对应 "需求 x" 或 "x.y" 章节标记；找不到 → WARNING
  2. implementation-log.md 中每个 `## ` 条目正文 < 30 字符或缺
     文件引用 (`.py` / `.md` 等) → WARNING（"空 log 等于没改过"）
  3. requirements.md 中的 EARS SHALL 行缺动词或缺 trigger
     （形如 WHEN / IF / WHILE / WHERE 关键字开头）→ WARNING

接入：acceptance phase 进入前由主代理调一次，把 WARNING 列给用户参考
（详见 SKILL.md §Phase Order 中 acceptance 部分）。

用法：
  spec_lint.py --spec <spec-dir>        lint 该 spec 目录下 5 份文档

stdlib-only。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional


# -------------------------------------------------------------------------

REQ_TAG_RE = re.compile(r"_需求[：:]\s*([0-9]+(?:\.[0-9]+)?)_")
FILE_REF_RE = re.compile(r"[A-Za-z0-9_./-]+\.(py|md|js|ts|tsx|jsx|go|rs|java|kt|rb|c|h|cpp|sh|yaml|yml|json)")
EARS_HEADS = ("WHEN", "IF", "WHILE", "WHERE", "WHENEVER")
SHALL_LINE_RE = re.compile(r"\bSHALL\b", re.IGNORECASE)


def _warn(buf: list[str], rule: str, msg: str) -> None:
    buf.append(f"[WARN][{rule}] {msg}")


def _read(p: Path) -> Optional[str]:
    try:
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    return None


def rule_task_traceability(spec_dir: Path, warnings: list[str]) -> None:
    tasks = _read(spec_dir / "tasks.md")
    if not tasks:
        return
    haystack_parts = []
    for fn in ("requirements.md", "bugfix.md"):
        s = _read(spec_dir / fn)
        if s:
            haystack_parts.append(s)
    haystack = "\n".join(haystack_parts)
    if not haystack:
        # tasks.md 含标签但无 req/bugfix → 全报
        for tag in set(REQ_TAG_RE.findall(tasks)):
            _warn(warnings, "trace",
                  f"tasks.md 引用 _需求：{tag}_ 但 requirements.md / bugfix.md 不存在或为空。")
        return
    for tag in sorted(set(REQ_TAG_RE.findall(tasks))):
        # 容许匹配 "需求 1" / "需求 1.2" / "1.2"
        if not re.search(rf"需求\s*{re.escape(tag)}\b", haystack) and tag not in haystack:
            _warn(warnings, "trace",
                  f"tasks.md 的 _需求：{tag}_ 在 requirements.md / bugfix.md 中找不到对应章节。")


def rule_log_entries(spec_dir: Path, warnings: list[str]) -> None:
    log = _read(spec_dir / "implementation-log.md")
    if not log:
        return
    # 拆 ## 开头的条目
    parts = re.split(r"(?m)^##\s+", log)
    # parts[0] 是文件头；条目从 parts[1:] 起
    for entry in parts[1:]:
        # 取第一行作为 title，正文是其余
        head, _, body = entry.partition("\n")
        body_stripped = body.strip()
        title = head.strip()
        if not body_stripped:
            _warn(warnings, "log",
                  f"implementation-log.md 条目「{title[:30]}」正文为空。")
            continue
        if len(body_stripped) < 30:
            _warn(warnings, "log",
                  f"implementation-log.md 条目「{title[:30]}」正文过短（< 30 字符）；信息量不足。")
        if not FILE_REF_RE.search(body_stripped):
            _warn(warnings, "log",
                  f"implementation-log.md 条目「{title[:30]}」未引用任何源码 / 文档文件路径。")


def rule_ears_shall(spec_dir: Path, warnings: list[str]) -> None:
    req = _read(spec_dir / "requirements.md")
    if not req:
        return
    for idx, line in enumerate(req.splitlines(), start=1):
        if not SHALL_LINE_RE.search(line):
            continue
        # 简单 EARS 检查：行内或紧邻上文应含 EARS 关键字
        upper = line.upper()
        has_trigger = any(k in upper for k in EARS_HEADS)
        if not has_trigger:
            # 看前后两行
            # 注意：splitlines 不保留尾换行；这里不报上下文，仅提示
            _warn(warnings, "ears",
                  f"requirements.md 第 {idx} 行包含 SHALL 但未检测到 EARS trigger（WHEN/IF/WHILE/WHERE）。")
            continue
        # 检查 SHALL 之后是否有动词（粗略判定：SHALL 后至少有非空白且非 thE/A/AN 的词）
        m = re.search(r"SHALL\s+([A-Za-z一-鿿]+)", line, re.IGNORECASE)
        if not m or m.group(1).lower() in ("the", "a", "an"):
            _warn(warnings, "ears",
                  f"requirements.md 第 {idx} 行 SHALL 后缺动词。")


# -------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="spec_lint.py", description="lint a specode spec directory")
    parser.add_argument("--spec", required=True, help="spec 目录绝对路径")
    args = parser.parse_args(argv)

    spec_dir = Path(args.spec).expanduser().resolve()
    if not spec_dir.is_dir():
        sys.stderr.write(f"spec 目录不存在：{spec_dir}\n")
        return 0  # lint 不阻断；返回 0

    warnings: list[str] = []
    rule_task_traceability(spec_dir, warnings)
    rule_log_entries(spec_dir, warnings)
    rule_ears_shall(spec_dir, warnings)

    if not warnings:
        sys.stdout.write("spec_lint: 0 warnings.\n")
        return 0
    sys.stdout.write(f"spec_lint: {len(warnings)} warning(s).\n")
    for w in warnings:
        sys.stdout.write(w + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
