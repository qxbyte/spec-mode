# Prompt Output Templates

Unified format for all clarification / confirmation / selection outputs in spec-mode. Every interaction point must conform to one of the templates below.

## Selector Preference (Iron Rule)

For any fixed-option decision (≤5 options), use `scripts/spec_choice.py`.

- **TTY**: curses ↑/↓ + Enter; script writes the chosen label to stdout, exits 0.
- **Non-TTY (Claude Code Bash, CI)**: script prints the option block + `[spec-mode:non-interactive] AWAITING_USER_CHOICE` sentinel on stdout, exits 0. Agent must forward the stdout block to the user verbatim and end the turn. **Do not** re-run the script in the same turn to "retry" or restate the options in your own words.

Never ask "请回复确认/继续/取消" as plain text without running the script first — the script is the source of truth for option wording.

## Template A — Fixed-option Confirmation

Use for: workflow choice, document confirmation, task execution choice, `/spec-continue` takeover, clarification completion.

```text
python3 scripts/spec_choice.py --title "<标题以问号结尾？>" \
  --option "<选项 1>::<一行说明>::recommended" \
  --option "<选项 2>::<一行说明>" \
  --option "<选项 3>::<一行说明>"
```

Rules:

- 标题以问号结尾，简洁明确
- 选项标签 ≤8 字，用中文动词式（如 `确认`、`查看全文`、`继续沟通`、`强制接管`、`只读查看`、`取消`、`进入下一阶段`）
- 仅一个选项标 `recommended`
- 每个选项必须有一行说明

## Template B — Open-ended Clarification (Plan-mode)

Use for: pre-requirements clarification, mid-workflow ambiguity that needs free-form answers.

```text
=== 需求澄清 ===
当前阶段：intake
源需求摘要：<一句话不超过 60 字>

待确认问题：

【阻塞】1. <问题描述>
【阻塞】2. <问题描述>
【可延后】3. <问题描述>
            （未回答 → 写入 requirements.md 的"待确认问题"节）

请按编号回答阻塞项。回答完成后将出现「澄清完成」选择器。
```

Rules:

- 标头三行固定：`=== 需求澄清 ===` / `当前阶段：<phase>` / `源需求摘要：<≤60 字>`
- 每条问题前必须标 `【阻塞】` 或 `【可延后】`
- 阻塞项 ≤5 条；超过则分轮提问，不要一次堆十几个
- 可延后项必须注明"未回答 → 待确认问题节"
- 结束语固定：`请按编号回答阻塞项。回答完成后将出现「澄清完成」选择器。`
- agent 收到回答后 **end the turn**，下一回合先解析回答、再调澄清完成选择器（Template A）

## Template C — List + Numeric Selection

Use for: `/spec-continue` 无参数时列出可继续 spec、多 vault 选择。

```text
=== <列表标题> ===
配置根目录：<path>

当前会话 (session: <id>)
  ► <slug>     <name>       <phase>    <m/n 任务>    <lock state>

其他窗口
    <slug>     <name>       <phase>    <m/n 任务>    <lock state>

可继续的全部 specs：
  1. <slug>     <name>       <phase>    <m/n 任务>    <lock state>
  2. ...

请输入编号 [1-N]，或输入 spec slug 名。
```

Rules:

- 三段固定（当前会话 / 其他窗口 / 全部 specs）；空段也保留标题
- 每行列宽对齐（spec_session.py list-specs 输出已支持）
- 锁状态用固定词：`✓持有锁` / `⚠ 锁定于 <id>` / `○ 空闲` / `（已过期）`
- 结束语固定：`请输入编号 [1-N]，或输入 spec slug 名。`

## Template D — Read-only Status Banner

Use for: 进入只读模式后每次响应前的提醒。

```text
[只读模式] 当前 session 未持有 spec 锁，所有写入操作将被拒绝。
请用 /spec-continue <slug> 选择"强制接管"恢复可写。
```

固定 footer 末尾追加 `| [只读]`。

## Template E — Eviction Notice

Use for: 被驱逐的会话下一次响应时。

```text
⚠ 你的会话已被 session <newSessionId> 强制接管。
当前 spec 在此窗口已转为只读，请用 /spec-continue 重新接管。
```

## 标准选择器命令（直接复制使用）

### Workflow 类型选择

```text
python3 scripts/spec_choice.py --title "选择 spec 工作流类型？" \
  --option "Requirements::按需求驱动，先写需求再设计::recommended" \
  --option "Technical Design::先做技术设计，再回推需求" \
  --option "Bugfix::记录当前/期望/不变行为"
```

### 文档确认

```text
python3 scripts/spec_choice.py --title "确认 <filename>？" \
  --option "确认::继续生成下一阶段文档::recommended" \
  --option "查看全文::在聊天中展示完整文档" \
  --option "继续沟通::先根据反馈修改当前文档"
```

### 任务执行

```text
python3 scripts/spec_choice.py --title "是否开始执行 tasks？" \
  --option "开始 required tasks::只执行必需任务::recommended" \
  --option "开始 required + optional tasks::执行必需任务和可选任务" \
  --option "暂不 coding::只保留文档，不开始实现"
```

### `/spec-continue` 接管（spec 已被其他 session 锁定）

```text
python3 scripts/spec_choice.py --title "spec 已被 session <holder> 锁定，如何继续？" \
  --option "强制接管::驱逐另一窗口，本窗口接管写权::recommended" \
  --option "只读查看::加载文档但不写入，footer 标记 [只读]" \
  --option "取消::返回上一步"
```

### 澄清完成（Plan-mode 结束）

```text
python3 scripts/spec_choice.py --title "需求澄清是否完成？" \
  --option "进入下一阶段::开始 workflow 选择和文档生成::recommended" \
  --option "继续澄清::还有问题需要讨论"
```

### 验收通过（进入 iteration）

```text
python3 scripts/spec_choice.py --title "本轮验收是否通过？" \
  --option "验收通过::进入 iteration 阶段，等待下一轮迭代::recommended" \
  --option "继续修改::返回 acceptance 阶段调整"
```

## Forbidden Phrasing

下列措辞**禁止**出现在 agent 面向用户的输出中：

- "够了"、"差不多"、"应该可以了"——口语化，改用选择器的正式选项
- "随便选一个"、"看你"——必须明确推荐项或问具体问题
- "我猜……"、"我假设……"——禁止猜测；走 Template B 澄清
- "稍等"、"我来想一下"——直接输出结果或结束 turn 等回复，不要中间填充语
