# Spec Mode Workflow

Operational reference for the rules defined in `SKILL.md`. Activation conditions, the hard rules, and command compliance live in SKILL.md and are **not** restated here.

## 0. Activation Guard

Activation rules are defined in `SKILL.md §Activation Guard` and apply here without exception. Do not re-state or paraphrase them. If the current request does not satisfy SKILL.md activation conditions, do not create a spec directory and do not run the phase-gated workflow.

## 1. `/spec` Intake

Parse user input as:

```text
/spec <requirement-or-path> [extra instructions]
/spec-mode <requirement-or-path> [extra instructions]
/spec --persist <requirement-or-path>
/spec-continue [spec-slug]
/spec-status
/spec-end
```

Intake rules:

- If `<requirement-or-path>` points to a readable file, summarize it and use it as the source.
- If it is prose, use it directly.
- **Spec name prefix parsing**: detect a leading `<名称>：<内容>` (full-width 冒号) or `<名称>: <内容>` (ASCII 冒号 + 空格) within the first ~30 chars; **不**对路径 / URL / 无冒号输入做拆分。匹配则：`<名称>` 作为 slug 来源（非英文需 agent 自行派生语义 slug，原文保留为 `requirementName`），`<内容>` 作为需求源文本。否则整段都是需求，agent 自行从内容推断 slug。
- Extract requirement name (semantic English slug — see §1.2), root hints, workflow hints, constraints, validation expectations.
- If the user only gives a root and no requirement, ask for the requirement.
- **Pre-requirements clarification (Plan-mode)**: if the requirement has real ambiguity affecting scope, behavior, UX, data, validation, or acceptance, stay in `intake` phase and enter a clarification dialogue **before** writing any document. Do not invent missing details. Group questions compactly (≤5), end the turn, wait for user reply. After resolution, proceed to workflow selection. → 详见 SKILL.md §Pre-requirements Clarification。
- Group unclear points into a compact confirmation list before generating the next document.

Persistent command rules:

- `/spec <requirement>` — one-shot. Runs the workflow without updating `.active-spec-mode.json`.
- `/spec --persist <requirement>` — persistent. Initializes spec and starts an active session.
- `/spec-continue [slug]` — resume; multi-window aware (see §9).
- `/spec-status` — prints current session, spec path, phase, task counts, lock state.
- `/spec-end` — ends current session, releases the spec lock, **does not** delete docs.

## 1.1 Natural-language Follow-up Routing

Within an active persistent session, route natural-language follow-ups via document-first discipline (the iron rules live in SKILL.md §Document-first Discipline).

> ⛔ **Post-`/spec-continue` 同 turn 同步（非常重要）**：恢复一个已落地 spec 后，用户在聊天中提出的任何对需求或设计的调整——哪怕只是一句澄清——都必须**在同一轮 turn 内**写回 `requirements.md` / `bugfix.md` / `design.md` / `tasks.md`（需求变更并同 turn 重写 `acceptance-checklist.md`）。不允许累积到"下一轮"，不允许"先写代码后补文档"。


| Intent | Action |
|---|---|
| Requirement change | Update `requirements.md` / `bugfix.md`, **same turn rewrite `acceptance-checklist.md`**, then check whether `design.md` and `tasks.md` are stale |
| Design change | Update `design.md`, then check whether `tasks.md` is stale |
| Task change | Update `tasks.md`, preserve `_需求：..._` traceability |
| Execution request | Verify lock → load only active spec's docs → execute selected or next pending task |
| Acceptance feedback | Update task/review state in `tasks.md` and `acceptance-checklist.md` |
| User said "/spec-accept" or "验收通过" | Run `spec_session.py iterate <spec-dir>` → phase becomes `iteration` |

## 1.2 Spec Slug Generation (Agent Responsibility)

`scripts/spec_init.py` **requires `--name <slug>`**. The script does not infer slugs from Chinese — that responsibility falls on the agent:

1. Read the user's requirement description
2. Produce a short semantic English slug, lowercase, hyphen-separated, ≤64 chars (e.g. `login-password-rule`, `undo-redo`, `dark-mode`)
3. Call `spec_init.py --name <slug> --requirement-name "<中文显示名>" --source-text "..."`

