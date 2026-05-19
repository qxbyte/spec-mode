# Selectors — `AskUserQuestion` 调用规范

每个 phase-gate 节点必须**调用宿主内置 `AskUserQuestion` 工具**呈现选择器；工具自身渲染 chip-tabs / 选项列表 / 上下键导航 / 回车提交 / ESC 取消 / "Other" 自定义输入 UI。模型只负责传参，**绝不**自己输出 markdown 列表让用户回复编号。

本文件给出全部 8 个固定场景的 (类型 → AskUserQuestion 参数)；spec_session.py 的 `SELECTOR_PROMPTS` 字典是这些模板的运行时常量库。

---

## 类型 → AskUserQuestion 参数形态

| 类型 | `questions` 数组 | `multiSelect` | UI 形态 |
|---|---|---|---|
| **A 单列单选** | 1 个 question | `false` | 一个问题 + N 个互斥选项；用户上下键选 + 回车 |
| **B wizard（多项串行决策）** | 2-4 个 question | 每个都 `false` | 顶部 chip-tabs（每个 question 一个 tab）+ 每 tab 单选；全部填完才能 Submit |
| **C 复选框多选** | 1 个 question | `true` | 一个问题 + N 个非互斥选项 + checkbox UI |

公共参数约束（来自 `AskUserQuestion` 工具本身）：
- `questions`: 1-4 项
- 每个 question 的 `options`: 2-4 项
- `question`: 完整问句，结尾建议带 `?`
- `header`: ≤12 字符 chip 短标签
- `description`: 选项的一句话说明（描述含义 / trade-off）
- `preview`: 可选；为有具体 artifact 对比的场景（mockup / 代码片段 / 配置）添加 markdown 渲染预览。**不要**为简单问题写 preview。

公共禁区：
- ❌ 在 selector 文本之外添加 "请回复编号"、"请输入选项名称"、`AWAITING_USER_CHOICE` 等措辞。
- ❌ 手工加 `Type something` / `Chat about this` / `Submit` 等保留位选项——`AskUserQuestion` 工具自动提供 "Other" + ESC。
- ❌ 选项数超过 4 → 收敛 / 重新拆问题；不要硬塞。
- ❌ `multiSelect=true` 配 `preview`（工具仅支持单选时 preview）。

---

## 类型变体 A+：单选 + 预览（side-by-side 布局）

类型 A 的视觉增强变体——为每个 option 额外传 `preview` 字段后，宿主 UI 自动切到**左右分栏**布局：左侧垂直选项列表，右侧 monospace 渲染当前焦点选项的 preview 内容（markdown，支持多行）。用户在选项间上下移动时，右侧 preview 实时切换，方便**逐项对比具体 artifact**。

**何时考虑**：

- 让用户在多份 UI mockup / 代码片段 / 配置 / 示意图之间挑一份。
- 文字描述不够直观、需要"看东西做选择"。

**何时不要用**：

- 简单偏好题（label + description 已经说清楚）——徒增视觉负担。
- 多选场景（`multiSelect=true`）—— 工具仅在单选时支持 preview。
- 候选不存在可视差异（仅取舍而无具象差别）。

**调用形态**：

```text
questions:
  - question: "<具体问题>?"
    header: "<≤12 字>"
    multiSelect: false
    options:
      - label: "<选项 A 名>"
        description: "<选项含义/trade-off>"
        preview: |
          <多行 markdown / ASCII mock / 代码片段 / 配置示例>
      - label: "<选项 B 名>"
        description: "..."
        preview: |
          <对应的另一份 artifact>
```

**当前状态**：8 个固定场景**均未启用** A+ 形态；本节是模板留档，待将来出现"让用户视觉对比 artifact"的 phase-gate 时按本骨架填空即可。如果新增固定场景使用 A+，应同步在 `spec_session.py SELECTOR_PROMPTS` 内加常量并补到下方 8 场景表里。

---

## 8 个固定场景常量库

下列 8 个固定场景的提示词与 `spec_session.py` 的 `SELECTOR_PROMPTS` 字典内容**逐字一致**——hook 命中 `pending_selector` 时把对应模板替换占位后注入到 `additionalContext`，模型读到后直接调 `AskUserQuestion` 工具。统一采用三段式 YAML 缩进格式（**目的** / **上下文** / **前置动作** / 工具参数 / **约束**）。

---

### A1 `workflow-choice` — 工作流选择（类型 A 单列单选）

