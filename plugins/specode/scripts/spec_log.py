#!/usr/bin/env python3
"""spec_log.py — specode 会话日志收集（0.10.0+）。

收集 spec 模式期间的事件流到 ~/.specode/logs/<session_id>.jsonl，
用于排查 bug 时回溯主代理调用 / hook 注入 / CLI 调用的现场。

设计：
- 单一事实源：~/.specode/logs/<session_id>.jsonl（每行一个 JSON event）
- 双开关：SPECODE_LOG=off env 临时关 / ~/.config/specode/config.json.logging=false 永久关
- 默认 redact：password / api_key / token / secret / authorization 等键名匹配 → 占位
- 默认截断：字符串字段超过 500 字符 → 截断 + 标记
- 不 rotation：手动清 rm -rf ~/.specode/logs/；status 子命令报当前占用

子命令：
- write-event   （内部）由其他脚本 / hook 调用写一条 event
- replay        按时序输出指定 session 的可读 events
- status        输出 ~/.specode/logs/ 占用与文件数
- enable        临时打开（清除 SPECODE_LOG env 提示）
- disable       临时关闭（提示设置 SPECODE_LOG=off）

stdlib-only。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional


DEFAULT_REDACT_KEYS = (
    "password", "passwd", "pwd",
    "api_key", "apikey", "api-key",
    "token", "access_token", "refresh_token",
    "secret", "client_secret",
    "authorization", "auth",
    "cookie", "session_cookie",
    "private_key", "ssh_key",
)
DEFAULT_TRUNCATE_LEN = 500
REDACT_PLACEHOLDER = "<redacted>"
TRUNCATE_SUFFIX = "...<truncated>"


# -------------------------------------------------------------------------
# 配置 + 开关
# -------------------------------------------------------------------------

def _logs_dir() -> Path:
    return Path.home() / ".specode" / "logs"


def _config_path() -> Path:
    return Path.home() / ".config" / "specode" / "config.json"


def _read_config() -> dict:
    p = _config_path()
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def is_logging_enabled() -> bool:
    """env > config > default(True)."""
    env = os.environ.get("SPECODE_LOG", "").lower()
    if env in ("off", "false", "0", "no"):
        return False
    if env in ("on", "true", "1", "yes"):
        return True
    cfg = _read_config()
    val = cfg.get("logging")
    if val is False:
        return False
    return True  # default


def _redact_key_set() -> set[str]:
    cfg = _read_config()
    extra = cfg.get("redact_keys") or []
    keys = set(k.lower() for k in DEFAULT_REDACT_KEYS)
    if isinstance(extra, list):
        keys.update(k.lower() for k in extra if isinstance(k, str))
    return keys


# -------------------------------------------------------------------------
# Redact + 截断
# -------------------------------------------------------------------------

def _truncate(s: str, limit: int = DEFAULT_TRUNCATE_LEN) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + TRUNCATE_SUFFIX


def _sanitize(value: Any, redact_keys: set[str], depth: int = 0) -> Any:
    """递归处理 dict / list / str；key 命中 redact 则替换占位，str 截断。"""
    if depth > 8:
        return "<deep_truncated>"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in redact_keys:
                out[k] = REDACT_PLACEHOLDER
            else:
                out[k] = _sanitize(v, redact_keys, depth + 1)
        return out
    if isinstance(value, list):
        return [_sanitize(v, redact_keys, depth + 1) for v in value]
    if isinstance(value, str):
        return _truncate(value)
    return value


# -------------------------------------------------------------------------
# 写 event
# -------------------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_event(event: str, payload: Optional[dict] = None,
                session_id: Optional[str] = None) -> None:
    """对外主入口；其他 .py 调这一个函数即可。

    任何异常都吞并；日志失败绝不阻断业务流程。
    """
    try:
        if not is_logging_enabled():
            return
        sid = session_id or (payload or {}).get("session_id")
        if not sid:
            # 没有 session_id 的事件落到 _orphan.jsonl
            sid = "_orphan"
        log_path = _logs_dir() / f"{sid}.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        redact = _redact_key_set()
        record = {
            "ts": _now_iso(),
            "event": event,
            "payload": _sanitize(payload or {}, redact),
        }
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # 静默吞并 —— logging 永不阻断主流程
        pass


# -------------------------------------------------------------------------
# CLI 子命令
# -------------------------------------------------------------------------

def cmd_write_event(args: argparse.Namespace) -> int:
    """write-event --event <name> [--session <id>] [--payload <json>]。

    payload 从 stdin 或 --payload JSON 字符串。
    """
    payload = {}
    if args.payload:
        try:
            payload = json.loads(args.payload)
        except Exception:
            sys.stderr.write(f"invalid --payload JSON: {args.payload!r}\n")
            return 1
    elif not sys.stdin.isatty():
        try:
            raw = sys.stdin.read().strip()
            if raw:
                payload = json.loads(raw)
        except Exception:
            pass
    write_event(args.event, payload, session_id=args.session)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """replay --session <id> 按时序打印 events。"""
    log_path = _logs_dir() / f"{args.session}.jsonl"
    if not log_path.exists():
        sys.stderr.write(f"no log for session: {args.session}\n")
        sys.stderr.write(f"  expected: {log_path}\n")
        return 3
    total = 0
    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                sys.stdout.write(f"[skip malformed] {line}\n")
                continue
            ts = rec.get("ts", "?")
            ev = rec.get("event", "?")
            payload = rec.get("payload", {})
            payload_str = json.dumps(payload, ensure_ascii=False)
            if len(payload_str) > 200:
                payload_str = payload_str[:200] + "..."
            sys.stdout.write(f"[{ts}] {ev}  {payload_str}\n")
            total += 1
    sys.stderr.write(f"(replayed {total} events from {log_path})\n")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """status 输出 ~/.specode/logs/ 占用 + 文件数。"""
    d = _logs_dir()
    enabled = is_logging_enabled()
    info = {
        "enabled": enabled,
        "switch_source": _switch_source(),
        "logs_dir": str(d),
        "exists": d.exists(),
    }
    if d.exists():
        files = list(d.glob("*.jsonl"))
        total_bytes = sum(p.stat().st_size for p in files if p.is_file())
        info["session_log_files"] = len(files)
        info["total_bytes"] = total_bytes
        info["total_mb"] = round(total_bytes / 1024 / 1024, 2)
        # 占用大时给个提示
        if total_bytes > 100 * 1024 * 1024:
            info["hint"] = "logs 超过 100MB，可手动清理：rm -rf ~/.specode/logs/"
    sys.stdout.write(json.dumps(info, ensure_ascii=False, indent=2) + "\n")
    return 0


def _switch_source() -> str:
    env = os.environ.get("SPECODE_LOG", "").lower()
    if env:
        return f"env:SPECODE_LOG={env}"
    cfg = _read_config()
    if "logging" in cfg:
        return f"config.json.logging={cfg.get('logging')}"
    return "default(on)"


def cmd_enable(args: argparse.Namespace) -> int:
    """提示如何打开（env unset / config 写 true）。"""
    msg = (
        "spec_log 默认开启。当前开关来源：" + _switch_source() + "\n\n"
        "如果当前是关闭状态，按下列之一打开：\n"
        "  1) 临时打开：unset SPECODE_LOG   （或 export SPECODE_LOG=on）\n"
        "  2) 永久打开：编辑 ~/.config/specode/config.json，把 logging 设为 true 或删除该字段\n"
    )
    sys.stdout.write(msg)
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    """提示如何关闭。"""
    msg = (
        "关闭 spec_log 的两种方式：\n\n"
        "  1) 临时关闭（仅当前 shell）：export SPECODE_LOG=off\n"
        "  2) 永久关闭：编辑 ~/.config/specode/config.json，加 \"logging\": false\n\n"
        "当前开关来源：" + _switch_source() + "\n"
    )
    sys.stdout.write(msg)
    return 0


# -------------------------------------------------------------------------
# main
# -------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spec_log.py",
        description="specode session log collection (0.10.0+)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_w = sub.add_parser("write-event", help="write a single event (internal)")
    p_w.add_argument("--event", required=True, help="event name")
    p_w.add_argument("--session", help="session id (or via payload)")
    p_w.add_argument("--payload", help="payload JSON string (or via stdin)")
    p_w.set_defaults(func=cmd_write_event)

    p_r = sub.add_parser("replay", help="replay a session's events in order")
    p_r.add_argument("--session", required=True, help="session id to replay")
    p_r.set_defaults(func=cmd_replay)

    p_s = sub.add_parser("status", help="show logs/ size + enable state")
    p_s.set_defaults(func=cmd_status)

    p_e = sub.add_parser("enable", help="show how to enable logging")
    p_e.set_defaults(func=cmd_enable)

    p_d = sub.add_parser("disable", help="show how to disable logging")
    p_d.set_defaults(func=cmd_disable)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