If `--name` is missing or normalizes to empty, `spec_init.py` exits with `invalid_name`.

## 2. Workflow Choice Prompt

When workflow is unclear, present the **Workflow 类型选择** selector from `references/prompts.md`.

| Option | When |
|---|---|
| Requirements | Behavior-first feature work — **recommended default** |
| Technical Design | Architecture / low-level design / non-functional constraints are primary |
| Bugfix | Defect / regression / failing test / incident |

## 2.1 Document Confirmation Prompt

After every generated document, in the **same response**:

1. Do not paste the full document by default — rely on the client's file diff preview.
2. Show file path, concise summary, key changes, unresolved questions.
3. Run the **文档确认** selector from `references/prompts.md`.
4. **End the turn.**

Rules:

- `确认` → next phase
- `查看全文` → print full document, then re-show selector
- `继续沟通` → apply feedback, re-show summary + selector
- Repeat until `确认`

After `tasks.md` is confirmed, run the **任务执行** selector from `references/prompts.md`.

## 3. Directory Resolution

Spec layout:

```text
<document-root>/
├── .active-spec-mode.json       ← v2 window index, slug-only
└── <spec-slug>/
    ├── requirements.md or bugfix.md
    ├── design.md
    ├── tasks.md
    ├── acceptance-checklist.md
    └── .config.json              ← per-spec lock + iteration state
```

Resolution priority (handled by `spec_init.py:resolve_document_root` / `spec_vault.resolve_spec_root`):

1. `--root` or `SPEC_MODE_ROOT`
2. `~/.config/spec-mode/config.json` → `obsidianRoot`
3. Auto-detect Obsidian vault → `<vault>/spec-in/<os>-<user>/specs`

**No further fallback.** All three miss → hard stop with guidance (see SKILL.md §Document Root Resolution).

## 4. Requirements-first Flow

1. Generate `requirements.md` with sections: 简介 / 词汇表 / 需求 / 用户故事 / EARS 验收标准 / 边界情况 / 非功能需求 / 待确认问题
2. **Same turn**: rewrite `acceptance-checklist.md` based on every SHALL in `requirements.md` (see §4.1)
3. Stop for review; show path, summary, key changes, unresolved questions
4. After confirm → generate `design.md` → review → confirm
5. → generate `tasks.md` → review → confirm
6. → ask whether to execute tasks
7. Code → validate → accept

## 4.1 acceptance-checklist 跟随式生成（铁律）

`acceptance-checklist.md` 没有独立确认门。它跟随 `requirements.md` / `bugfix.md` 的变更，由 agent 在**同一轮 turn 内**重写。

**填充规则：**

- 读取 requirements.md / bugfix.md 中每一条 EARS `SHALL` 语句
- 每条 SHALL → checklist 一行：
  - **功能点** = 该 SHALL 所属的需求名 / 编号
  - **操作步骤** = 测试人员可执行的具体动作（**禁止**"触发该能力"这种泛化描述）
  - **预期结果** = 直接引用 SHALL 后的期望行为
  - **实际结果** = `待记录`
  - **结论** = `待验证`
- **禁止保留**模板里"核心能力 / 异常输入 / 回归行为 / _agent 待填充_"等占位行
- 验证命令行可保留（自动从 tasks.md "验证：xxx" 提取）

**例**：需求"新增密码强度校验" → 一行：
`输入少于 8 位密码点击提交 → 预期提示"密码长度不足"`

**未跟上的检测**：`spec_lint.py` 会在 `acceptance-checklist.mtime < requirements.mtime` 时报 WARNING；`spec_session.py load` 会在加载时显示 `⚠ 落后于 requirements.md`。

## 5. Technical-design-first Flow

1. `design.md` first; choose level (high / low)
2. Stop → confirm
3. Derive `requirements.md` from approved design → **same turn rewrite checklist**
4. `tasks.md`
5. Display + confirm each
6. Ask whether to execute

## 6. Bugfix Flow

1. `bugfix.md` with: Current / Expected / Unchanged / Reproduction / Evidence / Impact
2. **Same turn rewrite `acceptance-checklist.md`** from SHALL statements
3. Investigate code before claiming root cause
4. `design.md` with root cause / fix strategy / regression risks / testing strategy
5. `tasks.md` with: reproduction test first → minimal fix → unchanged-behavior regression tests → final validation
6. Display + confirm each

