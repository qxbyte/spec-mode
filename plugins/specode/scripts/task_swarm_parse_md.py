#!/usr/bin/env python3
"""task_swarm_parse_md.py — 解析 tasks.md 为 stage 列表，并按文件冲突切 group（references/task-swarm.md §2）。

输入：tasks.md 路径
输出：
    parse_tasks_md(path) -> list[Stage]
    group_by_file_conflict(stages, max_parallel=N) -> list[list[Stage]]

stdlib-only。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# -------------------------------------------------------------------------
# 数据结构
# -------------------------------------------------------------------------

@dataclass
class StageItem:
    """tasks.md 中一个 - [ ] N.M ... 行（叶子子任务）。"""
    number: str  # 例如 "1.1" / "2.3"
    title: str  # 不含 @writes/@reads/_需求_ 等标签的纯标题
    writes: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)  # 形如 "1.1" / "1.2"
    depends_on: list[str] = field(default_factory=list)  # 形如 "2.1"
    raw_line: str = ""  # 原始行，writeback 时用作精确定位
    checkbox: str = " "  # 当前 checkbox 字符（空格 / x）
    line_no: int = 0  # 在 tasks.md 中的 1-based 行号


@dataclass
class Stage:
    """tasks.md 中一个 ## 阶段 N: ... 段，包含若干 StageItem。"""
    number: int  # 1, 2, 3, ...
    title: str  # 标题（不含 "阶段 N: " 前缀）
    items: list[StageItem] = field(default_factory=list)
    header_line_no: int = 0  # 标题所在行号
    end_line_no: int = 0  # 段最后一行（下个阶段标题前 / 文末）

    @property
    def writes(self) -> list[str]:
        """聚合该 stage 全部 item 的 @writes 集合。"""
        out: list[str] = []
        for it in self.items:
            for w in it.writes:
                if w not in out:
                    out.append(w)
        return out

    @property
    def reads(self) -> list[str]:
        """聚合该 stage 全部 item 的 @reads 集合。"""
        out: list[str] = []
        for it in self.items:
            for r in it.reads:
                if r not in out:
                    out.append(r)
        return out

    @property
    def depends_on(self) -> list[int]:
        """聚合该 stage 全部 item 的 @depends-on（取数字段，转为 int stage 号）。"""
        out: list[int] = []
        for it in self.items:
            for d in it.depends_on:
                # 接受 "2" / "2.1" 两种格式；统一取主 stage 号
                head = d.split(".", 1)[0].strip()
                try:
                    n = int(head)
                except ValueError:
                    continue
                if n != self.number and n not in out:
                    out.append(n)
        return out


# -------------------------------------------------------------------------
# 解析
# -------------------------------------------------------------------------

_STAGE_HEADER_RE = re.compile(r"^\s*##\s+阶段\s+(\d+)\s*[:：]\s*(.+?)\s*$")
_ITEM_RE = re.compile(r"^\s*-\s+\[(?P<box>[ xX])\]\s+(?P<num>\d+(?:\.\d+)+)\s+(?P<rest>.*)$")
_WRITES_RE = re.compile(r"@writes\s*[:：]\s*([^\s@_]+)")
_READS_RE = re.compile(r"@reads\s*[:：]\s*([^\s@_]+)")
_DEPENDS_RE = re.compile(r"@depends-on\s*[:：]\s*([^\s@_]+)")
_REQ_RE = re.compile(r"_需求[：:]\s*([^_]+?)_")


def _split_csv(s: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,，]", s) if p.strip()]


