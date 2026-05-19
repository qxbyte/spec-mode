# Obsidian / Document Root 解析

specode 的 spec 目录默认放在 Obsidian vault 内（也支持纯文件系统目录）。本文件给出三平台 `obsidian.json` 路径、三层根目录解析顺序、多 vault 选择策略、目录结构约定。

## 0. 文档目录结构

```text
<vault>/
└── spec-in/
 └── <os>-<username>/ ← e.g. macos-alice, windows-bob, linux-carol
 └── specs/
 ├── .active-specode.json ← v2 window index, slug-only
 └── <slug>/
 ├── requirements.md ← 或 bugfix.md（互斥）
 ├── design.md
 ├── tasks.md
 ├── acceptance-checklist.md
 ├── implementation-log.md（可选）
 └── .config.json ← per-spec lock + iteration state
```

路径段 `spec-in/<os>-<username>/specs` 由 `spec_vault.py` 的 `device_segment` 自动生成：

- `<os>` = `macos` / `windows` / `linux`。
- `<username>` = 当前操作系统用户名（`getpass.getuser`）。
- 同一 vault 在多设备 / 多用户共享时各设备的 spec 独立存放（避免锁串扰、避免文件冲突）。

`.active-specode.json` schema（v2，slug-only）：

```json
{
 "version": 2,
 "active_specs": [
 {
 "session_id": "abc-def-1234-...",
 "specId": "uuid",
 "slug": "login-password-rule",
 "phase": "tasks",
 "status": "active",
 "updated_at": "2026-05-19T10:05:00Z"
 }
 ]
}
```

`status` 取值：`active` / `readonly` / `evicted` / `ended`。多窗口同时活跃时数组里有多条。

## 1. 三层根目录解析（顺序固定）

由 `spec_init.py:resolve_document_root` 与 `spec_vault.py resolve_spec_root` 共同实现：

### 第 1 层：命令行 / 环境变量

- 显式参数 `--root <path>` 最高优先级。
- 环境变量 `SPECODE_ROOT` 次之。
- 命中 → 直接用，**不**追加 `spec-in/<os>-<user>/specs` 子结构（用户给什么就用什么）。

### 第 2 层：用户级配置

- 读 `~/.config/specode/config.json`（类 Unix 下也可走 `$XDG_CONFIG_HOME/specode/config.json`）。
- 字段 `obsidianRoot` 命中 → 自动追加 `spec-in/<os>-<user>/specs` 后使用。
- `rootOverride` 命中（由 `set --root` 写入）→ 直接用，不追加子结构。

### 第 3 层：自动检测 Obsidian vault

- 按当前平台读 Obsidian 全局配置 `obsidian.json`：

| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/obsidian/obsidian.json` |
| Windows | `%APPDATA%\obsidian\obsidian.json` |
| Linux | `~/.config/obsidian/obsidian.json` 或 `$XDG_CONFIG_HOME/obsidian/obsidian.json` |

- 读取 `vaults` 字段（dict，value 含 `path` / `ts` / `open` 等），按 §2 规则选 vault。

### 三层全 miss → 硬停 + 引导

`spec_init.py` exit 3，输出 SKILL.md §Document Root Resolution 中的引导文案（中文，三种设置方式）。**不**回退到 cwd、不回退到 `~/specs`、不回退到项目目录。

这条规则保证 spec 永远不会"被静默散布到不可预期的位置"。

## 2. 多 vault 选择规则

`spec_vault.py detect` 输出 vault 列表时按以下规则排序：

1. **过滤**：路径不存在的 vault 直接丢弃。
2. **优先选 `open=true` 的**：按 timestamp（`ts` 字段）降序取最新。
3. **若有多个 `open=true` 的 vault**：调用 `AskUserQuestion` 工具让用户选择（详见 §3）。
4. **若无 `open=true` 的 vault**：取 timestamp 最大的一个，并在 chat 提示"自动选 `<path>`；如需切换请运行 `/specode:spec --set-vault <other-path>`"。

选定 vault 后调 `spec_vault.py set --vault <path>` 把结果持久化到 `~/.config/specode/config.json.obsidianRoot`（下次跳过自动检测）。

## 3. 多 vault 选择的 UI 形式

多 vault 时按 SELECTOR_PROMPTS 同款三段式 YAML 格式呈现选择器（动态构造；path 来自 `spec_vault.py detect` 输出）。这是**动态选择器**——hook 不预生成、不在 11 个固定场景常量库中，由 SKILL.md 指引在 `--detect-vault` / 首次检测命中多 vault 时直接调工具。

```text
## 选择器节点：选择 Obsidian vault