## 7. Task Execution

Before editing code:

1. Resolve active spec from command or active session
2. **Three-check write guard** (see SKILL.md §Multi-Window + Lock): specId, boundary, lock
3. Load every file in that spec directory only
4. Find target task or next pending required task
5. **Heartbeat**: `python3 scripts/spec_session.py heartbeat <spec-dir>`
6. Update task marker `[~]`
7. Implement only the linked scope
8. Run validation
9. Mark `[x]` only when validation passes
10. If blocked, leave `[ ]` / `[~]` and note the blocker

Task markers:

```
[ ] pending      [~] in progress    [x] completed
[-] skipped      [*] optional
```

## 8. Acceptance

Final acceptance must include:

- Documents created or updated
- Tasks completed
- Validation commands and results
- Any skipped validation
- `acceptance-checklist.md` tester-operable steps and recorded results
- Remaining risks or open questions
- If persistent: footer with `/spec-end`

When all required checklist rows have `结论 = 通过`, agent runs the **验收通过** selector from `references/prompts.md`:

- `验收通过` → run `python3 scripts/spec_session.py iterate <spec-dir>` → `iterationRound` 自增、phase 变为 `iteration`
- `继续修改` → 留在 `acceptance` 阶段调整 checklist 或回滚到 `implementation`

## 9. `/spec-continue` — Context Loading + Multi-Window

`/spec-continue` is a load-and-report command. It restores context and stops; it does not start implementation, run validation, or evaluate acceptance.

### 9.1 No-arg form

```text
/spec-continue
```

Steps:

1. Resolve configured root: `python3 scripts/spec_vault.py get --json --configured-only`
   - If no configured root → ask user to run `/spec --set-vault` or `/spec --set-root` and stop
2. List specs: `python3 scripts/spec_session.py list-specs --root <root> --json`
3. List sessions: `python3 scripts/spec_session.py list --root <root> --json`
4. Present using **Template C — List + Numeric Selection** in `references/prompts.md` (三段固定：当前会话 / 其他窗口 / 可继续的全部 specs；锁状态用固定词；结束语固定)
5. After user picks → run 9.2 with that slug

### 9.2 With slug

```text
/spec-continue <slug>
```

Steps:

1. Resolve `spec_dir = <root>/<slug>`
2. `python3 scripts/spec_session.py acquire <spec-dir> --session <id>`
   - **Exit 0** → owned, proceed to step 3
   - **Exit 4 (LockHeld)** → 输出锁状态摘要，运行 **`/spec-continue` 接管** 选择器（见 `references/prompts.md`）
     - `强制接管` → `acquire --force`, warn that previous session was evicted
     - `只读查看` → skip acquire, set read-only flag; do **not** update active-pointer's specSlug binding
     - `取消` → exit
3. `python3 scripts/spec_session.py load <spec-dir> --session <id>` — capture output
4. `python3 scripts/spec_session.py continue <spec-dir> --session <id>` — bind session, write active pointer (skipped in read-only)
5. Present loaded context:

```
已加载 spec: <slug>
  specId:   <id>
  phase:    <phase>
  iteration: 第 N 轮（若 > 0）
  session:  <sessionId> (<status>)
  lock:     本会话持有 | ⚠ 锁定于 <id> | 空闲

  requirements.md           ← N 条验收标准  |  修改: <time>
  design.md                 ←               |  修改: <time>
  tasks.md                  ← N/M 已完成, P 待处理  |  修改: <time>
  acceptance-checklist.md   ← 验收操作清单  |  修改: <time>
```

6. Output footer (if persistent / read-only)
7. **Stop and wait for user's next input.** Do not start tasks.

> ⛔ 从这一刻起，本会话进入"已落地 spec 的持续沟通"模式。后续任何对需求或设计的调整 **必须同 turn 写回对应文档**——见 §1.1 顶部铁律。聊天里说过但没写入文件的内容，下次 `/spec-continue` 时全部丢失。

## 10. Boundary Anti-contamination Rules

Enforced for every continue, switch, edit, end, and any spec document write:

