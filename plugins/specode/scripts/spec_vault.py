#!/usr/bin/env python3
"""spec_vault.py — Obsidian vault 检测与 specode 根目录配置（详见 references/obsidian.md）。

子命令：
  detect            扫描三平台 obsidian.json，输出已知 vault 列表 (JSON)
  status            输出当前 doc_root 与来源 (env / config / auto / none)
  set --vault <p>   写 ~/.config/specode/config.json.obsidianRoot
  set --root  <p>   同字段（不强调 vault 概念）

退出码：0 ok / 3 用户引导（含 hard-stop 提示）。

stdlib-only。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple


# -------------------------------------------------------------------------
# 平台相关：obsidian.json 路径
# -------------------------------------------------------------------------

def _obsidian_config_paths() -> list[Path]:
    """返回当前平台下可能的 Obsidian obsidian.json 路径列表（按优先级）。"""
    home = Path.home()
    system = platform.system()
    paths: list[Path] = []
    if system == "Darwin":
        paths.append(home / "Library" / "Application Support" / "obsidian" / "obsidian.json")
    elif system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(Path(appdata) / "obsidian" / "obsidian.json")
        paths.append(home / "AppData" / "Roaming" / "obsidian" / "obsidian.json")
    else:
        # Linux / others
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            paths.append(Path(xdg) / "obsidian" / "obsidian.json")
        paths.append(home / ".config" / "obsidian" / "obsidian.json")
        # Flatpak
        paths.append(home / ".var" / "app" / "md.obsidian.Obsidian" / "config" / "obsidian" / "obsidian.json")
    return paths


def _load_obsidian_vaults() -> list[dict]:
    """读所有 obsidian.json，返回 vault 列表（含 path、open、mtime）。"""
    results: list[dict] = []
    seen: set[str] = set()
    for cfg in _obsidian_config_paths():
        try:
            if not cfg.exists():
                continue
            with cfg.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        vaults = data.get("vaults", {})
        if not isinstance(vaults, dict):
            continue
        for vid, info in vaults.items():
            if not isinstance(info, dict):
                continue
            path = info.get("path")
            if not path or not isinstance(path, str):
                continue
            if path in seen:
                continue
            seen.add(path)
            try:
                mtime = float(info.get("ts", 0)) / 1000.0
            except Exception:
                mtime = 0.0
            exists = False
            try:
                exists = Path(path).exists()
            except Exception:
                exists = False
            results.append({
                "id": vid,
                "path": path,
                "open": bool(info.get("open", False)),
                "mtime": mtime,
                "exists": exists,
                "source_config": str(cfg),
            })
    # 按 (open desc, mtime desc) 排序
    results.sort(key=lambda v: (0 if v.get("open") else 1, -float(v.get("mtime") or 0)))
    return results


# -------------------------------------------------------------------------
# specode 配置文件 (~/.config/specode/config.json)
# -------------------------------------------------------------------------

def _specode_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / "specode" / "config.json"


def _load_specode_config() -> dict:
    p = _specode_config_path()
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _atomic_write_json(path: Path, payload: dict) -> None:
    """tempfile -> os.replace -> fsync。失败抛异常。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
        # fsync parent dir 提高跨进程一致性（Windows 上无效，忽略）
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            except OSError:
                pass
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _save_specode_config(cfg: dict) -> None:
    _atomic_write_json(_specode_config_path(), cfg)


# -------------------------------------------------------------------------
# 三层 resolve_doc_root
# -------------------------------------------------------------------------

def resolve_doc_root(override: Optional[str] = None) -> Tuple[Optional[Path], str]:
    """三层根目录解析。

    优先级：
      1. override 参数（--root 或 SPECODE_ROOT 环境变量）
      2. ~/.config/specode/config.json.obsidianRoot
      3. 自动 vault 检测（取第一个 exists 且 open 的 vault，或第一个 exists 的）

    返回 (Path|None, source)。source ∈ {'override', 'env', 'config', 'auto', 'none'}。
    """
    # 1. override 优先：参数 > 环境变量
    if override:
        p = Path(override).expanduser()
        return (p, "override")

    env_root = os.environ.get("SPECODE_ROOT")
    if env_root:
        return (Path(env_root).expanduser(), "env")

    # 2. config.json
    cfg = _load_specode_config()
    root = cfg.get("obsidianRoot") or cfg.get("docRoot")
    if root and isinstance(root, str):
        return (Path(root).expanduser(), "config")

    # 3. auto-detect
    vaults = _load_obsidian_vaults()
    for v in vaults:
        if v.get("exists"):
            return (Path(v["path"]), "auto")

    return (None, "none")


# -------------------------------------------------------------------------
# 子命令
# -------------------------------------------------------------------------

def cmd_detect(args: argparse.Namespace) -> int:
    vaults = _load_obsidian_vaults()
    payload = {
        "platform": platform.system(),
        "configs_checked": [str(p) for p in _obsidian_config_paths()],
        "vaults": vaults,
        "count": len(vaults),
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root, source = resolve_doc_root()
    payload: dict = {
        "doc_root": str(root) if root else None,
        "source": source,
        "exists": bool(root and root.exists()),
        "config_path": str(_specode_config_path()),
        "env_SPECODE_ROOT": os.environ.get("SPECODE_ROOT"),
    }
    if source == "none":
        payload["hint"] = (
            "未检测到 specode 根目录。可任选其一：\n"
            "  1) 运行 `spec_vault.py set --vault <path>` 写入持久配置；\n"
            "  2) 在环境变量中 export SPECODE_ROOT=<path>；\n"
            "  3) 在 Obsidian 中打开任意 vault 后再次运行 detect。"
        )
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return 3
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    target = args.vault or args.root
    if not target:
        sys.stderr.write("用法：spec_vault.py set --vault <path>   或   set --root <path>\n")
        return 3
    p = Path(target).expanduser().resolve()
    if not p.exists():
        sys.stderr.write(f"路径不存在：{p}\n请确认目录已创建后再次执行。\n")
        return 3
    if not p.is_dir():
        sys.stderr.write(f"路径不是目录：{p}\n")
        return 3
    cfg = _load_specode_config()
    cfg["obsidianRoot"] = str(p)
    cfg["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        _save_specode_config(cfg)
    except Exception as e:
        sys.stderr.write(f"写入 {_specode_config_path()} 失败：{e}\n")
        return 1
    sys.stdout.write(json.dumps({
        "ok": True,
        "doc_root": str(p),
        "config_path": str(_specode_config_path()),
    }, ensure_ascii=False, indent=2) + "\n")
    return 0


# -------------------------------------------------------------------------
# entry
# -------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="spec_vault.py", description="specode vault detection & root configuration")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("detect", help="探测平台 obsidian.json，列出已知 vault")
    sub.add_parser("status", help="输出当前 doc_root 与来源")

    p_set = sub.add_parser("set", help="写入 ~/.config/specode/config.json")
    g = p_set.add_mutually_exclusive_group(required=True)
    g.add_argument("--vault", help="设置 vault 根目录")
    g.add_argument("--root", help="设置 doc 根目录（不强调 vault 概念）")

    args = parser.parse_args(argv)
    if args.cmd == "detect":
        return cmd_detect(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "set":
        return cmd_set(args)
    parser.print_help()
    return 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
