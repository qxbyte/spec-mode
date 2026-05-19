#!/usr/bin/env python3
"""spec_init.py — `/specode:spec <需求>` 入口。

参数：
  --name <slug>                  spec 目录名（建议短横线 slug）
  --requirement-name "<显示名>"  人类可读名称（写入 .config.json）
  --source-text "<原始需求文本>" 写入 requirements.md / bugfix.md 的 summary
  --session <session_id>         会话 id（必填）
  [--root <override>]            覆盖三层 doc_root 解析
  [--detect-vault]               仅打印 vault 检测结果后退出

行为：
  1. resolve_doc_root（含 --root / SPECODE_ROOT / config / auto）
  2. 三层全 miss → 输出引导 + exit 3
  3. 在 doc_root 下创建 specs/<slug>/{requirements.md,bugfix.md,design.md,tasks.md,
                                      implementation-log.md,.config.json}
     （tasks.md 末尾自带 `## 测试要点` 章节，由 agent 跟随 requirements/bugfix 同步更新）
  4. 更新 <doc_root>/.active-specode.json
  5. 强制写 ~/.specode/sessions/<session_id>.json （atomic tempfile + os.replace + fsync）
  6. 任一失败 → 回滚已写文件 + exit 1
  7. 成功输出 JSON：{"spec_dir","specId","session_id","phase"}

stdlib-only。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

# 复用 spec_vault.py 的解析与原子写
THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

# 0.10.0+ 日志（defensive import；失败时降级为 no-op）
try:
    from spec_log import write_event as _log_event  # type: ignore
except Exception:
    def _log_event(event: str, payload: Optional[dict] = None,
                   session_id: Optional[str] = None) -> None:
        return None

from spec_vault import resolve_doc_root, _atomic_write_json  # type: ignore  # noqa: E402


# -------------------------------------------------------------------------
# 模板
# -------------------------------------------------------------------------

TEMPLATE_DIR = THIS_DIR.parent / "assets" / "templates"

# fallback 骨架（模板缺失时使用）
FALLBACK_TEMPLATES: dict[str, str] = {
    "requirements.md": """# 需求文档

Spec Type: Feature
Workflow: requirements-first
Status: Requirements Draft

## 简介

{{summary}}

## 需求

### 需求 1：核心能力

#### 验收标准

1. WHEN 用户触发该能力，THE System SHALL 按需求描述执行预期行为。
""",
    "bugfix.md": """# Bugfix 文档

Spec Type: Bugfix
Workflow: bugfix
Status: Bug Analysis Draft

## 问题摘要

{{summary}}

## 当前行为

1. WHEN 缺陷触发条件满足，THEN THE System 出现当前错误行为。

## 期望行为

1. WHEN 缺陷触发条件满足，THE System SHALL 执行正确行为。
""",
    "design.md": """# 设计文档：{{name}}（{{slug}}）

Status: Design Draft

## 概述

{{summary}}

## 架构

待补充。

## 组件与接口

待补充。
""",
    "tasks.md": """# 实现计划：{{name}}（{{slug}}）

Status: Tasks Draft

## 阶段 1: 待规划阶段标题

- [ ] 1.1 待规划任务描述 @writes:src/path/to/file.py _需求：1.1_

## 测试要点

供测试人员快速了解需要验证的场景。spec-writer 在 tasks phase 按 SHALL 顺手补几行作为参考；非验收硬条件。

- _agent 待填充_：触发场景 → 预期结果（需求 X.Y）

## 验收

- [ ] 所有 required 任务完成。
""",
    "implementation-log.md": """# 实现记录：{{name}}（{{slug}}）

> 记录实现期间的设计偏离、关键决策、阻塞与解决方案。空白等于没改过——请勿留空。

## {{created_at}} — 初始化

