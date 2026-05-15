# Spec Mode Commands Reference

> 命令入口、子标志 dispatch、可选 spec 名前缀、会话模式、Helper Scripts、Hook 拦截。
> SKILL.md 仅保留最常用片段；细节在此。

## Command Entry

```text
/spec <requirement or path> [extras]      ← one-shot workflow
/spec --persist <requirement or path>     ← persistent session (footer + /end)
/continue [spec-slug]                     ← resume / switch; multi-window aware
/status                                   ← show current session status
/end                                      ← end persistent session (docs preserved)

/spec --set-vault <vault-path>            ← set Obsidian vault → vault/spec-in/<os>-<user>/specs
/spec --set-root <dir>                    ← set any directory as spec root
/spec --detect-vault                      ← detect installed Obsidian vaults
/spec --vault-status                      ← show current root + obsolete-location warnings

/spec --freeform                          ← relax INV-1 for current spec (INV-2 still enforced)
/spec --strict                            ← restore INV-1
/spec --sync-status                       ← show sync ledger / pending / last violation

/spec -h                                  ← help (hook-intercepted; bypasses model)
```

`--set-vault` / `--set-root` 任何时候都可运行；新值立即写入 `~/.config/spec-mode/config.json` 并被后续命令使用。

`/spec` 后文本若是已存在的文件路径 → 当作需求源读入；否则当作需求描述。

## Sub-flag Dispatch

执行后立即停止 —— **不**触发 spec 工作流、**不**创建 spec 目录、**不**进入 Plan-mode。

| Flag | Action |
|---|---|
| `--set-vault <path>` | `python3 scripts/spec_vault.py set --vault <path>` |
| `--set-root <path>` | `python3 scripts/spec_vault.py set --root <path>` |
| `--detect-vault` | `python3 scripts/spec_vault.py detect` |
| `--vault-status` | `python3 scripts/spec_vault.py get` |
| `-h` / `--help` | Output `references/help-output.md` 第一个 ```text``` 围栏块 verbatim (Fast Path — see SKILL.md §Help Output) |
| `--persist <req>` | `spec_init.py --persistent`, then start workflow |
| `--freeform` | `python3 scripts/spec_sync.py freeform on` |
| `--strict` | `python3 scripts/spec_sync.py freeform off` |
| `--sync-status` | `python3 scripts/spec_sync.py status` |

Sub-flag dispatch 由模型按本表执行，不进入 intake 流程。`-h` / `--vault-status` / `--detect-vault` / `--sync-status` 走 **Fast Path**（详见 SKILL.md §Help Output）—— 单文件读取或单脚本调用，输出 verbatim，不思考、不解释。

## Optional Spec Name Prefix

若需求文本以 `<名称>：<内容>`（全角 `：`）或 `<名称>: <内容>`（ASCII `:` 后跟空格）开头，按首个冒号拆分：

- **冒号前** = spec 文件夹名提示。Agent 派生英文 slug，调 `spec_init.py --name <slug> --requirement-name "<原名称>"`。`.config.json.requirementName` 保留原中文名作为显示名。
- **冒号后** = 需求源文本。
- **跳过条件**：前缀像路径（含 `/` 或 `\`）、URL、或前 30 字内无冒号。
- 无冒号 → 整段作需求；slug 由 agent 从需求内容推断。

## Sessions: One-shot vs Persistent

每次 `/spec` 都生成永久文档（`requirements.md` 或 `bugfix.md`, `design.md`, `tasks.md`（含 `## 测试要点` 节）, `.config.json`），随时可通过 `/continue` 重开。

| | one-shot `/spec` | `/spec --persist` |
|--|--|--|
| 任务完成后会话 | 自动结束 | 保持活跃 |
| 状态 footer | 不显示 | 每次回复显示 |
| 退出方式 | 自动 | 显式 `/end` |

**Persistent footer 格式（仅持久会话）**：

```
─── spec-mode ─── spec: <slug> | session: <sessionId> | phase: <phase> | /end 退出
```

只读模式额外标记 `[只读]`（见 `lock-protocol.md`）：

```
─── spec-mode ─── spec: <slug> | session: <id> | phase: <phase> | [只读] | /end 退出
```

**sessionId 解析顺序**：`$TERM_SESSION_ID` → `$SPEC_SESSION_ID` → `"default"`。多窗口并行需要每窗口独立 sessionId。

**状态文件**：
- `<spec-dir>/.config.json` — per-spec 身份、生命周期、**锁**、sessions、iteration round
- `<document-root>/.active-spec-mode.json` — v2 窗口索引，按 sessionId 索引（slug-only，不含绝对路径）

## Helper Scripts

- `scripts/spec_init.py` — 创建 spec 目录；**必须传 `--name <slug>`**（agent 派生 slug）
- `scripts/spec_session.py` — `start / continue / status / end / list / list-specs / load / acquire / release / heartbeat / verify-lock / iterate`
- `scripts/spec_vault.py` — `detect / set --vault / set --root / get`
- `scripts/spec_lint.py` — 校验 spec 文件（含锁字段）
- `scripts/spec_status.py` — 任务进度视图（`spec_session.py load --json` 的薄包装）
- `scripts/spec_choice.py` — 选择器；TTY → curses ↑/↓ + Enter；非 TTY（Claude Code Bash / CI）→ 打印 option 块 + `AWAITING_USER_CHOICE` 哨兵 + exit 0，agent 把 stdout 原样转发给用户后结束 turn
- `scripts/spec_sync.py` — Code-Doc Sync ledger（`status / freeform / strict` 等）
- `scripts/spec_guard.py` — Hook 入口（INV-1/2/3/4/6 强制 + SessionStart/End 跟踪）