def parse_tasks_md(path: Path) -> list[Stage]:
    """解析 tasks.md，返回 Stage 列表。

    解析规则：
        - 阶段标题：`## 阶段 N: <标题>` 或 `## 阶段 N：<标题>`
        - 子任务：`- [ ] N.M <标题> @writes:a.py,b.py @reads:c.py @depends-on:2.1 _需求：1.1,1.2_`
        - tag 之间分隔符可空格；中文/英文冒号都识别
        - 非阶段块行（介绍、备注）忽略
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    stages: list[Stage] = []
    current: Optional[Stage] = None

    for idx, line in enumerate(lines, start=1):
        m = _STAGE_HEADER_RE.match(line)
        if m:
            if current is not None:
                current.end_line_no = idx - 1
                stages.append(current)
            current = Stage(
                number=int(m.group(1)),
                title=m.group(2).strip(),
                header_line_no=idx,
            )
            continue
        if current is None:
            continue
        im = _ITEM_RE.match(line)
        if not im:
            continue
        num = im.group("num")
        rest = im.group("rest")
        # 提取标签
        writes = _split_csv(_WRITES_RE.search(rest).group(1)) if _WRITES_RE.search(rest) else []
        reads = _split_csv(_READS_RE.search(rest).group(1)) if _READS_RE.search(rest) else []
        depends_on = _split_csv(_DEPENDS_RE.search(rest).group(1)) if _DEPENDS_RE.search(rest) else []
        reqs = _split_csv(_REQ_RE.search(rest).group(1)) if _REQ_RE.search(rest) else []
        # 标题：去掉所有标签后剩余
        title = rest
        for r in (_WRITES_RE, _READS_RE, _DEPENDS_RE, _REQ_RE):
            title = r.sub("", title)
        title = re.sub(r"\s+", " ", title).strip()
        current.items.append(StageItem(
            number=num,
            title=title,
            writes=writes,
            reads=reads,
            requirements=reqs,
            depends_on=depends_on,
            raw_line=line,
            checkbox=im.group("box"),
            line_no=idx,
        ))

    if current is not None:
        current.end_line_no = len(lines)
        stages.append(current)

    return stages


# -------------------------------------------------------------------------
# group 切分（references/task-swarm.md §2）
# -------------------------------------------------------------------------

def group_by_file_conflict(
    stages: list[Stage],
    max_parallel: int = 4,
) -> list[list[Stage]]:
    """按 references/task-swarm.md §2 把 stage 切成 group：

    - 同 group 内任意两 stage 的 @writes 集合不相交且无 @depends-on 关系
    - 跨 group 串行：上一 group 全部 pass 后才能开 next group
    - 每 group 上限 = max_parallel
    - stage 顺序保留（按 stage.number 排序）

    依赖关系：若 stage X depends_on Y，X 所在 group 的 index 必须严格大于 Y 所在 group 的 index。
    """
    if not stages:
        return []
    if max_parallel < 1:
        max_parallel = 1

    sorted_stages = sorted(stages, key=lambda s: s.number)
    stage_group: dict[int, int] = {}  # stage.number -> group index
    groups: list[list[Stage]] = []

    for st in sorted_stages:
        placed = False
        # 计算依赖最低可放 group：所有依赖所在 group 的最大 index + 1
        min_idx = 0
        for dep in st.depends_on:
            if dep in stage_group:
                min_idx = max(min_idx, stage_group[dep] + 1)
        # 尝试放入已有 group（>=min_idx）
        for gi in range(min_idx, len(groups)):
            g = groups[gi]
            if len(g) >= max_parallel:
                continue
            # 检查与 group 内每个 stage 的冲突
            ok = True
            for other in g:
                # 文件冲突
                if set(st.writes) & set(other.writes):
                    ok = False
                    break
                # 直接依赖（双向）
                if other.number in st.depends_on or st.number in other.depends_on:
                    ok = False
                    break
            if ok:
                g.append(st)
                stage_group[st.number] = gi
                placed = True
                break
        if not placed:
            groups.append([st])
            stage_group[st.number] = len(groups) - 1
    return groups


# -------------------------------------------------------------------------
# 模块自测
# -------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import sys
    if len(sys.argv) < 2:
        print("usage: task_swarm_parse_md.py <tasks.md>")
        raise SystemExit(2)
    stages = parse_tasks_md(Path(sys.argv[1]))
    for s in stages:
        print(f"## 阶段 {s.number}: {s.title} (lines {s.header_line_no}-{s.end_line_no})")
        for it in s.items:
            print(f"  - [{it.checkbox}] {it.number} {it.title} "
                  f"writes={it.writes} reads={it.reads} deps={it.depends_on} req={it.requirements}")
    groups = group_by_file_conflict(stages)
    print("\nGroups:")
    for gi, g in enumerate(groups):
        print(f"  group {gi}: {[s.number for s in g]}")
