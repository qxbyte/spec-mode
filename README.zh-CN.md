<p align="right"><a href="./README.md">English</a> | <strong>中文</strong></p>

# specode

面向 CLI 编码代理的规格驱动工作流插件。

本插件**全部 hook 改为提醒式**——每个 hook 只往模型上下文里注入提示文本（`exit 0` + `additionalContext`），**永不阻断**工具调用。状态绑定到宿主注入的 `session_id`（多窗口天然不混淆），五份 spec 文档（`requirements.md` / `bugfix.md` / `design.md` / `tasks.md` / `implementation-log.md`）仍是事实源。

## 能力概览

- **会话状态绑定 `session_id`**。每个宿主会话拥有独立的状态文件 `~/.specode/sessions/<session_id>.json`，含 mode（active / readonly / ended）、active spec slug、phase、锁状态、当前 `pending_selector`、task_swarm_run_id。所有写操作原子化（tempfile + os.replace + fsync）。
- **持久会话是唯一模式**。`/specode:spec <需求>` 始终创建持久会话；`/specode:end` 写入 `mode=ended`，hook 立刻在下一 turn 停止注入。无 `--persist` 标志。
- **7 个提醒式 hook（`exit 0`、永不阻断）**：SessionStart / UserPromptSubmit×2（提示注入 + 静默续锁）/ PreToolUse / Stop / PostToolUse Task / SessionEnd。共同负责会话生命周期、phase-gate 选择器提醒、代码-文档同步、task-swarm 节点提醒、状态行 footer 要求。
- **task-swarm 多 agent 并发**：tasks.md 确认后选 `用 task-swarm 多 agent 并发`，主代理切到编排模式——`task_swarm.py init/plan/advance/writeback` 状态机驱动；多 coder 并发（按 `@writes` 文件冲突自动切 group），reviewer / validator 单实例物理隔离（无 Edit/Write 工具）；validator fail → coder 循环修复直到 pass（3 轮同 fail 签名 → 死循环保护）；reviewer P0 带证据标签触发一次性修复，全部 findings 写回 tasks.md。
- **选择器文本由模型生成**。hook 只注入"该呈现哪种类型 的 哪个场景 选择器"元信息，模型按 3 种骨架（A 单选 / B wizard / C 复选）格式化文本。11 个固定场景常量存放在 `spec_session.py` 的 `SELECTOR_PROMPTS` 字典里。
- **active 期间每次响应都有状态行 footer**：
  ```
  ─── spec-mode ─── spec: <slug> | session: <前 8 位> | phase: <p> | /specode:end 退出
  ```
  只读模式追加 `[只读]` 字段。
- **Document-first 纪律**。UserPromptSubmit 注入"📝 文档优先提醒（输入侧）"提示模型在写代码前评估是否需要先 Edit 文档；Stop 注入"🔄 代码-文档同步提醒（输出侧）"提示模型自检是否漏补文档。**全程提醒，不阻断**。
- **`/specode:spec -h` 帮助 fast-path**。hook 命中后注入完整帮助文本要求逐字打印，替代旧版"模型读 references/help-output.md"的不稳定路径。
- **`spec-writer` agent**——物理隔离的文档生成 agent（工具仅 Read / Write / Edit / Grep / Glob；无 Bash，无法跑命令）。

## 项目结构

```
.claude-plugin/marketplace.json   ← 单插件 marketplace 清单
plugins/specode/
  .claude-plugin/plugin.json      ← 插件清单
  hooks/hooks.json                ← 4 个提醒式 hook
  commands/                       ← /specode:spec, /specode:continue,
                                    /specode:end, /specode:status,
                                    /specode:task-swarm
  agents/                         ← task-swarm-{planner,coder,reviewer,
                                    validator}（v0.7）, spec-writer（v0.6）
  scripts/
    spec_vault.py                 ← 三层文档根解析 + Obsidian vault 探测
    spec_init.py                  ← spec 目录初始化 + 原子双写
    spec_session.py               ← 业务 + hook 子命令；含
                                    SELECTOR_PROMPTS 常量库
    spec_lint.py                  ← 4 条 advisory lint 规则
    spec_status.py
    run.sh / run.cmd              ← Python 跨平台启动器
  skills/specode/
    SKILL.md                      ← spec-mode 纪律契约
    references/                   ← workflow / lock-protocol /
                                    obsidian / prompts（选择器场景库）/
                                    templates / iteration
  assets/templates/               ← 文档模板
  tests/                          ← 75 个 pytest 用例
```

## 安装

### GitHub（推荐）

使用你已安装的任一 CLI；插件清单两边通用。CodeBuddy 已在 2.97.1 上验证。

```sh
# CodeBuddy
codebuddy plugin marketplace add https://github.com/qxbyte/specode
codebuddy plugin install specode@specode

# Claude Code
claude plugin marketplace add https://github.com/qxbyte/specode
claude plugin install specode@specode
```

### 一次性会话

```sh
# Claude Code 支持通过 --plugin-url 一次性安装
claude --plugin-url https://github.com/qxbyte/specode/archive/refs/heads/main.zip
```

### 本地开发

```sh
git clone https://github.com/qxbyte/specode.git
claude    --plugin-dir ./specode/plugins/specode
codebuddy --plugin-dir ./specode/plugins/specode
```

### 卸载

```sh
claude plugin uninstall specode@specode
claude plugin marketplace remove specode
# 可选：清理用户级状态
rm -rf ~/.specode ~/.config/specode
```

## 使用

```
/specode:spec <需求>                     # 创建持久 spec 会话
/specode:continue [slug]                 # 恢复 / 切换
/specode:status                          # 查看当前会话状态
/specode:end                             # 结束会话（保留文档）

/specode:spec --set-vault <路径>         # 绑定 Obsidian vault
/specode:spec --set-root <路径>          # 绑定非 vault 文档根
/specode:spec --detect-vault             # 列出已安装 vault
/specode:spec --vault-status             # 显示当前文档根与来源
/specode:spec -h                         # 完整帮助（hook fast-path）
```

会话激活后：

- 每次用户 prompt 都会注入状态块 + 选择器提醒（命中 phase-gate 时）+ 文档优先提醒。
- 每次模型 turn 结束时注入代码-文档同步提醒。
- 模型必须按三种类型骨架渲染选择器，并在响应末尾输出状态行 footer。

## 会话日志收集（0.10.0+）

specode 默认把每个 session 的事件流写到 `~/.specode/logs/<session_id>.jsonl`
（含 hook 触发、主代理工具调用、specode CLI 调用、phase/lock 变化），
便于排查"主代理为什么走偏 / 漏 fork spec-writer / 选错 selector"等问题。

```sh
# 回放一个 session
sh "$CLAUDE_PLUGIN_ROOT/scripts/run.sh" \
   "$CLAUDE_PLUGIN_ROOT/scripts/spec_log.py" replay --session <id>

# 临时关
export SPECODE_LOG=off

# 永久关：编辑 ~/.config/specode/config.json 加 "logging": false
```

默认 redact 黑名单（`password / api_key / token / …`）+ 字符串字段自动
截断到 500 字符。可通过 `~/.config/specode/config.json.redact_keys`
扩展黑名单。

## 全局 bypass

```sh
SPECODE_GUARD=off   # 让所有 hook 立即 exit 0
SPECODE_LOG=off     # 让 session 日志不写入
```

仅作调试用。

## 贡献

参见 [`CONTRIBUTING.md`](./CONTRIBUTING.md)：runtime 仅限标准库、hook 安全契约（提醒式、永不 `exit 2`）、测试规范。

## 许可证

MIT