```text
## 选择器节点：工作流选择

**目的**：用户刚运行 /specode:spec <需求>，已进入 intake 阶段。
在写 requirements.md / bugfix.md / design.md 之前，先决定走哪条 spec 工作流。

**上下文**：active spec=<slug>，phase=<phase>。

**前置动作（chat 简报，≤2 行）**：写一句"接到需求《<source_text_head>...》，请选择工作流。"

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "工作流选择 —— 决定走哪条 spec 流程？"
    header: "工作流选择"
    multiSelect: false
    options:
      - label: "Requirements first"
        description: "行为优先的新特性：先把 SHALL 写清楚，再补技术设计。"
      - label: "Technical Design first"
        description: "架构约束已知的新特性：先把 design.md 框架定下来，再反推 requirements。"
      - label: "Bugfix"
        description: "缺陷修复 / 回归测试：用 bugfix.md（Current/Expected/Unchanged）替代 requirements.md。"

**约束**：
- 调用工具后立即 end turn。
- 不要在 chat 输出 markdown 列表 / 不要让用户回复编号。
- 工具自动提供 "Other" + ESC，**禁止**自己加 "Type something" / "Chat about this" 保留位。
```

---

### A2 `clarification-done` — 需求澄清是否完成（类型 A）

```text
## 选择器节点：需求澄清是否完成？

**目的**：上一轮 wizard 用户已回答；判断是否进入 requirements.md / bugfix.md 生成，
还是再发一轮 wizard 继续澄清。

**上下文**：active spec=<slug>，phase=intake。

**前置动作（chat 简报，≤2 行）**：写一句"已记录用户的 N 个澄清回答，请确认下一步。"

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "需求澄清是否完成？"
    header: "澄清完成?"
    multiSelect: false
    options:
      - label: "进入下一阶段（推荐）"
        description: "用户回答已覆盖所有阻塞项，可开始写 requirements.md / bugfix.md。"
      - label: "继续澄清"
        description: "还有未解决的歧义，再发一轮 wizard。"

**约束**：
- 调用工具后立即 end turn。
- 不要复述选项 / 不要让用户回复编号。
```

---

### A3 `doc-confirm-{requirements,bugfix,design,tasks}` — 文档确认（类型 A，4 个变体共享结构）

`<filename>` / `<phase>` / 简报要点根据变体替换，骨架完全一致。下面以 `doc-confirm-requirements` 为例：

```text
## 选择器节点：requirements.md 文档确认

**目的**：requirements.md 已生成 / 更新；让用户确认是否进入 design phase，
或者先看全文 / 继续修改。

**上下文**：active spec=<slug>，phase=<phase>。
刚生成的文档：<spec_dir>/requirements.md

**前置动作（chat 简报，≤8 行）**：列出 3-8 条**关键变更要点**（文件路径 + 章节增量 + 未决问题）。
绝对不要 reprint 文档全文。

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "requirements.md 已生成。下一步？"
    header: "需求确认"
    multiSelect: false
    options:
      - label: "确认（推荐）"
        description: "文档内容符合预期，进入下一 phase。"
      - label: "查看全文"
        description: "在 chat 完整 echo 该文档（不进入下一 phase）。"
      - label: "继续沟通"
        description: "文档需要修改，告诉你具体怎么改。"

**约束**：
- 调用工具后立即 end turn。
- 简报必须在工具调用**之前**输出。
```

其余 3 个变体差异：

| key | question | header | 简报要点重心 |
|---|---|---|---|
| `doc-confirm-bugfix` | "bugfix.md 已生成。下一步？" | 缺陷确认 | Current/Expected/Unchanged 段落增量 + 复现步骤 + 影响范围 |
| `doc-confirm-design` | "design.md 已生成。下一步？" | 设计确认 | 架构图变化 + 接口签名 + 数据模型字段 + 风险 / 偏离 |
| `doc-confirm-tasks` | "tasks.md 已生成。下一步？" | 任务确认 | 任务计数（required / optional）+ 主要阶段 + traceability + 同文件冲突标注 |

---

### A4 `tasks-execution` — 任务执行选择（类型 A）

```text
## 选择器节点：任务执行选择

**目的**：tasks.md 已确认；选择执行模式（线性 / 含 optional / task-swarm 并发 / 暂不开始）。

**上下文**：active spec=<slug>，phase=tasks。
required 任务数：<n_required>，optional 任务数：<n_optional>。

**前置动作（chat 简报，≤2 行）**：写一句"tasks.md 已确认（required <N> / optional <M>），请选择执行方式。"

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "tasks.md 已确认，怎么执行？"
    header: "执行方式"
    multiSelect: false
    options:
      - label: "开始 required"
        description: "仅执行 required 任务，逐个推进 [ ] → [~] → [x]。"
      - label: "开始 required + optional"
        description: "required 完成后顺带处理 optional 任务。"
      - label: "用 task-swarm 多 agent 并发"
        description: "委派给 task-swarm 编排器；多 coder 并发 + reviewer + validator 自动 fix loop。"
      - label: "暂不 coding"
        description: "文档已落地但暂不开始实现；随时 /specode:end 关闭会话。"

**约束**：
- 4 个选项已占满工具上限；用户若想自定义可用 "Other" 输入。
- 调用工具后立即 end turn。
```

