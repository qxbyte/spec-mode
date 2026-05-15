---
name: spec-mode
description: Specification-driven workflow for requirements, technical design, task lists, implementation, acceptance, and ongoing spec iteration. Use when the user explicitly invokes /spec or /spec-mode, explicitly says to use spec mode, or the current conversation has an active persistent spec-mode session that has not been ended. Do not use for ordinary coding, planning, requirements, design, or documentation requests unless spec mode is explicitly requested or already active.
---

# Spec Mode

File-first specification-driven workflow for CLI agents (Codex, Claude Code). Generated Markdown documents are the source of truth; coding starts only after requirements, design, and tasks are confirmed.

## Activation Guard

This skill is opt-in only. Activate **only** when the user's current message contains one of:

- `/spec`, `/spec-mode`, `/spec-continue`, `/spec-status`, `/spec-end`
- `/spec -h`, `/spec-mode -h`
- `/spec --set-vault`, `/spec --set-root`, `/spec --detect-vault`, `/spec --vault-status`
- `使用 spec 模式` / `启用 spec 模式` / `用 spec 模式` / `use spec mode`

**Hard rules:**

1. `/spec` and `/spec-mode` always activate the spec workflow — even when the requested work is to inspect or modify the `spec-mode` skill itself.
2. **Command compliance**: when any spec command is triggered, follow the corresponding workflow exactly. Do not skip phases, phase gates, or confirmation steps for any reason. Commands are absolute; the assistant's judgment cannot override a command.
3. **Persistent session exception**: if a persistent spec-mode session is active for the current conversation, route follow-up messages through this skill until the user runs `/spec-end`.

Do **not** activate for ordinary coding, planning, requirements, design, task lists, bugfixes, implementation, or documentation requests. In those cases, work normally — do not create spec folders.

## ⛔ Iron Rules — Top of Mind

These rules are checked at **every turn** of every spec-mode session. Never violate them. Never defer them. If the user pushes back, acknowledge — then comply with the rule first, discuss after.

1. ⛔ **Document-first.** Any change to requirements / design / tasks discussed in chat MUST be written to the corresponding spec document **in the same turn**, *before* further discussion or implementation. Verbal-only changes are invisible to the next session and silently drift from the persisted spec.

2. ⛔ **Post-`/spec-continue` sync — 非常重要.** After `/spec-continue` you are resuming an **already-landed** spec. **Every** subsequent adjustment to requirements or design — even a single clarifying sentence from the user — MUST be reflected in `requirements.md` / `bugfix.md` / `design.md` / `tasks.md` and (for requirements changes) `acceptance-checklist.md`, **in the same turn**. Do not wait for "later", do not batch into "next round", do not say "I'll update it after the code". Write **now**. The user said it → write it. The next session can only see what was persisted; chat is ephemeral.

3. ⛔ **acceptance-checklist follow-mode.** `requirements.md` or `bugfix.md` modified → rewrite `acceptance-checklist.md` in the **same turn**, derived from the new SHALL statements. Failure surfaces as `spec_lint.py` WARNING and `⚠ 落后于 requirements.md` on next `/spec-continue`.

4. ⛔ **Write-before-verify-lock.** Before any `Edit`/`Write` on a spec document, call `python3 scripts/spec_session.py verify-lock <spec-dir> --session <id>`. Returns `evicted` → stop work immediately and tell the user the spec was taken over by another session.

5. ⛔ **Phase gate compliance.** No skipping confirmation steps. No auto-selecting at gates. No "this seems simple, let's skip ahead". Commands are absolute; the assistant's judgment cannot override them.

6. ⛔ **Forced writes.** Every config / document mutation must be persisted on the spot. When a write fails (IOError / permission / `lock_lost`), abort the operation — never continue with in-memory unpersisted state.

These rules trigger detectable signals (lint, `/spec-continue` ⚠ markers, verify-lock exit codes). Treat any of those signals as a regression on your part, not a tool quirk.

## Command Entry