1. `specDir` must be inside `documentRoot` (`ensure_within_root`); refuse if not
2. Active pointer `specId` must match `<spec-dir>/.config.json.specId`; refuse if not
3. **Lock must be held by current session** (`verify-lock` returns `ok`); refuse if not
4. Only files inside the selected spec folder are treated as active spec documents
5. Changes to one spec never update another spec's documents, config, task state, or active pointer entry
6. All writes use atomic temp + `os.replace()`; read-modify-write of `.config.json` is guarded by `_file_lock`

→ 详见 `lock-protocol.md`（5 个 lock 子命令、接管协议、只读模式、被驱逐窗口行为）

## 11. iteration Phase

→ 详见 `iteration.md`（完整 phase 生命周期、子循环图、文档累积写法、`/spec-accept` 触发约定、`spec_session.py continue --phase` 默认 None 的原因）

## Phase Gates — Detailed Sub-steps

Output order within each confirmation step (strictly follow):

1. Generate or update the document (write the file)
2. **First**: in agent's text — document path, concise summary, key changed points, unresolved questions
3. **Then**: confirmation options via `spec_choice.py`. TTY → user picks in curses. Non-TTY → the script prints the option block + `AWAITING_USER_CHOICE` sentinel on stdout and exits 0; relay the stdout block to the user verbatim. Do **not** re-run the script to "retry" or restate the options yourself in different wording.
4. **End the turn.** Do not continue to the next phase in the same response

The user's next reply drives the next action:

- `"确认" / "1" / "confirm"` → proceed to next phase
- `"查看全文" / "2"` → display full document, then show confirmation options again; end turn
- `"继续沟通" / "3" / any feedback` → update document, show revised summary + options; end turn

Full phase sequence:

1. Generate or update `requirements.md` (feature) or `bugfix.md` (bugfix). Show summary + options. End turn. Wait for confirm.
2. **Same turn as #1**: rewrite `acceptance-checklist.md` from SHALL statements (跟随式，无单独确认门).
3. After confirm: generate or update `design.md`. Show summary + options. End turn. Wait for confirm.
4. After confirm: generate or update `tasks.md`. Show summary + options. End turn. Wait for confirm.
5. After confirm: show task execution options (required only / required + optional / hold). End turn. Wait for choice.
6. After explicit execution choice: begin coding tasks, validate, accept.

If user asks for one-pass generation, still show paths, summaries, key changes per document, and mark `Review Status: unreviewed`.

## Implementation Execution — Full Steps

1. Resolve and validate the active spec session if persistent mode is active
2. **Three-check write guard** + heartbeat (see §7)
3. Load all spec files from the selected `<document-root>/<requirement-name>/`
4. Identify the selected task ID or next pending required task
5. Mark the task in `tasks.md` as in-progress using `[~]`
6. Make the smallest code change that satisfies the linked requirement
7. Run the validation command or nearest relevant project test
8. Mark `[x]` only after validation passes
9. If validation cannot run, keep the task incomplete and record the reason
10. Finish with an acceptance summary: changed files, completed tasks, validation result, remaining risks

**Task menu semantics:**

- "Run all tasks" = required tasks only unless the user opts in to optional tasks
- "Run required and optional tasks" = includes optional
- Stop at checkpoints if validation fails or user confirmation is needed

## Interactive Selectors (Reference)

Run at each decision point. In a TTY the script offers ↑/↓ + Enter. In a non-TTY shell (Claude Code Bash, CI) it prints the option block + `[spec-mode:non-interactive] AWAITING_USER_CHOICE` sentinel on stdout and exits 0; agent forwards the stdout block to the user and ends the turn. Do not invent your own option text — always run the script first.

All selector command blocks live in `references/prompts.md` — copy-paste them verbatim:

- Workflow 类型选择
- 文档确认（每份 spec 文档生成后）
- 任务执行（tasks.md 确认后）
- `/spec-continue` 接管（spec 已被锁定时）
- 澄清完成（Plan-mode 结束）
- 验收通过（acceptance 完成时）

Selectors are preferred over plain-text confirmation. Use plain text only when tool execution is unavailable. **Forbidden phrasings** (`够了`、`差不多`、`随便选` 等口语词) are listed at the bottom of `references/prompts.md`.
