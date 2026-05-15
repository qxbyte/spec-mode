#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import re
import sys
from pathlib import Path

CONFIG_FILE = Path.home() / ".config" / "spec-mode" / "config.json"


def _safe_username() -> str:
    """Return a filesystem-safe username, stripping domain prefix on Windows."""
    try:
        username = getpass.getuser()
    except Exception:
        username = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    # Strip DOMAIN\user or domain/user prefix (Windows domain accounts)
    username = re.sub(r"^[^/\\]+[/\\]", "", username)
    # Replace characters that are invalid or awkward in directory names
    username = re.sub(r"[^\w.-]", "-", username).strip("-")
    return username or "user"


def device_segment() -> Path:
    """Return the vault-relative Path for this machine: spec-in/<os>-<user>/specs."""
    os_map = {"Darwin": "macos", "Windows": "windows"}
    os_name = os_map.get(platform.system(), platform.system().lower())
    return Path("spec-in") / f"{os_name}-{_safe_username()}" / "specs"


def obsidian_config_path() -> Path | None:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        return Path(appdata) / "obsidian" / "obsidian.json" if appdata else None
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "obsidian" / "obsidian.json"


def read_vaults() -> list[dict]:
    config_path = obsidian_config_path()
    if not config_path or not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        result = []
        for _vid, v in (data.get("vaults") or {}).items():
            path_str = v.get("path")
            if path_str and Path(path_str).exists():
                result.append({
                    "path": path_str,
                    "ts": v.get("ts", 0),
                    "open": v.get("open", False),
                })
        return result
    except Exception:
        return []


def pick_best_vault(vaults: list[dict]) -> dict | None:
    if not vaults:
        return None
    open_vaults = sorted([v for v in vaults if v.get("open")], key=lambda v: v["ts"], reverse=True)
    if open_vaults:
        return open_vaults[0]
    return sorted(vaults, key=lambda v: v["ts"], reverse=True)[0]


