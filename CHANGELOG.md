# Changelog

## Unreleased

_no entries yet_

## 0.8.0 (2026-05-19)

### Changed — host-neutral wording + sessions schema field rename

Two coordinated cleanups so the plugin reads as host-agnostic and the
on-disk schema uses neutral key names.

**1. Description neutralization across docs and code.** All
user-facing wording that singled out one host CLI was reworded to
neutral terms ("host CLI" / "宿主" / "CLI agent"). Affected files:

- `README.md` / `README.zh-CN.md` — install section lists CodeBuddy
  before Claude Code; tagline says "for CLI coding agents".
- `SKILL.md` + 6 `references/*.md` — "Claude Code 内置 X 工具" → "宿主
  内置 X 工具"; "Claude 窗口" / "Claude 会话" → 中性词。
- `DESIGN.md` / `CHANGELOG.md` / `CONTRIBUTING.md` /
  `IMPLEMENTATION-AUDIT.md` — same treatment.
- `plugin.json` / `marketplace.json` description fields drop the
  "(Claude Code + CodeBuddy)" suffix.
- `spec_session.py` `HELP_OUTPUT_TEXT`, hook context strings, error
  messages neutralized.

Technical contracts retained verbatim because they are platform-
injected, not stylistic: `CLAUDE_PLUGIN_ROOT` env var (with the
existing `:-${CODEBUDDY_PLUGIN_ROOT}` fallback), `.claude-plugin/`
directory name (plugin discovery protocol), the `claude plugin …`
install commands users actually type.

**2. Sessions / state.json schema: `claude_session_id` → `session_id`.**
All write sites now produce the new key. Read sites are
backwards-compatible:

- `read_session()` (`spec_session.py:135`) auto-migrates legacy files
  by copying `claude_session_id` → `session_id` in-memory; the next
  write lands the new key on disk.
- `StateMachine.load()` (`task_swarm_state.py:149`) does the same for
  `~/.specode/runs/<run_id>/state.json` (renames the dataclass field
  too: `sm.claude_session_id` → `sm.session_id`).
- Lock holder reads (`list-specs`, `on-heartbeat-quiet`) fall back
  through `holder → session_id → claude_session_id` in priority
  order. The lock field's actual persisted key has always been
  `holder`; the rename does not touch `<spec-dir>/.config.json`.

No manual migration needed. Existing `~/.specode/sessions/*.json`
and `state.json` files keep working; they get rewritten in the new
schema on the next mutating CLI call.

### Changed — command md files carry copy-pasteable CLI templates

Every command md under `plugins/specode/commands/` now opens with an
**「立即调用」** section that embeds the full `sh
"${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/run.sh"
"${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/<name>.py"
<args>` template the model should execute. Motivation: a recent
session showed the model looping six times on bare `python3
spec_session.py …` invocations against the wrong cwd, because the
command md only listed `/specode:continue $ARGUMENTS` and the model
never consulted SKILL.md for the wrapper rule before retrying.

SKILL.md also gained a "**CLI 调用规约（强制）**" subsection and an
Iron Rule explicitly banning bare `python3` invocations.

### Added — backwards-compat regression tests

Two new tests pin the auto-migration behavior so future refactors
can't quietly drop legacy support:

- `test_read_session_migrates_legacy_claude_session_id`
  (`tests/test_spec_session_business.py`).
- `test_load_migrates_legacy_claude_session_id`
  (`tests/test_task_swarm_state.py`).

### Removed

- `migrate-from-spec-mode.sh` — one-shot migration script for users
  upgrading from 0.1.0's `spec-mode` plugin name. Long past its
  usefulness window; deleted.

### Tests

154 pass (152 previous + 2 new compat regressions). All existing
fixtures updated to write the new `session_id` key directly.

### Migration

None. Plugin cache update sufficient:

```sh
# Adjust the CLI name for whichever host you use (claude / codebuddy).
claude plugin marketplace update specode
claude plugin update specode
```

## 0.7.3 (2026-05-19)

### Changed — all selector references unified to YAML three-section format

Following 0.7.2 (which rewrote SELECTOR_PROMPTS to the three-section
+ YAML format), this release brings the two **dynamic selectors** in
`references/obsidian.md` and the **8 static scenarios** in
`references/prompts.md` to the same format so every selector
reference across the plugin is visually identical.

Two dynamic selectors updated:

- **§3 multi-vault selection** (when `spec_vault.py detect` finds >1
  vault and no `obsidianRoot` is set yet) — was Python-call form;
  now three-section YAML.
- **§5.1 `/specode:continue` no-slug spec picker** (when
  `spec_session.py list-specs` returns >0 specs) — was Python-call
  form; now three-section YAML. Empty list still skips the tool and
  prompts the user to run `/specode:spec <requirement>` instead.

The 8 static scenarios in `prompts.md` (workflow-choice,
clarification-{wizard,done}, doc-confirm-{requirements,bugfix,design,
tasks}, tasks-execution, takeover-options, acceptance-gate,
iteration-scope) were rewritten from Python-call form to **byte-for-
byte the same YAML three-section format as SELECTOR_PROMPTS**, with
the same wrapper section structure (目的 / 上下文 / 前置动作 / 工具参数 /
约束). The previous "Python-call form and YAML form are equivalent"
caveat is removed — there is now only one format.

A worked example with three clarification points (login UX scenario)
was added to §B1 to give the model a concrete reference for wizard
construction.

### Tests

- All 152 tests pass; no test changes required (snapshots already
  match the new format after 0.7.2).

### Migration

None. Plugin cache update sufficient:

```sh
claude plugin marketplace update specode
claude plugin update specode
```

## 0.7.2 (2026-05-19)

### Changed — SELECTOR_PROMPTS rewritten to three-section + YAML format

