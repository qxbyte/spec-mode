# Obsidian Integration Reference

## 文档目录结构

```text
<vault>/
└── spec-in/
    └── <os>-<username>/          ← e.g. macos-alice, windows-bob, linux-carol
        └── specs/
            ├── .active-spec-mode.json
            └── <requirement-name>/
                ├── requirements.md
                ├── bugfix.md
                ├── design.md
                ├── tasks.md
                └── .config.json
```

路径段 `spec-in/<os>-<username>/specs` 由 `scripts/spec_vault.py` 的 `device_segment()` 自动生成，确保同一 vault 在多设备/多用户共享时各设备的 spec 独立存放。

## config.json 生命周期

`~/.config/spec-mode/config.json` 在两种情况下写入：

- **首次 Obsidian 检测**：`resolve_spec_root()` 检测到 vault 后计算路径并自动保存。后续调用直接读取此文件，不再重新检测 Obsidian。
- **显式设置**：用户运行 `/spec --set-vault` 或 `/spec --set-root`（任何时候可执行，立即覆盖旧值）。

此文件不会自动创建于其他情况。若 Obsidian 未安装且未显式设置，`resolve_spec_root()` 返回 `None`，由 `spec_init.py` 抛出引导提示并终止（不再回退到项目目录或默认路径）。

## 跨会话路径读取

对于持久 session 和跨会话恢复（`/spec-continue`），文档根目录从各 spec 自身的 `.config.json`（`documentRoot` 字段）直接读取，**不依赖** vault 检测或 `~/.config/spec-mode/config.json`。vault 路径解析仅在创建新 spec 时需要。

## 旧位置警告

`/spec --set-vault` / `--set-root` 执行后，`spec_vault.py` 会扫描历史 fallback 位置（`<cwd>/specs`、`~/new project/specs`）。若发现遗留 spec 目录，输出 `⚠ 旧位置仍有 N 个 spec（不会自动迁移）` 警告，并列出最多 10 个 spec 路径。如需迁移，用户手动 `mv` 并更新各 spec 的 `.config.json.documentRoot` 字段。

## 平台 Obsidian 配置文件路径

`spec_vault.py` 按当前平台读取 Obsidian 的全局配置文件以获取已注册 vault 列表：

| Platform | Path |
|----------|------|
| macOS    | `~/Library/Application Support/obsidian/obsidian.json` |
| Windows  | `%APPDATA%\obsidian\obsidian.json` |
| Linux    | `~/.config/obsidian/obsidian.json` (or `$XDG_CONFIG_HOME/obsidian/obsidian.json`) |

`obsidian.json` 中的 `vaults` 字段包含所有已注册 vault 的路径、时间戳和 `open` 状态。

## 多 Vault 选择逻辑

1. 过滤掉路径不存在的 vault。
2. 优先选 `open: true` 的 vault，按时间戳降序取最新。
3. 若有多个 `open: true` 的 vault，使用 `spec_choice.py` 让用户选择，然后通过 `spec_vault.py set --vault` 保存选择。
4. 若无 `open` vault，取时间戳最大的一个。

## spec_vault.py 命令参考

```text
python3 scripts/spec_vault.py detect           ← 列出已安装的 vault，未检测到时给出手动指定提示
python3 scripts/spec_vault.py set --vault <p>  ← 绑定 vault（写入 config.json）
python3 scripts/spec_vault.py set --root <p>   ← 直接指定根目录（写入 config.json）
python3 scripts/spec_vault.py get              ← 显示当前解析到的根目录及来源
```

`set --vault <p>` 自动将 spec root 设为 `<p>/spec-in/<os>-<user>/specs`。
`set --root <p>` 使用完全自定义路径，不附加 `spec-in/` 子目录结构。