---

### A5 `takeover-options` — 接管选项（类型 A）

```text
## 选择器节点：接管选项

**目的**：/specode:continue <slug> 命中 LockHeld；让用户选择强制接管 / 只读查看 / 取消。

**上下文**：active spec=<slug>，phase=<phase>。
锁持有者: <other_id_short>（前 8 位），最近 heartbeat: <last_heartbeat>。

**前置动作（chat 简报，≤2 行）**：写一句"spec '<slug>' 已被 <other_id_short> 在 <last_heartbeat> 持有，请选择处理方式。"

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "该 spec 已被其他会话窗口持有，怎么处理？"
    header: "接管选项"
    multiSelect: false
    options:
      - label: "强制接管"
        description: "驱逐对方锁，本会话成为新锁主；对方下一次写操作会被 verify-lock 拒绝。"
      - label: "只读查看"
        description: "不持锁，加载文档进入只读模式；所有 Edit/Write 在 SKILL.md 层面被劝阻。"
      - label: "取消"
        description: "不接管，关闭本次 /specode:continue。"

**约束**：
- **不给"（推荐）"标记**——让用户根据对方是否仍活跃自己判断。
- 调用工具后立即 end turn。
```

---

### A6 `acceptance-gate` — 验收门（类型 A）

```text
## 选择器节点：验收门

**目的**：acceptance phase；acceptance-checklist.md 已填写，判断是否通过验收进入 iteration，
或者回到 requirements / design / tasks 继续修改。

**上下文**：active spec=<slug>，phase=acceptance。
已通过：<n_pass>，未通过 / 待复核：<n_fail>。

**前置动作（chat 简报，≤3 行）**：
- 列出已通过 / 未通过 / 待复核计数。
- n_fail > 0 时简列未通过项（≤3 条）。

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "验收结论？"
    header: "验收门"
    multiSelect: false
    options:
      - label: "验收通过，进入 iteration（推荐）"
        description: "所有 SHALL 已满足；如有后续调整走 iteration 子循环。"
      - label: "继续修改"
        description: "仍有未达标项，回到 requirements / design / tasks 调整。"

**约束**：
- n_fail = 0 时推荐选 1；n_fail > 0 时**移除"（推荐）"标记**。
- 调用工具后立即 end turn。
```

---

### B1 `clarification-wizard` — 需求澄清问答（类型 B / wizard）

```text
## 选择器节点：需求澄清问答（wizard）

**目的**：需求有歧义，必须在写 requirements.md / bugfix.md 之前**一次性**收齐
影响 scope / behavior / UX / data / validation / acceptance 的 2-4 个阻塞性澄清点。

**上下文**：active spec=<slug>，phase=intake。
源需求摘要：<source_text_head>

**前置动作（chat 简报，≤3 行）**：写一句"为避免 invent 业务规则，需要先确认 N 个关键点，请逐一回答。"

**调用 `AskUserQuestion` 工具一次**，`questions` 数组传 **2-4 个 question 对象**
（每个 question 都是独立的 chip-tab，每个 multiSelect=false）。子问题与选项**由你结合源需求摘要 + 用户最近输入 + assets/templates 章节结构自行生成**——不要凭空 invent 业务规则。

参数格式示例（替换为你针对当前需求生成的具体子问题）：

questions:
  - question: "<具体决策点 1 标题，必须是'是/否/选哪条'问题>"
    header: "<≤12 字 chip 标签>"
    multiSelect: false
    options:
      - label: "<选项 A>"
        description: "<一句话解释 + trade-off>"
      - label: "<选项 B>"
        description: "<一句话解释 + trade-off>"
  - question: "<具体决策点 2>"
    header: "<chip 标签>"
    multiSelect: false
    options:
      - label: "<选项 A>"
        description: "..."
      - label: "<选项 B>"
        description: "..."
  # 最多 4 个 question

**约束**：
- 每个子问题必须是"是/否/选哪条"具体问题；禁止开放式叙述（"你怎么想"）。
- 子问题之间**无依赖**——若有依赖应拆成两次 wizard。
- 决策点 ≥ 5 个 → 只保留最阻塞的 4 个，其余记入 requirements.md "待确认问题" 节。
- inputs 不足以构成阻塞决策点 → **不调本工具**，直接进 `clarification-done`。
- 工具自动提供 "Other"，**不要**手工加 "Type something" / "Chat about this" 保留位。
- 调用工具后立即 end turn。
```