All 11 entries in `spec_session.py SELECTOR_PROMPTS` have been rewritten
to a **three-section + YAML-indented** format that matches the
"directly paste into the host CLI and the tool fires" prompt style
the maintainer validated in another window.

Each constant now has the same structure:

```
## 选择器节点：<scenario>

**目的**：<why this selector is appearing now; what user just did>

**上下文**：active spec=<slug>, phase=<phase>, <other dynamic fields>

**前置动作（chat 简报，≤N 行）**：<what to write to chat BEFORE calling the tool>

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "<full question>"
    header: "<≤12 char chip>"
    multiSelect: <true|false>
    options:
      - label: "<option A>"
        description: "<rationale / trade-off>"
      - label: "<option B>"
        description: "..."

**约束**：<scenario-specific dos and don'ts>
```

Why this change:
- The previous wrapper text ("必须呈现「X」选择器（类型 A 单列单选）...") was diagnostic / declarative — the model still had to translate it into tool arguments mentally.
- The new format **is itself a paste-and-fire tool prompt** — the YAML block can be lifted as-is into the model's mental model of the `AskUserQuestion` call.
- Three sections make purpose / pre-action / call / constraints visually separate, so the model can't skip the chat briefing before tool call (doc-confirm-* constants explicitly require 3-8 bullets of summary).