```text
/spec <requirement or path> [extras]            ← one-shot workflow
/spec-mode <requirement or path> [extras]
/spec --persist <requirement or path>           ← persistent session (shows footer)
/spec-continue [spec-slug]                       ← resume / switch; multi-window aware
/spec-status                                     ← show current session status
/spec-end                                        ← end persistent session (docs preserved)

/spec --set-vault <vault-path>                   ← set Obsidian vault → vault/spec-in/<os>-<user>/specs
/spec --set-root <dir>                           ← set any directory as spec root
/spec --detect-vault                             ← detect installed Obsidian vaults
/spec --vault-status                             ← show current root + obsolete-location warnings

/spec -h | /spec-mode -h                         ← help (output references/help-output.md verbatim, stop)
```

`--set-vault` / `--set-root` may be run **at any time** (no need to `/spec-end` first); the new value is written to `~/.config/spec-mode/config.json` immediately and used by all subsequent commands.

If the text after `/spec` is an existing file path, read it as the requirement source; otherwise treat it as the requirement description.

### Sub-flag Dispatch (execute and stop — do NOT trigger spec workflow)

| Flag | Action |
|---|---|
| `--set-vault <path>` | Run `python3 scripts/spec_vault.py set --vault <path>`, show output, stop. |
| `--set-root <path>` | Run `python3 scripts/spec_vault.py set --root <path>`, show output, stop. |
| `--detect-vault` | Run `python3 scripts/spec_vault.py detect`, show output, stop. |
| `--vault-status` | Run `python3 scripts/spec_vault.py get`, show output, stop. |
| `-h` / `--help` | Output `references/help-output.md` verbatim, stop. |
| `--persist <req>` | Initialize persistent session via `spec_init.py --persistent` and start workflow. |
| `--freeform` | Run `python3 scripts/spec_sync.py freeform on` (relax INV-1 for current spec; INV-2 still enforced), show output, stop. |
| `--strict` | Run `python3 scripts/spec_sync.py freeform off` (restore INV-1), show output, stop. |
| `--sync-status` | Run `python3 scripts/spec_sync.py status` (show ledger / pending sync / last violation), show output, stop. |

For any of the dispatch flags above (the first five rows), **do not** run intake, do not create a spec folder, do not enter Plan-mode. Just execute the indicated script and stop.

**Optional spec name prefix.** If the requirement starts with `<名称>：<内容>` (full-width `：`) or `<名称>: <内容>` (ASCII `:` followed by a space), split on the first colon:

- The part **before** the colon is the **spec folder name hint**. Agent derives a semantic English slug from the hint and passes `spec_init.py --name <slug> --requirement-name "<原名称>"`. The original hint is preserved as the displayed `requirementName` in `.config.json`.
- The part **after** the colon is the requirement source text.
- Skip the split if the prefix looks like a path (contains `/` or `\`), a URL, or no colon appears in the first ~30 chars.
- No colon → whole text is the requirement; agent infers the slug from the requirement content as before.

## Pre-requirements Clarification (Plan-mode)

Before generating `requirements.md` / `bugfix.md`, evaluate whether the user's requirement is unambiguous enough to translate into EARS SHALL statements **without invention**.

- **Clear enough** → proceed to workflow selection and document generation.
- **Real ambiguity** affecting scope, behavior, UX, data, validation, or acceptance → enter clarification dialogue first. Phase stays in `intake`. **Do not write any spec document yet.**

Clarification dialogue protocol:

1. Output the questions using **Template B** (开放式澄清问答) in `references/prompts.md`. Each question is labeled `【阻塞】` or `【可延后】`. Aim for ≤5 blocking items per round.
2. End the turn. Wait for the user's reply.
3. After the user answers, parse the response and run the **澄清完成** selector (Template A in `references/prompts.md`):
   - `进入下一阶段` → proceed to workflow selection and document generation. Carry resolved answers into the document; unresolved items go to the `待确认问题` section.
   - `继续澄清` → ask the next round of questions using Template B again.
4. Never invent missing scope, business rules, UI behavior, data fields, or acceptance criteria.

This is the spec-mode equivalent of agent "Plan mode": converge on intent through dialogue before any file is written.

→ 详见 `references/prompts.md`（所有 prompt 输出模板、统一选择器命令、措辞禁忌）

## Document Root Resolution (Iron Law)

Three-tier resolution. **No project fallback, no home fallback.**

1. `--root` argument or `SPEC_MODE_ROOT` env (highest)
2. `~/.config/spec-mode/config.json` → `obsidianRoot` (set via `--set-vault`/`--set-root`; written automatically on first Obsidian detection)
3. Auto-detect Obsidian vault → `<vault>/spec-in/<os>-<user>/specs` (and persist to config)

**All three miss → hard stop**, output guidance and exit:

```
未检测到 Obsidian vault，且未配置 spec 根目录。请选择以下方式之一：
  1. 安装 Obsidian 后重试（推荐）
  2. /spec --set-vault <vault路径>
  3. /spec --set-root <自定义目录>
```

`/spec` and `/spec-continue` use the **same** resolution. Never create `<project>/specs` or `~/new project/specs`.

→ 详见 `references/obsidian.md`（vault 检测、目录树、多 vault 选择）

## Sessions: One-shot vs Persistent

Every `/spec` creates permanent documents (`requirements.md`, `design.md`, `tasks.md`, `acceptance-checklist.md`, `.config.json`). They can always be reopened via `/spec-continue`.

| | one-shot `/spec` | `/spec --persist` |
|--|--|--|
| Session after task completion | Ends | Stays active |
| Status footer | Not shown | Shown after every response |
| Exit | Automatic | Explicit `/spec-end` |

Persistent-mode footer (exact format, shown only in persistent mode):

```
─── spec-mode ─── spec: <slug> | session: <sessionId> | phase: <phase> | /spec-end 退出
```

When in read-only mode (see `references/lock-protocol.md`), append ` | [只读]` before `/spec-end 退出`.

`sessionId` resolution: `$TERM_SESSION_ID` → `$SPEC_SESSION_ID` → `"default"`. Each window must use a distinct sessionId for parallel work.

State files:

- `<spec-dir>/.config.json` — per-spec identity, lifecycle, **lock**, sessions, iteration round
- `<document-root>/.active-spec-mode.json` — v2 window index keyed by sessionId (slug-only, no absolute paths)

## Multi-Window + Lock (Iron Law)

Different agent windows may work on **different** specs in parallel. The **same** spec is held by at most one session at a time via a write lock in its `.config.json`.

**Before any spec document write**, perform three checks:

1. **specId**: active-pointer.specId == .config.json.specId
2. **boundary**: spec_dir is inside documentRoot (`spec_session.ensure_within_root`)
3. **lock**: `python3 scripts/spec_session.py verify-lock <spec-dir> --session <id>` returns `ok`

Any failure → refuse the write, surface the error, do not silently continue.

`/spec-continue <slug>` on a locked spec must offer three options to the user: 强制接管 / 只读查看 / 取消. Never auto-evict without user choice.

Heartbeat: agent must call `python3 scripts/spec_session.py heartbeat <spec-dir>` before every Edit/Write on a spec document. Stale lock threshold: 30 minutes (`SPEC_MODE_LOCK_STALE_SECONDS` to override).

→ 详见 `references/lock-protocol.md`（5 个 lock 子命令、接管协议、只读模式、被驱逐窗口行为）

## Phase Gates

Phase order (no skipping confirmations):

1. requirements (or bugfix)
2. **Confirm**
3. design
4. **Confirm**
5. tasks
6. **Confirm**
7. Ask whether to execute tasks
8. Code → validate → accept
9. (post-acceptance) `iteration`

Confirmation protocol — for every phase boundary, in the same response:
1. Show the document path, summary, key changes, unresolved questions
2. Show confirmation options via `scripts/spec_choice.py`. In a non-interactive shell (Claude Code Bash, CI) the script prints the option block + `AWAITING_USER_CHOICE` sentinel on stdout and exits 0 — relay the stdout block to the user **verbatim** and end the turn. Never re-run the script to "retry" the prompt.
3. **End the turn.** Never proceed in the same response.

Auto-selecting a default at a phase gate is never acceptable.

→ 详见 `references/workflow.md` §Phase Gates（完整 9 步、`spec_choice.py` 调用范本）
→ 详见 `references/iteration.md`（iteration 阶段、子循环、文档累积规则）

## Document-first Discipline

Spec documents are the sole persistent memory. Any change not written to a document is invisible to the next session. See also Iron Rules #1, #2, #3, #6 at the top of this file.

**Iron rules (apply from the moment a persistent session is active, **and** apply equally — and especially — after `/spec-continue`):**

1. **Requirement change** → update `requirements.md` / `bugfix.md` **first**, then continue
2. **Design decision** → update `design.md` **first**, then implementation
3. **Task status change** → update `tasks.md` **immediately** when a task starts (`[~]`), completes (`[x]`), or is blocked
4. **New task or sub-task** → append to `tasks.md` **before** starting it
5. **requirements.md / bugfix.md modified** → must rewrite `acceptance-checklist.md` in the **same turn** (跟随式生成；不写则下次 `/spec-continue` 显示 ⚠ 落后于 requirements.md)
6. **Write-before-verify**: before any `Edit`/`Write` on a spec document, call `spec_session.py verify-lock`. EVICTED → stop work and tell the user.
7. **Post-`/spec-continue` sync (非常重要)**: after `/spec-continue`, the spec docs are already landed. Any further requirement/design adjustment from the user (including verbal-only "顺便改一下…") MUST be applied to the landed `requirements.md` / `design.md` / `tasks.md` / `acceptance-checklist.md` **in the same turn it is raised**, before any code action. **Never** leave a chat-only change unwritten between turns — the next session will lose it. If multiple docs are affected by one change, update all of them in the same turn.

These writes are non-negotiable. If the user asks to skip writing and proceed, acknowledge — then write first, proceed second. **Writes are forced**: if a write fails (IOError/permission), abort the operation; never continue with in-memory unpersisted state.

→ 详见 `references/workflow.md` §1.1（自然语言路由表）

## Workflow Selection

Classify the request before creating documents:

- Feature, behavior-first → **Requirements-first** (recommended default)
- Feature, architecture-first → **Technical Design first**
- Bug / regression / failing test → **Bugfix**

Use `scripts/spec_choice.py` when the workflow matters and is unclear. In non-interactive shells the script outputs the option block + `AWAITING_USER_CHOICE` sentinel and exits 0 — relay stdout to the user and end the turn. Never silently choose for the user.

## Output Language

All user-facing output (summaries, questions, confirmations, status, errors) — **Chinese**.

Exceptions (English / original form):
- Technical terms, command names, file paths, code identifiers
- Content inside code blocks
- Skill's own rule files (`SKILL.md`, `references/`)

If the user's requirement is in English, generated spec documents may use English; other agent output (summaries, confirmations) stays Chinese.

## Helper Scripts

- `scripts/spec_init.py` — create spec directory; **requires `--name <slug>`** (agent generates the semantic slug)
- `scripts/spec_session.py` — start / continue / status / end / list / list-specs / load / acquire / release / heartbeat / verify-lock / iterate
- `scripts/spec_vault.py` — detect / set --vault / set --root / get
- `scripts/spec_lint.py` — validate spec files (now also lock-field + checklist-staleness checks)
- `scripts/spec_status.py` — task-progress view (thin wrapper over `spec_session.py load --json`)
- `scripts/spec_choice.py` — selector. TTY → curses ↑/↓ + Enter. Non-TTY (Claude Code Bash, CI) → prints option block + `AWAITING_USER_CHOICE` sentinel on stdout, exits 0; agent relays to user and ends turn.

## Help Output

When `/spec -h` / `/spec-mode -h` is triggered, output exactly `references/help-output.md` and stop.

## References

- `references/workflow.md` — full phase protocol, interactive selector commands, `/spec-continue` context loading, EARS examples
- `references/prompts.md` — **unified prompt templates** (selector usage, clarification format, list view, forbidden phrasings)
- `references/iteration.md` — iteration phase, sub-loop, document accumulation rules
- `references/lock-protocol.md` — lock mechanism, takeover, read-only mode, eviction
- `references/obsidian.md` — vault detection, directory tree, config.json lifecycle
- `references/templates.md` — document templates and style conventions
- `references/help-output.md` — verbatim help text