具体示例（登录页 spec 的 3 个澄清点）：

```text
questions:
  - question: "登录失败时是否要显示具体原因？"
    header: "失败提示"
    multiSelect: false
    options:
      - label: "区分密码错 / 账号锁"
        description: "对用户更友好；但便于撞库探测，安全性较低。"
      - label: "统一显示"凭据错误""
        description: "避开账号枚举攻击；用户体验略差。"
  - question: "是否需要"忘记密码"入口？"
    header: "找回密码"
    multiSelect: false
    options:
      - label: "需要（邮件链接）"
        description: "标准流程；与现有邮件服务集成。"
      - label: "需要（短信验证）"
        description: "需要短信通道；适用于移动 first。"
      - label: "不需要（管理员重置）"
        description: "内部系统常见；用户找管理员。"
  - question: "session 有效期？"
    header: "会话时长"
    multiSelect: false
    options:
      - label: "8 小时（工作时段）"
        description: "适合纯桌面办公场景；超时强制重登。"
      - label: "30 天（带'记住我'）"
        description: "提供 Remember-me checkbox；token 安全级别需要提高。"
```

---

### C1 `iteration-scope` — 本轮 iteration 调整范围（类型 C / 多选）

```text
## 选择器节点：iteration 调整范围（多选）

**目的**：用户从 acceptance-gate 选了"验收通过"或显式提出迭代调整；确定本轮 iteration 调整哪些文档/动作。

**上下文**：active spec=<slug>，phase=iteration。

**前置动作（chat 简报，≤2 行）**：写一句"进入 iteration 子循环，请选择本轮调整范围（可多选）。"

**调用 `AskUserQuestion` 工具**，注意 **multiSelect=true**：

questions:
  - question: "本轮 iteration 要调整哪些文档/动作？（可多选）"
    header: "迭代范围"
    multiSelect: true
    options:
      - label: "改 requirements"
        description: "新增 / 修改 EARS SHALL 条款。"
      - label: "改 design"
        description: "架构 / 接口 / 数据模型调整。"
      - label: "改 tasks"
        description: "新增任务或调整已有任务范围。"
      - label: "重跑测试"
        description: "不改文档，重新验证当前实现。"

**约束**：
- multiSelect=true（**唯一**使用类型 C 复选框的场景）。
- 允许用户全不选（视为本轮 iteration 取消）；ESC 等价。
- 调用工具后立即 end turn。
```

---

## hook 注入与模板替换

`UserPromptSubmit` 的 `on-user-prompt` hook 在 `sessions/<id>.json.pending_selector` 命中某 key 时，把 `SELECTOR_PROMPTS[key]` 拿出来做字符串替换（`<slug>` / `<phase>` / `<spec_dir>` / `<source_text_head>` 等），包入 `additionalContext` 注入。

实际注入文本采用三段式 YAML 缩进格式（与本文件 §A1-§C1 模板**逐字一致**）：

模型看到注入后**唯一动作**：

1. 在 chat 写 hook 提示中"前置动作"要求的简报内容（doc-confirm 类型必须含 3-8 条变更要点；接管类型必须含锁持有者信息）。
2. 调 `AskUserQuestion` 工具，参数按 hook 给的 YAML 块**逐字**翻译为工具参数（不要翻译选项 label / description）。
3. 工具返回后按用户选择推进下一步（调对应 CLI 子命令）。

绝不允许：
- ❌ 把模板里的 questions / options 翻译 / 重写 / 加减项。
- ❌ 工具调用之外另开 chat 输出"也可以告诉我..."。
- ❌ 跳过工具直接做下一步（即使你"觉得"用户的意图明确）。

---

## 自主判断（hook 失败时）

按上面 8 个场景对照表选 key → 用对应模板直接调 `AskUserQuestion`。hook 是**提醒**而非**触发**——hook 失效时仍要按本文规范走。

---

## 历史措辞兼容

如果你在 SKILL.md / commands / agents 任意位置看到下列**过时词汇**，视为遗留文档，按本文规范覆盖：

- `AWAITING_USER_CHOICE` sentinel
- "请回复选项编号或选项名称"
- "Type something" / "Chat about this" 保留位
- "请按 §3.7.X 类型骨架输出"
- `spec_choice.py` 命令样例

这些词汇在运行时永远不应再出现；它们的目标都由 `AskUserQuestion` 工具替代。
