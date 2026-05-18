---
name: specode
description: Specification-driven workflow for requirements, technical design, task lists, implementation, acceptance, and ongoing spec iteration. Use when the user explicitly invokes /spec, explicitly says to use spec mode, or the current conversation has an active persistent specode session that has not been ended. Do not use for ordinary coding, planning, requirements, design, or documentation requests unless spec mode is explicitly requested or already active.
---

# Spec Mode

File-first specification-driven workflow for CLI agents (Codex, Claude Code). Generated Markdown documents are the source of truth; coding starts only after requirements, design, and tasks are confirmed.

## Activation Guard

This skill is opt-in only. Activate **only** when the user's current message contains one of:

- `/spec`, `/continue`, `/status`, `/end`
- `/spec -h` / `--help`
- `/spec --persist`, `/spec --freeform`, `/spec --strict`
- `/spec --set-vault`, `/spec --set-root`, `/spec --detect-vault`, `/spec --vault-status`, `/spec --sync-status`
- `使用 spec 模式` / `启用 spec 模式` / `用 spec 模式` / `use spec mode`

**Hard rules:**

1. `/spec` always activates the spec workflow — even when the requested work is to inspect or modify the `specode` skill itself.
2. **Command compliance**: when any spec command is triggered, follow the corresponding workflow exactly. Do not skip phases, phase gates, or confirmation steps for any reason. Commands are absolute; the assistant's judgment cannot override a command.
3. **Persistent session exception**: if a persistent specode session is active for the current conversation, route follow-up messages through this skill until the user runs `/end`.

Do **not** activate for ordinary coding, planning, requirements, design, task lists, bugfixes, implementation, or documentation requests. In those cases, work normally — do not create spec folders.

## ⛔ Iron Rules — Top of Mind

These rules are checked at **every turn** of every specode session. Never violate them. Never defer them. If the user pushes back, acknowledge — then comply with the rule first, discuss after.

1. ⛔ **New spec via `spec_init.py` only — no manual scaffolding.** Any new spec (one-shot `/spec` or `/spec --persist`) MUST be created by calling `spec_init.py --name <slug> --requirement-name "<显示名>" --source-text "<需求>"`. The script alone resolves the document root (three-tier, see §Document Root Resolution) and writes `.config.json`. You **MUST NOT**:
   - `mkdir` a spec directory anywhere yourself (not in cwd, not under `~/Git/<x>/`, not in any path you constructed)
   - `Write` `requirements.md` / `bugfix.md` / `design.md` / `tasks.md` / `.config.json` to a path you chose
   - Interpret phrasing like "在项目下创建"、"在 git 目录下创建一个新项目"、"放本地" as a directive to place spec docs in the project / cwd. Those phrases describe **future code location**, not spec-document location. Spec docs always live under the resolved doc_root; code lives wherever the user wants. **Never conflate the two.**

   Workflow: derive slug → call `spec_init.py` → fill content into the files it created. If `spec_init.py` exits with `no_spec_root`, **stop and surface the guidance verbatim**; do not invent a fallback location.

2. ⛔ **Document-first.** Any change to requirements / design / tasks discussed in chat MUST be written to the corresponding spec document **in the same turn**, *before* further discussion or implementation. Verbal-only changes are invisible to the next session and silently drift from the persisted spec.

3. ⛔ **Post-`/continue` sync — 非常重要.** After `/continue` you are resuming an **already-landed** spec. **Every** subsequent adjustment to requirements or design — even a single clarifying sentence from the user — MUST be reflected in `requirements.md` / `bugfix.md` / `design.md` / `tasks.md`, **in the same turn**. Do not wait for "later", do not batch into "next round", do not say "I'll update it after the code". Write **now**. The user said it → write it. The next session can only see what was persisted; chat is ephemeral.

4. ⛔ **tasks.md 测试要点 follow-mode.** `requirements.md` or `bugfix.md` modified → update the `## 测试要点` section of `tasks.md` in the **same turn**, derived from the new SHALL statements. This is INV-4 (enforced at `Stop`): touching requirements/bugfix without touching tasks.md → hook denies the turn.

5. ⛔ **Write-before-verify-lock.** Before any `Edit`/`Write` on a spec document, call `sh ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh ${CLAUDE_PLUGIN_ROOT}/scripts/spec_session.py verify-lock <spec-dir> --session <id>`. Returns `evicted` → stop work immediately and tell the user the spec was taken over by another session.

6. ⛔ **Phase gate compliance.** No skipping confirmation steps. No auto-selecting at gates. No "this seems simple, let's skip ahead". Commands are absolute; the assistant's judgment cannot override them.

7. ⛔ **Forced writes.** Every config / document mutation must be persisted on the spot. When a write fails (IOError / permission / `lock_lost`), abort the operation — never continue with in-memory unpersisted state.