What this changes in behavior:
- doc-confirm-* now explicitly mandates a chat briefing **before** the tool call (file path + 3-8 change bullets + open questions).
- takeover-options explicitly forbids the "（推荐）" marker (user must judge based on other holder's liveness).
- acceptance-gate conditionally moves the "（推荐）" marker (only when `n_fail=0`).
- tasks-execution explicitly notes the 4 options have saturated the tool ceiling; Other for custom.
- iteration-scope explicitly flags `multiSelect: true` as the **only** Type C usage in the protocol.

`references/prompts.md` updated with a note that the Python-call form (`AskUserQuestion(questions=[...])`) and the YAML-indented form (used by the hook) are **semantically equivalent** — both mean "call the tool with these arguments".

### Tests

- `test_selector_prompts.py` snapshot assertions updated to match new
  format (assert `选择器节点：`, `AskUserQuestion`, `multiSelect: false|true`,
  YAML `options:` / `label:` markers, `Type something` + `Other` in
  forbidden-list).
- All 152 tests pass.

### Migration

None. Existing sessions / specs unaffected; hooks behavior otherwise
unchanged. Plugin cache update sufficient:

```sh
# Adjust the CLI name for whichever host you use (claude / codebuddy).
claude plugin marketplace update specode
claude plugin update specode
# restart the host CLI
```

## 0.7.1 (2026-05-19)

### Changed — selector protocol switched to AskUserQuestion

The 11 entries in `spec_session.py SELECTOR_PROMPTS` have been
rewritten to instruct the model to call the host CLI's built-in
**`AskUserQuestion`** tool instead of emitting a markdown list with
a `AWAITING_USER_CHOICE` sentinel and asking the user to reply with
a number.

The three selector types map to `AskUserQuestion` parameters as
follows:

- **Type A (single-select)** — `questions=[1 q]` + `multiSelect=false`
- **Type B (wizard)** — `questions=[2-4 q]`, each `multiSelect=false`
  (each question shows as its own chip-tab)
- **Type C (multi-select)** — `questions=[1 q]` + `multiSelect=true`

Why this changes things:

- The host tool renders arrow-key navigation + Enter to
  submit + ESC to cancel + auto-provided "Other" for free-text
  input. The user never types a number.
- The historical reserved positions `Type something` / `Chat about
  this` / `Submit` are deleted from the selector text — "Other" and
  ESC are provided by the tool.
- The `AWAITING_USER_CHOICE` sentinel is removed everywhere it
  drove turn termination — calling the `AskUserQuestion` tool is
  itself a turn terminator.
- `references/prompts.md`, `SKILL.md` §Selectors, and the
  multi-vault selector in `references/obsidian.md` §3 all now
  describe selectors as `AskUserQuestion(questions=[...])`
  invocations with explicit Python-style parameter blocks.
- `references/workflow.md` §9.1 (`/specode:continue` with no slug)
  and §10 (phase-gate output order) updated; the model no longer
  outputs a numbered list and waits for a reply — it calls the
  tool.

The historical wording (`AWAITING_USER_CHOICE` / "请回复编号" /
`Type something` / `Chat about this`) is now listed as **forbidden
phrasing** in `references/prompts.md`'s compat section so the model
can recognize and reject it if encountered in older docs.

### Tests

- `tests/test_selector_prompts.py::test_workflow_choice_snapshot`
  updated to assert `AskUserQuestion` / `multiSelect` / `"label"`
  fields appear, and that `Type something` / `Other` appear in the
  forbidden-list section.
- All 152 tests pass (75 v0.6 + 77 task-swarm = 152; no new tests
  introduced in 0.7.1).

### Migration

No state migration needed. Existing `~/.specode/sessions/<id>.json`
files are unaffected. Hooks behavior is otherwise unchanged.

## 0.7.0 (2026-05-19)

### Added

- **task-swarm multi-agent orchestrator** (was originally targeted at
  a separate v0.7). Six new scripts:
  - `task_swarm.py` (1333 lines) — CLI: `init` / `status` / `plan` /
    `advance` / `writeback` / `heartbeat` / `resolve`.
  - `task_swarm_state.py` — StateMachine + `state.json` atomic
    persistence + deadloop detection (3 consecutive same fail
    signatures → group failed-deadloop, no infinite loop).
  - `task_swarm_parse_md.py` — `tasks.md` parser + group splitting
    by `@writes` file-conflict (no two stages in the same group
    touch overlapping files).
  - `task_swarm_outbox.py` — strict schema validators for coder
    `result.md` / reviewer `review.md` (with evidence tags) /
    validator `validation.md` (with fix targets).
  - `task_swarm_writeback.py` — line-safe `tasks.md` writeback;
    refuses any diff outside `[ ] → [x]` checkbox toggle + `> `
    annotation block append.
  - `task_swarm_prompt.py` — prerendered prompts for coder /
    reviewer / validator subagents at every phase / round.
- **`on-task-completed` hook (PostToolUse matcher=Task)** — when a
  subagent returns during a task-swarm run, calls
  `task_swarm.py plan` and injects the next-step advice into
  `additionalContext` (9 state matrix per §11.6). Never blocks.
- **`on-heartbeat-quiet` hook (UserPromptSubmit, second handler)** —
  silently renews the spec lock on every user turn when
  `mode=active`. Never injects `additionalContext`.
- **`on-pre-tool-use` hook (PreToolUse matcher=Edit|Write|MultiEdit)** —
  when in a task-swarm run and the target is the active spec's
  `tasks.md`, injects an advisory reminding the model to go through
  `task_swarm.py writeback` instead of direct edits. Never blocks.
- **`spec_session.py list-specs --root <path>`** — replaces the
  removed `spec_choice.py` discovery flow for `/specode:continue`
  with no slug; returns a JSON of all specs in the doc root with
  lock state / phase / mtime so the model can present a
  numbered-list selector without Grep'ing the project directory.
- **`spec-writer` agent** (already in 0.6) and **four task-swarm
  agents** (planner / coder / reviewer / validator, already
  shipping; tool isolation unchanged).
- **References** added: `task-swarm.md` (full protocol incl.
  §11.1–§11.7 spec) and `task-swarm-example.md` (3-stage / 8-task
  worked example showing group split + traceability + annotation
  block format).
- **77 new tests** for the task-swarm pipeline; **152 tests total**
  (75 v0.6 + 77 v0.7) all passing.

### Changed

- **`SKILL.md` slimmed from ~335 lines to ~194 lines** — heavy
  detail moved to references; SKILL.md is now an activation contract
  + dispatch table, not a manual.
- **`/specode:continue` flow corrected** — model is now explicitly
  forbidden from Grep'ing the project directory; must go through
  `spec_vault.py status` → `spec_session.py list-specs`. See
  `references/obsidian.md` §5.1.
- **Multi-vault selection UI** now follows §3.7.1 Type A skeleton
  with `AWAITING_USER_CHOICE` sentinel and `Type something` /
  `Chat about this` reserved positions (was a freer list before).
- **All version-difference wording removed** from runtime docs
  (commands / agents / skills / references) — features are
  documented as "what works", not "what is new in vX".

### Removed

Nothing further. All deletions from 0.5.0 remain: `spec_guard.py`,
`spec_sync.py`, `bash_guard.py`, `task_swarm_guard.py`, all INV-1
through INV-11, `spec_choice.py`, sentinel short-circuit, audit
log, telemetry.

### Hook inventory (final)

| Event | Handler | Behavior |
|---|---|---|
| `SessionStart` | `on-session-start` | Initializes sessions file; emits session_id reminder |
| `UserPromptSubmit` | `on-user-prompt` | Fast-path / selector / doc-first / status-footer / mode reminder |
| `UserPromptSubmit` | `on-heartbeat-quiet` | Silently renews lock on every active turn |
| `PreToolUse` | `on-pre-tool-use` | Reminds (never blocks) on direct `tasks.md` edits during task-swarm runs |
| `Stop` | `on-stop` | Code-doc sync reminder + spec-mode continuation reminder |
| `PostToolUse` (matcher=Task) | `on-task-completed` | task-swarm next-step advice |
| `SessionEnd` | `on-session-end` | Writes mode=ended + releases lock as fallback |

All hooks `exit 0`. `SPECODE_GUARD=off` short-circuits all of them.

## 0.6.0 (2026-05-19)

### Added — full re-implementation on the 0.5.0 skeleton

0.5.0 stripped the project back to a skeleton. 0.6.0 rebuilds the runtime
**without** any of the previous INV / exit-2 enforcement, on top of a new
"advisory hooks + selector prompts + session-bound state" foundation.

- **Session lifecycle bound to host `session_id`.** New per-session state
  file at `~/.specode/sessions/<session_id>.json` with fields
  `mode` (active / readonly / ended), `active_spec_slug`, `phase`,
  `lock_state`, `pending_selector`, ... — all writes are atomic
  (tempfile + `os.replace` + `os.fsync`) and rolled back on partial
  failure. `<spec-dir>/.config.json` lock field uses `holder` as the
  persisted key.
- **Persistent session is the only mode** — `--persist` flag removed.
  `/specode:spec <requirement>` always creates a persistent session;
  `/specode:end` writes `mode=ended` and hooks immediately stop
  injecting.
- **Four v0.6 hooks, all advisory (`exit 0`, never block):**
  - `SessionStart` → `on-session-start` — initializes sessions file +
    injects `session_id` reminder.
  - `UserPromptSubmit` → `on-user-prompt` — overlays up to 5 segments
    based on `mode`: fast-path interception (`/specode:spec -h` / 
    `--vault-status` / `--detect-vault` / `--sync-status`), selector
    prompt (per `pending_selector`), document-first reminder, status
    footer template, and spec-mode continuation reminder.
  - `Stop` → `on-stop` — injects code-doc sync reminder (output side)
    and spec-mode continuation reminder when `mode=active`.
  - `SessionEnd` → `on-session-end` — writes `mode=ended` + releases
    any lock the dying session still holds (forgiveness fallback if
    user forgot `/specode:end`).
- **Selector text is generated by the model, not the script.** Three
  selector types (A single-select / B wizard / C multi-select) match
  the three reference screenshots shipped by the project. The hook
  injects an `additionalContext` constant telling the model *which*
  type and scenario to render; the model formats the text per the
  skeletons in `references/prompts.md`. 11 fixed scenario constants
  shipped in `spec_session.py SELECTOR_PROMPTS` (workflow-choice,
  clarification-wizard, clarification-done, doc-confirm-{requirements,
  bugfix,design,tasks}, tasks-execution, takeover-options,
  acceptance-gate, iteration-scope).
- **Status footer** required on every active-spec turn:
  `─── spec-mode ─── spec: <slug> | session: <8-prefix> | phase: <p> | /specode:end 退出`
  (readonly mode adds `[只读]` segment).
- **Document-first discipline as advisory hooks**, replacing the
  exit-2 INV-1 / INV-2 enforcement entirely:
  - `on-user-prompt` injects "📝 文档优先提醒（输入侧）" listing the
    six spec docs, asking the model to check whether the current
    input warrants a doc edit *before* code.
  - `on-stop` injects "🔄 代码-文档同步提醒（输出侧）" asking the
    model to self-check whether the just-finished turn left a
    code change without a matching doc update.
- **`/specode:spec -h` fast-path** — hook intercepts the prompt and
  injects the full help text into `additionalContext` for the model
  to verbatim-print, replacing the prior unstable "model reads
  references/help-output.md" path.
- **Six core scripts** (stdlib-only): `spec_vault.py` (3-tier doc
  root resolution + Obsidian vault detection), `spec_init.py` (spec
  scaffolding with forced double-write of sessions + .config.json
  and rollback), `spec_session.py` (1500 LOC — business commands,
  hook subcommands, SELECTOR_PROMPTS), `spec_lint.py` (4 advisory
  rules), `spec_status.py`, plus `run.sh` / `run.cmd` launchers.
- **`spec-writer` agent** — new agent for document generation with
  tools `Read, Write, Edit, Grep, Glob` (no Bash; physical
  isolation prevents the agent from touching code, locks, or
  phase transitions).
- **SKILL.md and 6 references rewritten** for the new model:
  `workflow.md`, `lock-protocol.md`, `obsidian.md`, `prompts.md`
  (selector scenarios constant library), `templates.md` (six doc
  templates + EARS SHALL), `iteration.md`.
- **75 pytest tests** covering 3-tier vault resolution, init &
  rollback, business lock state machine, four hooks across mode
  matrix, SELECTOR_PROMPTS snapshot, lint rules, and end-to-end
  event chain. All passing.

### Removed

Nothing further beyond the 0.5.0 skeleton removal. **INV-1 through
INV-11 and `spec_choice.py` remain gone** and are not coming back —
their goals are now served by advisory hook injections plus model
self-discipline guided by SKILL.md.

### Global bypass

`SPECODE_GUARD=off` short-circuits all hooks to `exit 0` with no
output and no state writes. Reserved for debugging.

### Compatibility

- **Plugin commands**: `/specode:spec`, `/specode:continue`,
  `/specode:end`, `/specode:status`, `/specode:task-swarm`
  (placeholder, v0.7).
- **State migration**: nothing automatic. Users coming from 0.4.x
  who still have `~/.specode/sessions/*.json` in the old schema
  should run `/specode:end` once (which will write the new schema
  with `mode=ended`) or remove the file. New schema is written
  starting from the next `SessionStart`.

## 0.5.0 (2026-05-18)

### Removed (breaking — please read)

This release strips the plugin back to a skeleton. Every runtime
enforcement and helper introduced from 0.1.0 through 0.4.0 has been
removed; what remains is the plugin shell and the agent role docs.

- **All hook handlers removed.** `plugins/specode/hooks/` (both
  `hooks.json` and the `hooks-probe.json` diagnostic) is deleted. The
  6 hook events (SessionStart / UserPromptSubmit / PreToolUse /
  PostToolUse / Stop / SessionEnd) no longer fire any plugin code.
- **All invariants removed.** INV-1 through INV-11 (CDSG hard-deny,
  CDSG advisory, eviction guard, acceptance follow-mode, status-block
  injection, phase gate, subagent_type prefix, subagent @writes
  boundary, tasks.md writeback, outbox schema, non-interactive Bash
  guard) no longer exist as code paths.
- **All scripts removed.** `plugins/specode/scripts/` is deleted —
  `spec_guard.py`, `spec_session.py`, `spec_init.py`, `spec_sync.py`,
  `spec_choice.py`, `spec_state.py`, `spec_status.py`, `spec_lint.py`,
  `spec_vault.py`, `spec_telemetry.py`, `task_swarm.py`,
  `task_swarm_*.py`, `bash_guard.py`, `run.sh`, `verify_local.sh`.
- **Tests removed.** `plugins/specode/tests/` is deleted in full.
- **Skill references removed.** `plugins/specode/skills/specode/references/`
  (workflow / commands / prompts / lock-protocol / templates / iteration /
  obsidian / help-output / task-swarm / task-swarm-example /
  sample-analysis) is deleted.
- **SKILL.md** rewritten as a short skeleton describing the spec-mode
  activation contract; the iron rules referencing INV / hooks /
  scripts are gone.
- **`/task-swarm` command** rewritten as a placeholder; the 7-step
  CLI-driven orchestrator protocol it used to host is removed.

### Kept

- `.claude-plugin/marketplace.json` + `plugins/specode/.claude-plugin/plugin.json`
- `plugins/specode/commands/` — entry stubs for `/spec`, `/continue`,
  `/end`, `/status`, `/task-swarm`
- `plugins/specode/skills/specode/SKILL.md` — skeleton
- `plugins/specode/agents/` — task-swarm planner / coder / reviewer /
  validator role docs (descriptive only; the orchestrator that
  dispatched them is gone)
- Top-level docs (`README.md` / `README.zh-CN.md` / `CHANGELOG.md` /
  `CONTRIBUTING.md` / `DEV.md` / `migrate-from-spec-mode.sh`) are
  retained and updated to reflect the new skeleton state.

### Migration

No automatic migration. Reinstall on top of the new version to drop
the hooks; user runtime state under `~/.specode/` and
`~/.config/specode/` is untouched and can be removed manually:

```sh
rm -rf ~/.specode ~/.config/specode
```

If you were relying on any 0.4.x behaviour (CDSG advisories, INV-11
non-interactive Bash guard, task-swarm orchestrator), pin to
`specode--v0.4.0` until the runtime is rebuilt.

## 0.4.0 (2026-05-18)

### Changed (behavior change — please read)

- **CDSG downgraded to advisory.** INV-1 / INV-2 / INV-4 / INV-6 no
  longer block the tool call when violated. Instead they record a
  sticky advisory on `.sync-ledger.json` (new field
  `pending_advisories`) and inject it into the next
  `UserPromptSubmit` status block. Rationale: the previous hard-deny
  caused legitimate work (e.g. P0 hot-fixes during task-swarm coder
  rounds) to be interrupted mid-stride while the model retried
  permutations to satisfy the rule. Advisory keeps the drift signal
  visible without breaking flow.
  - Auto-clear: editing any spec doc drops INV-1/2/4 advisories
    (the drift those warned about is being addressed).
  - Manual dismiss: `python3 scripts/spec_sync.py dismiss-advisories
    [--inv INV-1,INV-2]`
  - Visible in: ledger `pending_advisories[]`, status block in next
    turn, and `spec_sync.py status` output.
  - **Data-safety INVs unchanged**: INV-3 / INV-7 / INV-8 / INV-9
    remain hard-enforced (`exit 2`). They protect against actual data
    corruption (evicted writes, bad subagent dispatch, subagent
    boundary breach, tasks.md non-writeback edit).

### Added

- **INV-11 — Non-interactive Bash guard.** New `bash_guard.py` with two
  layers of defense against agent Bash hanging on TTY prompts:
  - **PreToolUse hard-deny** of 14 known interactive command patterns:
    `npm create` (no `--yes`), `npx` (no `--yes`), `npm init` (no `-y`),
    `yarn create`, `pnpm create`, `git rebase -i`, `git add -p/-i`,
    `git commit` (no `-m`/`-F`/`--amend --no-edit`), TUI editors/pagers
    (`vim`/`nano`/`less`/`top`/`man`/...), interactive shells (`bash -i`,
    `python -i`), bare REPLs (`python3` alone), `ssh` without
    `BatchMode`, `gh pr create` without `--title`/`--body`, `apt install`
    without `-y`. Each denial includes a ready-to-paste non-interactive
    rewrite.
  - **PostToolUse hang signature scan** of Bash stdout/stderr tail
    (4 KiB) for ~17 known prompt strings (`Ok to proceed?`, `[Y/n]`,
    `password:`, `确认吗`, etc.) plus exit code 124 (`timeout` kill).
    When detected, injects an `additionalContext` advisory into the
    next turn telling the model the previous command hung and not to
    retry the same form.
  - Hooks: `hooks.json` PreToolUse matcher extended from
    `Edit|Write|MultiEdit|Task` → `+Bash`; PostToolUse from
    `Edit|Write|MultiEdit` → `+Bash`. INV-11 works without an active
    spec session (Bash hangs are universal, not spec-bound).
  - 55 new unit tests in `tests/test_bash_guard.py` (positive +
    negative samples per rule, hang signature detection).

- **`/spec --dismiss-advisories` CLI** (`spec_sync.py dismiss-advisories`)
  — clears all sticky advisories or `--inv INV-1,INV-6` for a subset.

- **SKILL.md Iron Rule #9** — Non-interactive Bash discipline. Lists
  the safe forms (`npm create xxx -- --yes`, `git commit -m`, etc.)
  the model should default to before the hook ever has to deny.

### Migration

Users on 0.3.x upgrading to 0.4.0:

- Code paths that previously expected `exit 2` from INV-1/2/4/6 now
  see `exit 0` plus a sticky advisory. Re-tune any local automation
  that grepped `~/.specode/audit/*.log` for `deny-INV-1` —
  the new audit string is `advisory-INV-1`.
- `pending_advisories[]` field appears in `.sync-ledger.json` on first
  hook fire after upgrade. Older ledgers without the field continue
  to work (defaulted to `[]`).
- No spec session needs to be re-created.
- `freeform` mode meaning subtly shifted: previously "INV-1 bypass,
  INV-2 still enforced"; now "INV-1 silenced (no advisory recorded
  either); INV-2/4/6 still raise advisories." Effectively quieter.

## 0.3.2 (2026-05-18)

### Fixed

- **`spec_choice.py` hang under CodeBuddy Bash** — observed a single
  Stop-gate selector running for 1h16m, with multiple zombie selectors
  accumulating per spec session (one per phase gate). Root cause: TTY-only
  `input()` / curses paths blocked indefinitely when stdin was a pipe
  without EOF (CodeBuddy harness behavior). Both paths are now deleted —
  the script always emits options + `AWAITING_USER_CHOICE` sentinel and
  exits 0. Physically cannot block on stdin. `--no-curses` flag kept as
  a no-op for back-compat.

### Added

- **CI static guard against blocking stdin reads** — new
  `tests/test_no_blocking_io.py` tokenizes every runtime script under
  `scripts/` and fails on any `input()` / `raw_input()` /
  `sys.stdin.read*` / `getpass.getpass(` not explicitly whitelisted
  with a `# stdin-block: <reason>` comment marker. Prevents future
  regressions of the hang class.
- **`tests/test_spec_choice.py`** — 9 subprocess-driven tests with
  `timeout=3s` so a hang is a regression.
- **SKILL.md Iron Rule #8 — selector via `spec_choice.py` only.**
  Every phase-gate selector MUST be produced by running the exact
  `spec_choice.py` command from `references/prompts.md` and relaying
  its stdout verbatim. Hand-rolling silently drops newer options the
  script knows about (real observed regression: 任务执行 selector
  rendered as 3 options because the model wrote them from memory,
  dropping `用 task-swarm 多 agent 并发`).
- **SKILL.md Document Output Brevity rule** — when writing or updating
  a spec doc, do not reprint the full content in chat. Report only:
  file path, 3-8 section bullets, open questions, next action.

### Notes

- `scripts/spec_guard.py` legitimately reads stdin (hook payload from
  the host CLI, bounded JSON + immediate close) — annotated
  with `# stdin-block: hook entry point` to satisfy the new scanner.

## 0.3.1 (2026-05-18)

### Added

- **Local-only telemetry** (`~/.specode/telemetry.jsonl`): opt-in via
  `SPECODE_TELEMETRY=on`, append-only single file so `grep` / `jq` stay
  trivial. **Absolutely no remote upload** — purely for the user's own
  inspection of flow execution.
  - Events: `spec.init` / `spec.phase_transition` / `spec.end` /
    `inv.violation` (INV-1..9, with `spec_slug` + `phase`) /
    `swarm.run_start` / `swarm.stage_round` / `swarm.stage_done` /
    `swarm.writeback` / `swarm.run_end`.
  - Records carry `spec_slug` / `cwd` / `run_id` so you can aggregate per
    spec or per project.
  - Size cap defaults to 50 MB (overridable via
    `SPECODE_TELEMETRY_MAX_BYTES`); rotates to `telemetry.jsonl.0` once.
  - All IO errors are swallowed — telemetry never breaks a hook.
- `python3 scripts/spec_state.py telemetry-summary [--days N] [--json]`
  aggregates the telemetry file locally: counts per event, INV violations
  top-list, per-spec phase-transition / violation totals, and task-swarm
  average rounds per converged/failed stage.

### Fixed

- **task-swarm fork description now carries scope** (`[validator-fail-fix]`
  / `[advisory]` / `[re-run]`). Previously a checkpoint stage's r2 coder
  was labelled `阶段 N coder-r2: <title>` with no scope, and the
  orchestrator commonly improvised a description like "修复 N 个 P0" off
  the validator outbox — reading as if the reviewer had triggered a fix
  loop (reviewer is advisory and never does). With scope baked in, the
  Task UI now reads e.g. `阶段 5 coder-r2 [validator-fail-fix]: ...` and
  `commands/task-swarm.md` explicitly tells the orchestrator to copy
  `<json.description>` verbatim.
- **validator agent now forbidden from using P0/P1 severity labels** in
  fix guidance (`agents/task-swarm-validator.md`). Those tags are
  reviewer terminology; validator fail is itself blocking. This prevents
  the orchestrator from observing "(P0)" markers in validator output and
  carrying them into the fork description.

## 0.3.0 (2026-05-18)

### Added

- **Task-Swarm control inversion (CLI-driven orchestrator)**: the main model
  no longer drives the state machine in long context — it now follows a tiny
  `init → loop(next → fork → parse → advance) → writeback → done` protocol
  while all determinism (round counting, convergence, dead-loop detection,
  outbox parsing, tasks.md write-back) lives in Python scripts.
  - New CLI: `scripts/task_swarm.py` with 7 sub-commands
    (`init` / `next` / `parse` / `advance` / `writeback` / `status` / `resolve`).
  - New modules: `task_swarm_state.py` (state machine + round tracking),
    `task_swarm_parse_md.py` (stage-aggregated dispatch from tasks.md),
    `task_swarm_outbox.py` (schema-checked result/review/validation parsing),
    `task_swarm_prompt.py` (pre-rendered subagent prompts), and
    `task_swarm_writeback.py` (line-safe tasks.md edits — checkbox toggle +
    `> ` notes only).
  - Per-run workspace under `.task-swarm/runs/<run_id>/` with predictable
    `agents/stage-N-{role}[-rN]/` layout — easy to inspect, replay, or clean.
- **INV-7 / INV-8 / INV-9 / INV-10** hooks (task-swarm period only):
  - **INV-7** — `Task` calls must use `subagent_type` prefixed with
    `specode:task-swarm-`; otherwise the hook denies (prevents accidental
    fall-back to `general-purpose`, which would bypass tool-whitelist
    isolation).
  - **INV-8** — subagents may only write files declared in their `@writes`
    block (or under their own `outbox/`); any out-of-bound write is denied.
  - **INV-9** — during a task-swarm run, edits to `tasks.md` must go through
    the `writeback` sub-command; direct `Edit`/`Write` is diffed and rejected
    unless the change is purely checkbox toggles + `> ` annotations.
  - **INV-10** — subagent outbox files must pass a schema check (required
    sections, `STATUS:` line, judgment field); `parse` surfaces a
    `schema-error` which the orchestrator handles by retrying instead of
    advancing on garbage.
  - Hook implementation: `scripts/task_swarm_guard.py` + extensions in
    `scripts/spec_guard.py`. Matcher now also covers the `Task` tool.
- **Independent reviewer / validator rounds**: `--reviewer-rounds N` (default
  **1**) and `--validator-rounds N` (default **3**). Rationale —
  reviewer is an LLM reading code (subjective, prone to corrective spirals);
  validator runs commands (objective failure signal, deserves more retries).
  `--max-rounds N` remains as a fallback default for both.
- **Reviewer P0 evidence tags**: every P0 finding must carry one of
  `[req:x.y]` / `[security]` / `[contract]`. Untagged P0s are auto-downgraded
  to `advisory_p0` (logged for audit, but do not count toward `p0_count` and
  do not trigger a coder fix round). If every P0 in a review is untagged, the
  judgment flips from `p0` to `approved` and the stage converges directly.
  This stops cheap-prose reviewers from forcing infinite fix loops.
- Help-output (`/spec -h`) now surfaces `--freeform` and `--strict`.

### Changed

- Reviewer "exit fix loop" behavior is now **advisory**, not hard-block.
  When a reviewer round reports the same P0s as the previous round, the
  orchestrator records the loop signal and stops the stage with a
  `failed` mark via the same loop-detection path as validator, instead
  of a separate hard-stop branch. Keeps loop handling symmetric across
  roles.
- `references/task-swarm.md` rewritten as a **design spec** (why the
  state machine exists, how iron rules are upheld) — the runtime model
  reads `commands/task-swarm.md` for the actual 7-step protocol.
- `agents/task-swarm-reviewer.md` updated to require P0 evidence tags
  and the loop-detection self-report convention.
- `agents/task-swarm-coder.md` clarified the fix-round contract (only
  touch P0-listed locations or validator-fail repair guidance; no scope
  creep).

### Fixed

- Long-context drift on round counting: the main model no longer
  computes round numbers or convergence in prompt — all of that lives
  in `task_swarm_state.py` and is checked by tests.
- Outbox format drift: `task_swarm_outbox.py` validates against a
  schema instead of relying on the main model to "eyeball" review.md /
  validation.md.

### Tests

- 6 new test files (`test_task_swarm_cli.py`, `test_task_swarm_guard.py`,
  `test_task_swarm_hook_integration.py`, `test_task_swarm_outbox.py`,
  `test_task_swarm_parse_md.py`, `test_task_swarm_prompt.py`,
  `test_task_swarm_state.py`). Total suite now **135 tests**.

### Migration

No user action required — `/task-swarm`, `/spec`, `/continue`, `/status`,
`/end` slash commands are unchanged; subagent names are unchanged;
`~/.specode/` state schema is unchanged. The orchestrator protocol is
**internal** to the plugin: the main model loads it from
`commands/task-swarm.md` automatically on plugin update.

If you had pinned `0.2.0`, the only behavior change on bumping to
`0.3.0` is that reviewer P0s without `[req:x.y]` / `[security]` /
`[contract]` evidence tags will no longer trigger coder fix rounds —
they become advisory. Audit logs continue to record them.

## 0.2.0 (2026-05-17)

### Renamed (breaking-ish — see Migration)

- Plugin renamed: **`spec-mode` → `specode`**. All identifiers follow:
  - plugin / skill name (manifests, frontmatter, namespace `specode:*`)
  - directory tree (`plugins/specode/`, `skills/specode/`)
  - env vars (`SPEC_MODE_ROOT` → `SPECODE_ROOT`, `SPEC_MODE_GUARD` → `SPECODE_GUARD`)
  - runtime state (`~/.spec-mode/` → `~/.specode/`,
    `~/.config/spec-mode/` → `~/.config/specode/`)
  - vault index file (`.active-spec-mode.json` → `.active-specode.json`)
- The slash command `/spec` itself is unchanged (it was never `/spec-mode`).

### Added

- **Task-Swarm Mode**: a third option in the "任务执行" selector that delegates
  task execution to specialized subagents shipped with the plugin
  (`specode:task-swarm-coder`, `-reviewer`, `-validator`, `-planner`).
- Reviewer and validator subagents are spawned with **no Edit/Write tools** —
  enforces anti-self-approval at the tool layer (not just prompt-level).
- Stage-aggregated dispatch: one coder per top-level tasks.md stage (covering
  all its leaf subtasks), one reviewer per stage, validator reuses the
  built-in "检查点" tasks. Cuts subagent count from naive `N×3` to roughly
  `N` for a spec with `N` stages.
- **Convergence loop per stage**: each stage runs `coder → reviewer → validator`
  in a loop. Reviewer must classify findings as P0 (blocking) / P1 / P2; any
  P0 triggers a focused coder fix round + re-review. Validator fail triggers
  another coder fix round + mandatory re-review + re-validate. Default
  `--max-rounds 3` per loop; reviewer/validator self-report "same finding as
  previous round" to short-circuit infinite loops. tasks.md `[x]` is only
  written when both loops converge cleanly; intermediate rounds only append
  `> 第 N 轮…` progress notes under the affected subtask.
- New command `/task-swarm <spec-dir>/tasks.md` for manual triggering of
  task-swarm mode outside the standard selector flow.
- New plugin subdirectory `agents/` carrying the 4 task-swarm subagents.
  The host CLI auto-registers these (namespaced as `specode:task-swarm-*`).
- New references:
  - `references/task-swarm.md` — full protocol (single authority for editing
    behavior, subagent contract, write-back rules, loop semantics, iron-rule
    interaction)
  - `references/task-swarm-example.md` — sample tasks.md showing stage
    layout, `@swarm:*` labels, and expected subagent count
- New `migrate-from-spec-mode.sh` — one-shot migration script for users
  upgrading from 0.1.0; handles state dirs, vault index file rename,
  stale plugin install detection, and `SPEC_MODE_*` env var detection.
  Supports `--dry-run`.
- README: new `Uninstall` section (en + zh) covering plugin uninstall,
  marketplace removal, optional state cleanup, cache GC behavior, and
  temporary disable/enable.
- CONTRIBUTING: new `Release` section documenting semver bump rules
  (with examples), pre-release checklist, cutting a release, re-tagging
  caveats, and post-release verification.

### Changed

- `references/prompts.md` task execution selector now lists 4 options
  (added `用 task-swarm 多 agent 并发` before `暂不 coding`).
- `references/workflow.md` §7.1 documents the task-swarm delegation flow
  and how it co-exists with lock / INV-4 / phase-gate iron rules.
- `references/help-output.md` now lists `/task-swarm` in the cheat sheet.
- `SKILL.md` References section links the two new task-swarm docs.
- README (en + zh): new Task-Swarm Mode section.

### Fixed

- **P0 — subagent_type must be plugin-prefixed**: dispatching with the
  bare name `Task(subagent_type="task-swarm-coder", ...)` is rejected by
  the host CLI with `"Agent type not found"`. All 13 references in
  `commands/task-swarm.md` and `references/task-swarm.md` now use the
  fully-qualified `specode:task-swarm-coder` (and `-reviewer` /
  `-validator` / `-planner`). The `agents/*.md` frontmatter `name` is
  intentionally left as the bare form — the plugin loader applies the
  namespace automatically. Without this fix, task-swarm mode would have
  failed on the first Task dispatch.
- Doc improvements (from LLM-perspective review of `task-swarm.md`):
  - role mapping table gains a `职责` column documenting what each role
    actually does, plus a note that `planner` is rarely needed in the
    specode + task-swarm flow (specode tasks phase already does splitting)
  - 5-tier precedence rules for `@swarm:*` subtask labels (`skip` always
    wins > `full` > `coder-only` > heuristic); conflicts → INFO log;
    unknown tag → WARN log
  - typed pseudocode signatures in the 4e/4f convergence loops:
    `@dataclass ReviewResult` / `ValidationResult`, typed `fork_reviewer`
    / `fork_coder_fix_round` / `fork_validator` / `fork_reviewer_quick_check`

### Migration

Users upgrading from 0.1.0:

```sh
./migrate-from-spec-mode.sh --dry-run    # preview
./migrate-from-spec-mode.sh              # apply
claude plugin marketplace remove spec-mode
claude plugin marketplace add https://github.com/qxbyte/specode
claude plugin install specode@specode
```

If you had `SPEC_MODE_ROOT` / `SPEC_MODE_GUARD` exported in your shell rc,
rename them to `SPECODE_ROOT` / `SPECODE_GUARD`. The migration script
detects and prints reminders for these.

## 0.1.0 (2026-05-15)

### Phase 1 — bootstrap

- Initial plugin skeleton.
- `plugin.json` consumed by the host CLI's plugin loader.
- `hooks/hooks.json` wiring SessionStart / UserPromptSubmit / PreToolUse /
  PostToolUse / Stop / SessionEnd → `scripts/spec_guard.py`.
- `scripts/spec_guard.py`: dispatch entry, audit-log every event, all
  handlers return ok. Supports `SPECODE_GUARD=off` global bypass.

### Phase 2 — state layer + injection + short-circuit

- `scripts/spec_state.py`: read-only probe against existing
  `.active-specode.json` / per-spec `.config.json`. Owns
  `~/.specode/{sessions,.any-active}`. CLI: status / sync-sentinel /
  demo-activate / demo-deactivate.
- `spec_guard.py`: SessionStart writes the host-session record;
  UserPromptSubmit injects a status block via
  `hookSpecificOutput.additionalContext` when a spec is active; other
  handlers fast-exit when no active spec.
- `hooks/hooks.json`: shell short-circuit on `$HOME/.specode/.any-active`
  for the non-session hooks so Python doesn't even start when idle.

### Phase 3 — Code-Doc Sync Guard (CDSG)

- `scripts/spec_sync.py`: tasks_files extraction (FILE: lines + Affected
  Files section + glob), path classification (spec-doc / project-code /
  outside), `.sync-ledger.json` + per-turn ledger, legality checks. CLI:
  status / freeform / extract.
- `spec_guard.py`:
  - `UserPromptSubmit` refreshes turn_id, re-extracts tasks_files, mirrors
    freeformMode into ledger, extends the injected status block (mode /
    turn / tasks_files count).
  - `PreToolUse` enforces INV-1 (Code-Doc Sync) on project-code edits.
  - `PostToolUse` appends changes to ledger turn_code/doc_changes.
  - `Stop` enforces INV-2 (turn conservation) and INV-4 (acceptance-
    checklist follow-mode). Resets turn on pass.
- `SKILL.md`: three new dispatch entries for `--freeform` / `--strict` /
  `--sync-status`.

Hard-decision compliance (design doc §13):
- 1A: freeform does NOT exempt INV-2.
- 2A: `implementation-log.md` counts as a doc change for INV-2.

### Phase 4 — INV-3 + INV-6

- `spec_sync.py`: `check_verify_lock` (delegates to `spec_session._verify`;
  denies on `evicted`, allows on `ok`/`not_held`/`stale_lock` for backward
  compat); `check_phase_gate` against `PHASES_FORBID_CODE = {intake,
  requirements, bugfix, design, tasks}`.
- `spec_guard.py` `PreToolUse`:
  - spec-doc edits → INV-3 verify-lock first.
  - project-code edits → INV-6 phase gate (absolute; freeform does NOT
    exempt), then INV-1.
  - outside edits → pass through.

### Phase 5 — CodeBuddy static adapter

- `hooks/hooks.json`: env var fallback to
  `${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}`.
- `adapters/codebuddy/README.md`: open items + suggested wrapper script if
  CodeBuddy doesn't honor `${VAR:-fallback}` expansion inside hook command
  strings.

### Phase 6 — tests + docs + stub

- `tests/` with 19 pytest cases (unit + integration). Covers all six
  invariants, freeform behavior, glob matching, lock states, phase gate
  matrix.
- `README.md` rewritten as full reference (rules, architecture, install,
  usage, performance, bypass, CodeBuddy note, tests).
- `CONTRIBUTING.md`: stdlib-only runtime rule, test conventions.