- spec 已初始化，等待 intake / requirements 推进。
""",
}


def _render(text: str, ctx: dict[str, str]) -> str:
    # 简单 {{key}} 替换；缺失保留原文（不报错）
    def repl(m: "re.Match[str]") -> str:
        key = m.group(1).strip()
        return ctx.get(key, m.group(0))
    return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", repl, text)


def _load_template(name: str) -> str:
    p = TEMPLATE_DIR / name
    if p.exists():
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            pass
    return FALLBACK_TEMPLATES.get(name, f"# {name}\n\n待补充。\n")


# -------------------------------------------------------------------------
# 工具
# -------------------------------------------------------------------------

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sessions_dir() -> Path:
    return Path.home() / ".specode" / "sessions"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# -------------------------------------------------------------------------
# 主流程
# -------------------------------------------------------------------------

def _print_root_missing_hint() -> None:
    msg = (
        "specode: 未能解析出可用的文档根目录（doc_root）。\n"
        "已尝试：\n"
        "  1) --root 参数 / 环境变量 SPECODE_ROOT\n"
        "  2) ~/.config/specode/config.json 的 obsidianRoot\n"
        "  3) 自动检测 Obsidian vault\n\n"
        "请任选其一：\n"
        "  - 运行 `spec_vault.py set --vault <绝对路径>` 持久化\n"
        "  - 或 `export SPECODE_ROOT=<绝对路径>` 临时指定\n"
        "  - 或在 Obsidian 中打开任意 vault 后重试\n"
    )
    sys.stderr.write(msg)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="spec_init.py", description="initialise a new specode spec")
    parser.add_argument("--name", required=True, help="spec slug（短横线小写）")
    parser.add_argument("--requirement-name", required=True, help="人类可读名称")
    parser.add_argument("--source-text", required=True, help="原始需求文本（写入 summary）")
    parser.add_argument("--session", required=True, help="会话 id（宿主注入的 session_id）")
    parser.add_argument("--root", help="覆盖 doc_root（绝对路径）")
    parser.add_argument("--detect-vault", action="store_true", help="仅打印 vault 检测结果后退出")
    args = parser.parse_args(argv)

    if args.detect_vault:
        # 透传给 spec_vault.detect
        from spec_vault import cmd_detect  # type: ignore
        ns = argparse.Namespace()
        return cmd_detect(ns)

    slug = args.name.strip()
    if not SLUG_RE.match(slug):
        sys.stderr.write(
            f"非法 slug：{slug!r}（仅允许小写字母、数字、短横线，开头必须是字母/数字，长度 ≤ 80）。\n"
        )
        return 3

    # 1. 解析 doc_root
    root, source = resolve_doc_root(override=args.root)
    if root is None:
        _print_root_missing_hint()
        return 3
    if not root.exists():
        sys.stderr.write(
            f"doc_root 不存在（来源={source}）：{root}\n"
            "请创建该目录后重试，或换一个 --root 参数。\n"
        )
        return 3

    specs_root = root / "specs"
    spec_dir = specs_root / slug
    if spec_dir.exists():
        sys.stderr.write(
            f"spec 目录已存在：{spec_dir}\n"
            "请换一个 --name slug，或使用 /specode:continue 接管已有 spec。\n"
        )
        return 3

    spec_id = str(uuid.uuid4())
    created_at = _now_iso()
    ctx = {
        "summary": args.source_text,
        "name": args.requirement_name,
        "slug": slug,
        "spec_type": "Feature",
        "workflow": "requirements-first",
        "created_at": created_at,
        "spec_id": spec_id,
    }

    # 文档内容
    doc_files = {
        "requirements.md": _render(_load_template("requirements.md"), ctx),
        "bugfix.md": _render(_load_template("bugfix.md"), ctx),
        "design.md": _render(_load_template("design.md"), ctx),
        "tasks.md": _render(_load_template("tasks.md"), ctx),
        "implementation-log.md": _render(_load_template("implementation-log.md"), ctx),
    }

    spec_config = {
        "specId": spec_id,
        "slug": slug,
        "name": args.requirement_name,
        "createdAt": created_at,
        "phase": "intake",
        "workflow": None,            # workflow 选择器之后写入
        "pending_selector": "workflow-choice",
        "lock": {
            "holder": args.session,
            "acquired_at": created_at,
            "last_heartbeat_at": created_at,
        },
        "doc_root": str(root),
        "source": source,
        "source_text": args.source_text,
    }

    active_pointer_path = root / ".active-specode.json"
    sessions_path = _sessions_dir() / f"{args.session}.json"

    # 跟踪已创建以便回滚
    created_paths: list[Path] = []
    # 备份 active-pointer 用于回滚
    prior_active_pointer: Optional[str] = None
    if active_pointer_path.exists():
        try:
            prior_active_pointer = active_pointer_path.read_text(encoding="utf-8")
        except Exception:
            prior_active_pointer = None
    prior_session_blob: Optional[str] = None
    if sessions_path.exists():
        try:
            prior_session_blob = sessions_path.read_text(encoding="utf-8")
        except Exception:
            prior_session_blob = None

    def _rollback() -> None:
        # 删除新建的 spec_dir（整个目录是新建的）
        try:
            if spec_dir.exists():
                shutil.rmtree(spec_dir)
        except Exception:
            pass
        # 还原 active-pointer
        try:
            if prior_active_pointer is None:
                if active_pointer_path.exists():
                    active_pointer_path.unlink()
            else:
                _atomic_write_text(active_pointer_path, prior_active_pointer)
        except Exception:
            pass
        # 还原 sessions
        try:
            if prior_session_blob is None:
                if sessions_path.exists():
                    sessions_path.unlink()
            else:
                _atomic_write_text(sessions_path, prior_session_blob)
        except Exception:
            pass

    try:
        # 3. 创建 spec_dir + 6 份文档 + .config.json
        spec_dir.mkdir(parents=True, exist_ok=False)
        created_paths.append(spec_dir)
        for fname, content in doc_files.items():
            fp = spec_dir / fname
            _atomic_write_text(fp, content)
            created_paths.append(fp)
        _atomic_write_json(spec_dir / ".config.json", spec_config)
        created_paths.append(spec_dir / ".config.json")

        # 4. 更新 active-pointer
        active_payload = {
            "active_spec_slug": slug,
            "active_spec_dir": str(spec_dir),
            "specId": spec_id,
            "updatedAt": created_at,
            "session_id": args.session,
        }
        _atomic_write_json(active_pointer_path, active_payload)

        # 5. 强制写 sessions/<id>.json
        session_payload = {
            "session_id": args.session,
            "started_at": created_at,
            "last_activity_at": created_at,
            "ended_at": None,
            "mode": "active",
            "active_spec_slug": slug,
            "active_spec_dir": str(spec_dir),
            "spec_id": spec_id,
            "phase": "intake",
            "lock_state": "ok",
            "task_swarm_run_id": None,
            "pending_selector": "workflow-choice",
        }
        _atomic_write_json(sessions_path, session_payload)

    except Exception as exc:
        _rollback()
        sys.stderr.write(f"spec_init 失败，已回滚：{exc}\n")
        return 1

    # 7. 输出
    out = {
        "spec_dir": str(spec_dir),
        "specId": spec_id,
        "session_id": args.session,
        "phase": "intake",
        "doc_root": str(root),
        "doc_root_source": source,
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    return 0


def _log_wrap_main(argv: Optional[list[str]] = None) -> int:
    """0.10.0+ 包一层捕捉 cli_call / cli_exit 事件。"""
    import contextlib as _cl
    argv_list = list(sys.argv[1:]) if argv is None else list(argv)
    sid = None
    for i, a in enumerate(argv_list):
        if a == "--session" and i + 1 < len(argv_list):
            sid = argv_list[i + 1]
            break
    with _cl.suppress(Exception):
        _log_event("cli_call", {"script": "spec_init.py", "argv_len": len(argv_list)}, session_id=sid)
    rc = main(argv)
    with _cl.suppress(Exception):
        _log_event("cli_exit", {"script": "spec_init.py", "exit_code": rc}, session_id=sid)
    return rc


if __name__ == "__main__":
    try:
        sys.exit(_log_wrap_main())
    except KeyboardInterrupt:
        sys.exit(130)