def read_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def write_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def resolve_spec_root() -> tuple[Path | None, str]:
    """Return (resolved_path, source_tag) or (None, 'not_found').

    On first successful Obsidian detection, auto-saves the resolved path to
    config.json so subsequent calls are stable even if Obsidian is not running.
    """
    env_root = os.environ.get("SPEC_MODE_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve(), "env"

    cfg = read_config()
    if cfg.get("obsidianRoot"):
        return Path(cfg["obsidianRoot"]).expanduser().resolve(), "config"

    vaults = read_vaults()
    best = pick_best_vault(vaults)
    if best:
        root = Path(best["path"]) / device_segment()
        cfg["vaultPath"] = best["path"]
        cfg["obsidianRoot"] = str(root)
        write_config(cfg)
        return root, "obsidian"

    return None, "not_found"


def configured_spec_root() -> tuple[Path | None, str]:
    """Return the spec root explicitly recorded by spec-mode config.json."""
    cfg = read_config()
    if cfg.get("obsidianRoot"):
        return Path(cfg["obsidianRoot"]).expanduser().resolve(), "config"
    return None, "not_found"


def list_other_root_specs(current_root: Path) -> list[dict[str, object]]:
    """Look for spec folders living under known historical fallback locations.

    Used to warn users after `--set-root` / `--set-vault` so they know specs
    created under the old root are not auto-migrated.
    """
    candidates: list[Path] = []
    cwd = Path.cwd().resolve()
    candidates.append(cwd / "specs")
    candidates.append(Path.home() / "new project" / "specs")
    seen: list[dict[str, object]] = []
    for candidate in candidates:
        if not candidate.exists() or candidate.resolve() == current_root.resolve():
            continue
        for child in sorted(candidate.iterdir()):
            if child.is_dir() and (child / ".config.json").exists():
                seen.append({"slug": child.name, "path": str(child)})
    return seen


def command_detect(args: argparse.Namespace) -> int:
    config_path = obsidian_config_path()
    vaults = read_vaults()
    best = pick_best_vault(vaults)
    cfg = read_config()

    if args.json:
        print(json.dumps({
            "platform": platform.system(),
            "obsidianConfigPath": str(config_path) if config_path else None,
            "obsidianConfigExists": bool(config_path and config_path.exists()),
            "vaults": vaults,
            "bestVault": best,
            "specModeConfig": str(CONFIG_FILE),
            "specModeConfigExists": CONFIG_FILE.exists(),
            "currentConfig": cfg,
        }, ensure_ascii=False, indent=2))
        return 0

    if not vaults:
        print("未检测到 Obsidian 安装，或没有已注册的 vault。")
        print(f"  Obsidian 配置路径: {config_path}")
        print()
        print("请选择以下方式之一：")
        print("  1. 安装 Obsidian 后重试（推荐）")
        print("  2. /spec --set-vault <vault路径>")
        print("  3. /spec --set-root <自定义目录>")
    else:
        print(f"检测到 {len(vaults)} 个 vault：")
        for v in vaults:
            marker = "► " if v == best else "  "
            status = "open" if v.get("open") else "closed"
            print(f"{marker}{v['path']}  [{status}]")
        if best:
            print(f"\n将使用: {Path(best['path']) / device_segment()}")
    return 0


def command_set(args: argparse.Namespace) -> int:
    cfg = read_config()
    changed = False

    if args.vault:
        vault = Path(args.vault).expanduser().resolve()
        segment = device_segment()
        cfg["vaultPath"] = str(vault)
        cfg["obsidianRoot"] = str(vault / segment)
        changed = True
        print(f"vault:     {vault}")
        print(f"spec root: {vault / segment}")

    if args.root:
        cfg["obsidianRoot"] = str(Path(args.root).expanduser().resolve())
        changed = True
        print(f"spec root: {cfg['obsidianRoot']}")

    if not changed:
        print("未指定任何参数。可用选项：", file=sys.stderr)
        print("  --vault <vault路径>    设置 Obsidian vault，spec 存入 vault/spec-in/<os>-<user>/specs", file=sys.stderr)
        print("  --root  <目录>         直接指定 spec 文档根目录（完全自定义路径）", file=sys.stderr)
        return 1

    write_config(cfg)
    print(f"\n配置已保存至: {CONFIG_FILE}")
    print("  (此后每次 /spec 自动使用此路径；任何时候可再次运行 set 修改)")

    new_root = Path(cfg["obsidianRoot"]).expanduser().resolve()
    others = list_other_root_specs(new_root)
    if others:
        print()
        print(f"⚠ 检测到旧位置仍有 {len(others)} 个 spec（不会自动迁移）：")
        for entry in others[:10]:
            print(f"    - {entry['slug']}   {entry['path']}")
        if len(others) > 10:
            print(f"    ... 还有 {len(others) - 10} 个")
        print("  如需迁移，请手动 mv 并更新各 spec 的 .config.json.documentRoot 字段。")
    return 0


def command_get(args: argparse.Namespace) -> int:
    if args.configured_only:
        root, source = configured_spec_root()
    else:
        root, source = resolve_spec_root()
    cfg = read_config()

    if args.json:
        print(json.dumps({
            "specRoot": str(root) if root else None,
            "source": source,
            "config": cfg,
            "configFile": str(CONFIG_FILE),
        }, ensure_ascii=False, indent=2))
        return 0

    source_labels = {
        "env": "SPEC_MODE_ROOT 环境变量",
        "config": "spec-mode 配置文件",
        "obsidian": "Obsidian 自动检测",
        "not_found": "未配置",
    }
    if root:
        print(f"spec 文档根目录: {root}")
        print(f"来源: {source_labels.get(source, source)}")
        others = list_other_root_specs(root)
        if others:
            print()
            print(f"⚠ 旧位置仍有 {len(others)} 个 spec（不会自动迁移）：")
            for entry in others[:10]:
                print(f"    - {entry['slug']}   {entry['path']}")
            if len(others) > 10:
                print(f"    ... 还有 {len(others) - 10} 个")
    else:
        print("未配置 spec 文档根目录。")
        print()
        print("请选择以下方式之一：")
        print("  1. 安装 Obsidian 后重试（推荐）")
        print("  2. /spec --set-vault <vault路径>")
        print("  3. /spec --set-root <自定义目录>")
    print(f"配置文件: {CONFIG_FILE} ({'存在' if CONFIG_FILE.exists() else '不存在'})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Obsidian vault detection and spec-mode root configuration.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    detect_p = sub.add_parser("detect", help="检测已安装的 Obsidian vault。")
    detect_p.add_argument("--json", action="store_true")
    detect_p.set_defaults(func=command_detect)

    set_p = sub.add_parser("set", help="设置 spec 文档根目录或 vault 路径。")
    set_p.add_argument("--vault", help="Obsidian vault 路径。spec root = vault/spec-in/<os>-<user>/specs。")
    set_p.add_argument("--root", help="直接指定 spec 文档根目录（完全自定义路径）。")
    set_p.set_defaults(func=command_set)

    get_p = sub.add_parser("get", help="显示当前解析到的 spec 文档根目录。")
    get_p.add_argument("--json", action="store_true")
    get_p.add_argument("--configured-only", action="store_true", help="只读取 spec-mode config.json 中记录的根目录，不自动检测或回退。")
    get_p.set_defaults(func=command_get)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