8. ⛔ **Selector via `spec_choice.py` only — never hand-roll options.** Every phase-gate selector (workflow choice / 文档确认 / 任务执行 / `/continue` 接管 / 验收 / 澄清完成) MUST be produced by running the exact `spec_choice.py` command from `references/prompts.md` and relaying its stdout **verbatim**. You **MUST NOT** type the option list from memory, paraphrase it, drop options, reorder them, or translate the labels. Hand-rolling silently hides newer options the script knows about (e.g. omitting `用 task-swarm 多 agent 并发` from the 任务执行 selector and forcing the user into the default path). If you don't have the exact command in context, Read `references/prompts.md` first — never improvise.

These rules trigger detectable signals (lint, `/continue` ⚠ markers, verify-lock exit codes). Treat any of those signals as a regression on your part, not a tool quirk.

## Command Entry (Summary)

```text
/spec <requirement or path>             ← one-shot workflow
/spec --persist <requirement or path>   ← persistent session (footer + /end)
/continue [spec-slug] | /status | /end  ← session control

/spec --set-vault <p> | --set-root <p> | --detect-vault | --vault-status
/spec --freeform | --strict | --sync-status
/spec -h                                ← help (hook-intercepted)
```

→ **完整命令、子标志 dispatch、可选 spec 名前缀、会话模式、Helper Scripts、Hook 拦截**：`references/commands.md`

## Pre-requirements Clarification (Plan-mode)

Before generating `requirements.md` / `bugfix.md`: evaluate whether the user's requirement is unambiguous enough to translate into EARS SHALL statements **without invention**.

- **Clear enough** → proceed to workflow selection and document generation.
- **Real ambiguity** affecting scope / behavior / UX / data / validation / acceptance → enter clarification dialogue first. Phase stays in `intake`. **Do not write any spec document yet.**

每轮 ≤5 个【阻塞】项；用户答复后用 `references/prompts.md` §澄清完成 selector 决定 `进入下一阶段` 或 `继续澄清`。**Never** invent missing scope, business rules, UI behavior, data fields, or acceptance criteria.

→ 详见 `references/prompts.md` §Template B（开放式澄清问答）+ §澄清完成

## Document Root Resolution (Iron Law)

Three-tier resolution. **No project fallback, no home fallback.**

1. `--root` argument or `SPECODE_ROOT` env (highest)
2. `~/.config/specode/config.json` → `obsidianRoot`
3. Auto-detect Obsidian vault → `<vault>/spec-in/<os>-<user>/specs` (and persist)

All three miss → **hard stop**, output guidance, exit. `/spec` and `/continue` use the **same** resolution. **Spec documents are NEVER allowed outside the resolved doc_root**:

- ❌ `<project>/specs/`、`<project>/specode/`、`<project>/spec/`、`<project>/<任意名>/`
- ❌ `~/Git/<x>/specode/`、`~/Git/<x>/specs/`
- ❌ `<cwd>/specs/`
- ❌ Any path of your own choosing

The directory name (`specs`, `specode`, anything else) does NOT matter — the **location** (under doc_root or not) is what matters. If the user says "在项目下"、"在这个目录下"、"放本地" referring to where to create the spec, treat that as **misinterpreting code scope as document scope** and clarify: code can live in the project; spec docs must live under the configured doc_root. If no doc_root is configured, run `/spec --set-vault <p>` or `/spec --set-root <p>` first, **not** invent a fallback.

→ 详见 `references/obsidian.md`

## Multi-Window + Lock (Iron Law)

Different agent windows may work on **different** specs in parallel. The **same** spec is held by at most one session at a time via a write lock in its `.config.json`.

**Before any spec document write**, three checks:

1. **specId**: active-pointer.specId == .config.json.specId
2. **boundary**: spec_dir is inside documentRoot (`spec_session.ensure_within_root`)
3. **lock**: `spec_session.py verify-lock <spec-dir> --session <id>` returns `ok`

Any failure → refuse the write, surface the error, do not silently continue. `/continue <slug>` on a locked spec must offer three options: 强制接管 / 只读查看 / 取消. Heartbeat before every Edit/Write; stale lock = 30 min.

→ 详见 `references/lock-protocol.md`

## Phase Gates

Phase order (**no skipping**): requirements (or bugfix) → Confirm → design → Confirm → tasks → Confirm → ask whether to execute → Code → validate → accept → iteration.

At each gate, in the same response: (1) show document path, summary, key changes, unresolved questions; (2) invoke `spec_choice.py` — in non-interactive shells (Claude Code Bash, CI) the script prints the option block + `AWAITING_USER_CHOICE` sentinel on stdout and exits 0; relay stdout **verbatim** and end the turn; (3) **end the turn**.

Auto-selecting a default at a phase gate is **never** acceptable.

→ 详见 `references/workflow.md` §Phase Gates Detailed Sub-steps + `references/iteration.md`

## Document-first Discipline

