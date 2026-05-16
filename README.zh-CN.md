<p align="right"><a href="./README.md">English</a> | <strong>中文</strong></p>

# spec-mode

面向 **Claude Code** 与 **CodeBuddy** 的规格驱动工作流插件。

承担约束的工作流规则由 Claude Code hooks 强制——也就是 harness 实际执行的确定性 shell 命令——而不是依赖模型在上下文里"记住并遵守"指令。

## 强制保证（Invariants）

只要 spec 处于激活状态，下列规则即由 **harness 硬强制**：

| ID | 规则 | Hook |
|---|---|---|
| **INV-1** | 编辑源文件，必须满足以下任一：该文件被列在 `tasks.md` / `## Affected Files`；或同一轮内编辑了 `design.md` / `tasks.md` / `bugfix.md`；或处于 `freeform` 模式 | `PreToolUse` |
| **INV-2** | 触碰过源代码的 turn，结束前必须至少触碰一份 spec 文档 | `Stop` |
| **INV-3** | 当前会话已被其他窗口驱逐时，spec 文档写入被拒绝 | `PreToolUse` |
| **INV-4** | 编辑 `requirements.md` / `bugfix.md` 必须在同一轮内更新 `tasks.md`（其 `## 测试要点` 节，由 SHALL 衍生） | `Stop` |
| **INV-5** | 每个用户 turn 自动注入状态块（`spec / phase / lock / turn`）到模型上下文 | `UserPromptSubmit` |
| **INV-6** | 实现前阶段（intake / requirements / bugfix / design / tasks）绝对禁止源代码编辑 | `PreToolUse` |

INV-1 与 INV-2 共同构成 **Code-Doc Sync Guard (CDSG)**。

## 项目结构

```
.claude-plugin/marketplace.json   ← 单插件 marketplace 清单
plugins/spec-mode/
  .claude-plugin/plugin.json      ← 插件清单
  hooks/hooks.json                ← 6 个事件处理器，挂哨兵 shell 短路
  hooks/hooks-probe.json          ← 诊断探针（重新验证时替换上去）
  skills/spec-mode/               ← skill 内容（SKILL.md + references）
  commands/                       ← /spec, /continue, /status, /end
  scripts/
    spec_guard.py                 ← hook 入口；分发 + 审计日志
    spec_state.py                 ← 只读状态探测 + 哨兵 + Claude 会话登记
    spec_sync.py                  ← INV-1/2/3/4/6 逻辑；ledger；阶段闸门；glob 匹配
    spec_session.py               ← 锁 + 阶段 + active-pointer 模型
    spec_init.py / spec_lint.py / spec_status.py / spec_choice.py / spec_vault.py
  tests/                          ← 19 个 pytest 用例（单元 + 集成）
```

## 安装

### 通过 GitHub（推荐）

```sh
# Claude Code
claude plugin marketplace add https://github.com/qxbyte/spec-mode
claude plugin install spec-mode@spec-mode

# CodeBuddy（已在 2.97.1 上验证）
codebuddy plugin marketplace add https://github.com/qxbyte/spec-mode
codebuddy plugin install spec-mode@spec-mode
```

两个 harness 都会克隆 marketplace、定位到 `plugins/spec-mode/` 下的插件，然后自动加载 `hooks/`、`skills/`、`commands/`。后续升级用 `claude plugin update spec-mode` 或 `claude plugin marketplace update spec-mode`。

### 一次性会话（仅 Claude Code）

```sh
claude --plugin-url https://github.com/qxbyte/spec-mode/archive/refs/heads/main.zip
```

仅当前会话生效，不持久化任何状态。

### 本地开发

```sh
git clone https://github.com/qxbyte/spec-mode.git
claude    --plugin-dir ./spec-mode/plugins/spec-mode
codebuddy --plugin-dir ./spec-mode/plugins/spec-mode
```

加载后：

```
/help                              # 列出 /spec-mode:* 系列命令
/reload-plugins                    # 修改插件文件后重新加载
```

Hook 行为日志写入 `~/.spec-mode/audit/<date>.log`（UTC）。

## 使用

会话内（插件已加载）：

```
/spec-mode:spec --persist <需求>            # 启动持久 spec 会话
/spec-mode:continue [slug]                  # 恢复 / 切换 spec
/spec-mode:status                           # 查看当前会话状态
/spec-mode:end                              # 结束持久会话

/spec-mode:spec --freeform                  # 放宽 INV-1（INV-2 仍然强制）
/spec-mode:spec --strict                    # 恢复 INV-1
/spec-mode:spec --sync-status               # 查看 ledger / 待同步项 / 上次违规
```

spec 激活后：

- 每个用户 prompt 都会被附加一段 `spec-mode active` 状态块，标注 spec、phase、锁状态、turn id、freeform 模式
- 对项目源文件的编辑（不在 `tasks.md` 列表内的）会被拦截，除非同一轮先动了文档（INV-1）
- 触碰过代码的 turn 在停止前未触碰任何文档时会被拦下，模型必须补一条 `design.md` / `tasks.md` / `implementation-log.md`（INV-2）
- 改 `requirements.md` / `bugfix.md` 必须在同一轮更新 `tasks.md` 的 `## 测试要点` 节（INV-4）
- intake / requirements / bugfix / design / tasks 阶段绝对不允许源代码编辑——freeform **不**豁免 INV-6

## 不对称约束说明

INV-2 是**单向**的：源代码变更 ⇒ 必须有文档变更，但纯文档编辑（错别字、措辞调整）**不**强制配套代码变更。`implementation-log.md` 算作满足 INV-2 的轻量文档动作——`spec_lint.py` 对短于 30 字符或没有引用任何实际代码文件的日志条目会发出软 WARNING（防止"装饰性文档"绕规则）。

## 性能预算

| Hook | 墙钟预算 |
|---|---|
| `SessionStart` / `SessionEnd` | 总会跑 Python；<500ms |
| `UserPromptSubmit` | 仅当 `~/.spec-mode/.any-active` 哨兵存在时跑 Python；<80ms |
| `PreToolUse` / `PostToolUse` / `Stop` | 同样的 shell 短路；运行时 <100ms |

无 spec 激活时，shell 那行 `[ ! -e ~/.spec-mode/.any-active ]` 直接退出，Python 根本不启动 → 实际成本可忽略。

## CodeBuddy 支持

已在 CodeBuddy 2.97.1 上验证：相同的 `hooks/hooks.json` 与 `scripts/spec_guard.py` 不需任何修改即可工作。CodeBuddy 内部基于 Claude Code 2.1.142 agent，并同时注入 `CLAUDE_PLUGIN_ROOT` 与 `CODEBUDDY_PLUGIN_ROOT`，因此集成是字节级兼容的。

## 贡献

参见 [`CONTRIBUTING.md`](./CONTRIBUTING.md)：runtime 仅限标准库、hook 安全契约、测试规范。

## 许可证

MIT