**目的**：检测到多个已安装的 vault，需用户指定 specode 使用哪个目录。

**上下文**：当前未设置 obsidianRoot；`spec_vault.py detect` 返回 N 个 vault。

**前置动作（chat 简报，≤2 行）**：写一句"检测到 N 个 vault，请选择 specode 使用的目录。"

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "检测到多个 Obsidian vault，挑选 specode 使用的目录。"
    header: "选择 vault"
    multiSelect: false
    options:
      - label: "<vault-1 绝对路径>"
        description: "open=<true|false>，最近活动 <ts>"
      - label: "<vault-2 绝对路径>"
        description: "open=<true|false>，最近活动 <ts>"
      # 最多 4 个；超过 4 时取按 ts 最新的 4 个，其余在 chat 引导
      # 用户走 /specode:spec --set-vault <path>

**约束**：
- `multiSelect=false`；options ≤ 4（工具上限）。
- **不**给"（推荐）"——用户必须显式选；即使第一个 vault 看起来最近。
- 不要列出 `spec_vault.py detect` 之外的路径。
- 工具自动提供 "Other" + ESC；用户可在 Other 中输入自定义 vault 绝对路径。
- 工具返回后下一轮调 `spec_vault.py set --vault <chosen-path>` 持久化。
- 调用工具后立即 end turn。
```

如果只有一个 vault，**不**呈现选择器，直接调 `set --vault <path>` 并在 chat 简报一句"已绑定 vault `<path>`，如需切换运行 `/specode:spec --set-vault <other>`"。

## 4. `~/.config/specode/config.json` 生命周期

写入时机（仅这两种）：

1. **首次 Obsidian 检测后**：`resolve_spec_root` 检测到 vault → 自动保存。后续调用直接读此文件，不再重新检测。
2. **显式设置**：用户运行 `/specode:spec --set-vault <path>` 或 `--set-root <path>`，立即覆盖旧值。

文件内容示例：

```json
{
 "version": 1,
 "obsidianRoot": "/Users/alice/Documents/main-vault",
 "rootOverride": null,
 "specRootCache": "/Users/alice/Documents/main-vault/spec-in/macos-alice/specs",
 "lastDetectedAt": "2026-05-19T09:30:00Z"
}
```

`specRootCache` 是计算结果缓存（vault + device_segment）；若 `obsidianRoot` 或 `rootOverride` 改动，CLI 同步刷新。

不会在其他情况自动创建。Obsidian 未安装且未显式设置 → `resolve_spec_root` 返回 `None` → `spec_init.py` 抛引导提示并 exit 3。

## 5. 跨会话路径读取

对于持久 session 和 `/specode:continue`：

- 文档根目录从**各 spec 自身**的 `.config.json` 的 `documentRoot` 字段直接读取。
- **不**依赖 vault 检测或 `~/.config/specode/config.json`。
- vault 路径解析仅在**创建新 spec**（`/specode:spec <需求>`）时需要。

这保证已落地 spec 即使在不同设备 / 不同 vault 配置下仍能稳定恢复。

### 5.1 `/specode:continue` 无 slug 时的查找流程（**禁止 Grep 项目目录**）

> spec 文档**不在项目代码目录里**——它们在 `<vault>/spec-in/<os>-<user>/specs/` 之下（见 §1 目录约定）。模型**不能**用 `Grep` / `Glob` 去项目根目录扫 `**/.spec/**` 或 `**/specs/**`——找不到就会误判"无可继续 spec"，但实际上 spec 在 vault 里。

正确流程（必须严格按这个顺序，不要发挥）：

```bash
# step 1: 拿当前已配置 doc_root（只读 config.json，不重新检测）
python3 plugins/specode/scripts/spec_vault.py status
# → {"root": "...", "source": "env|config|auto|none"}

# step 2: 若 source=none → 提示用户运行 /specode:spec --set-vault <p> 后 end turn
#         若 source 有效 → 列出该 root 下全部 spec
python3 plugins/specode/scripts/spec_session.py list-specs
# → {"root": "...", "source": "...", "specs": [
#       {"slug": "...", "phase": "...", "lock_state": "held|free|stale",
#        "holder": "abc12345", "displayName": "...", "iterationRound": N,
#        "mtimes": {...}},
#       ...
#     ], "ok": true}
```

`list-specs` 的输出已经聚合了 spec 元数据 + 锁状态 + 文档 mtime——不需要再去读各 spec 的 `.config.json`。按 SELECTOR_PROMPTS 同款三段式 YAML 格式调 `AskUserQuestion`（这也是**动态选择器**，不在 11 个固定常量中，由本节指引直接调工具）：

```text
## 选择器节点：选择要继续的 spec

**目的**：用户运行 /specode:continue 无 slug；列出当前 doc_root 下全部可恢复 spec，让用户选。

**上下文**：当前 root=<root>，source=<env|config|auto>，找到 N 个 spec。

**前置动作（chat 简报，≤2 行）**：写一句"找到 N 个可继续 spec（M 个空闲 / K 个被持有），请选择。"

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "继续哪个 spec？"
    header: "选择 spec"
    multiSelect: false
    options:
      - label: "<slug-1>"
        description: "phase=<phase> 迭代=<N> lock=<held|free|stale> 最近修改 <ts>"
      - label: "<slug-2>"
        description: "phase=<phase> 迭代=<N> lock=<held|free|stale> 最近修改 <ts>"
      # 最多 4 个；超过 4 个时按 last_heartbeat_at 取最近 4 个，
      # 其余在 chat 引导用户用 /specode:continue <slug> 显式指定

**约束**：
- `multiSelect=false`；options ≤ 4。
- **不**给"（推荐）"——用户必须显式选。
- 工具自动提供 "Other"，允许用户输入 spec slug 或路径作为自定义答案。
- 工具返回后下一轮进入 `/specode:continue <slug>` 流程（详见 `references/workflow.md` §9.2）。
- 调用工具后立即 end turn。
- 锁状态描述用固定词：`held` / `free` / `stale`。
```

`list-specs` 返回 `specs: []` → **不**调工具，直接在 chat 引导用户用 `/specode:spec <需求>` 创建新 spec。

**绝不允许的回退路径**：

- ❌ `Grep('**/.spec/**')` 或 `Glob('**/specs/**')` 扫项目目录——spec 不在项目里
- ❌ 看到 `list-specs.specs == []` 就说"项目里没有 .spec/ 目录"——`list-specs` 已经是权威答案，空列表就是"该 root 下确实没有 spec"，引导用户用 `/specode:spec <需求>` 创建
- ❌ 假设 spec 在 cwd 之下（spec 永远在 `<doc_root>/specs/<slug>/`）

## 6. 旧位置警告

`/specode:spec --set-vault` / `--set-root` 执行后，`spec_vault.py` 会扫描历史 fallback 位置：

- `<cwd>/specs/`
- `~/new project/specs/`
- `~/specs/`

发现遗留 spec 目录 → 输出：

```text
⚠ 旧位置仍有 N 个 spec（不会自动迁移）：
 - /path/to/old/spec-1
 - /path/to/old/spec-2
 ...

如需迁移，请手动 mv 并更新各 spec 的 .config.json.documentRoot 字段；
否则旧位置 spec 在新 root 下不可见。
```

最多列出 10 个，多余的提示总数。**不**自动迁移（避免静默移动用户文件）。

## 7. `spec_vault.py` 命令参考

```text
python3 plugins/specode/scripts/spec_vault.py detect
 列出已安装的 vault；未检测到时给出手动指定提示

python3 plugins/specode/scripts/spec_vault.py status
 显示当前解析到的根目录及来源（cli / env / config / auto）

python3 plugins/specode/scripts/spec_vault.py set --vault <path>
 绑定 vault（写入 config.json.obsidianRoot；自动追加 spec-in/<os>-<user>/specs 子结构）

python3 plugins/specode/scripts/spec_vault.py set --root <path>
 直接指定根目录（写入 config.json.rootOverride；不追加 spec-in/<os>-<user>/specs）
```

退出码：0 ok / 3 用户引导（含 hard-stop 提示）。

## 8. 跨文档引用

- 三层解析的引导文案 → SKILL.md §Document Root Resolution。
- 锁与多窗口接管 → `references/lock-protocol.md`。
- 选择器三种类型与具体场景（非 vault 选择场景，如 takeover-options）→ `references/prompts.md`。
- vault 内目录约定与 phase 序列的关系 → `references/workflow.md`。