Spec documents are the sole persistent memory. Any change not written to a document is invisible to the next session. See also Iron Rules #1, #2, #3, #6 at the top of this file.

**Iron rules (apply from the moment a persistent session is active, **and** apply equally — and especially — after `/continue`):**

1. **Requirement change** → update `requirements.md` / `bugfix.md` **first**, then continue
2. **Design decision** → update `design.md` **first**, then implementation
3. **Task status change** → update `tasks.md` **immediately** (`[~]` / `[x]` / blocked)
4. **New task / sub-task** → append to `tasks.md` **before** starting it
5. **requirements.md / bugfix.md modified** → must update `tasks.md` 的 `## 测试要点` 节 in the **same turn**（INV-4，Stop hook 强制；未同步则整轮被拒绝）
6. **Write-before-verify**: before any `Edit`/`Write` on a spec document, call `spec_session.py verify-lock`. EVICTED → stop work and tell the user.
7. **Post-`/continue` sync (非常重要)**: after `/continue`, the spec docs are already landed. Any further requirement/design adjustment from the user (including verbal-only "顺便改一下…") MUST be applied to the landed `requirements.md` / `design.md` / `tasks.md` **in the same turn it is raised**, before any code action. **Never** leave a chat-only change unwritten between turns — the next session will lose it. If multiple docs are affected by one change, update all of them in the same turn.

These writes are non-negotiable. If the user asks to skip writing and proceed, acknowledge — then write first, proceed second. **Writes are forced**: if a write fails (IOError/permission), abort the operation; never continue with in-memory unpersisted state.

→ 详见 `references/workflow.md` §1.1（自然语言路由表）

## Workflow Selection

Classify the request before creating documents:

- Feature, behavior-first → **Requirements-first** (recommended default)
- Feature, architecture-first → **Technical Design first**
- Bug / regression / failing test → **Bugfix**

Use `scripts/spec_choice.py` when the workflow matters and is unclear; non-interactive shells get the option block + `AWAITING_USER_CHOICE` sentinel on stdout. **Never silently choose for the user.**

## Help Output (Fast Path)

When the prompt is exactly `/spec -h` or `/spec --help` — **fast path, no thinking, no file scanning beyond the one file below**:

1. `Read` `references/help-output.md` (single file, no other context loading)
2. Extract the **first** ` ```text ... ``` ` fenced block
3. Output that block **verbatim** inside one ` ```text ` fence, then **stop**

Forbidden in this path: thinking blocks, summaries, "here is the help", reading other references, loading other files, calling any script. The output is purely a file echo.

The same fast-path applies to `/spec --vault-status`, `/spec --detect-vault`, `/spec --sync-status`: run the single mapped script in `references/commands.md` §Sub-flag Dispatch, output its stdout verbatim, stop. No additional commentary.

## Output Language

All user-facing output (summaries, questions, confirmations, status, errors) — **Chinese**.

Exceptions (English / original form): technical terms, command names, file paths, code identifiers; content inside code blocks; skill's own rule files (`SKILL.md`, `references/`).

If the user's requirement is in English, generated spec documents may use English; other agent output (summaries, confirmations) stays Chinese.

## Document Output Brevity

When writing or updating a spec document (`requirements.md` / `bugfix.md` / `design.md` / `tasks.md` / `implementation-log.md`), **never reprint the full document content in chat**. The Write/Edit tool UI already shows a preview; the user can also open the file. Duplicating the full content in user-facing text is pure noise.

In chat, report only:

- File path (one line)
- Section headings or 3–8 key change bullets (e.g., "added §4 data model / §5.B Mascot behavior / tightened §6 validation thresholds")
- Open questions, if any
- Next action (gate confirm / next phase / etc.)

Never paste the document body, EARS SHALL clauses, code snippets, full task lists, or full design rationale. Comply if the user explicitly asks; otherwise default to **summary only**.

This applies equally to first-time creation and to follow-up edits.

## References

- `references/commands.md` — **命令完整参考**（入口、子标志 dispatch、可选 spec 名前缀、会话模式、Helper Scripts、Hook 拦截）
- `references/workflow.md` — 完整 phase 协议、interactive selector 命令、`/continue` 上下文加载、EARS 示例
- `references/prompts.md` — **统一 prompt 模板**（selector 用法、澄清格式、列表视图、禁用措辞）
- `references/iteration.md` — iteration 阶段、子循环、文档累积规则
- `references/lock-protocol.md` — 锁机制、接管、只读模式、驱逐
- `references/obsidian.md` — vault 检测、目录树、config.json 生命周期
- `references/templates.md` — 文档模板与样式约定
- `references/help-output.md` — 帮助文本原文（hook 拦截输出源）
- `references/task-swarm.md` — 任务执行阶段可选委派给 task-swarm（多角色 agent 并发；按阶段聚合 coder/reviewer/validator；防"自我认可"）
- `references/task-swarm-example.md` — task-swarm 模式的 tasks.md 完整示例
