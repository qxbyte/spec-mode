<p align="right"><strong>English</strong> | <a href="./README.zh-CN.md">中文</a></p>

# specode

Specification-driven workflow plugin for **Claude Code** and **CodeBuddy**.

Load-bearing workflow rules are enforced by Claude Code hooks — deterministic
shell commands the harness runs — rather than relying on the model to
remember and obey instructions in context.

## What it enforces

Once a spec is active, these invariants are **harness-enforced**:

| ID | Rule | Hook |
|---|---|---|
| **INV-1** | Editing a source file requires either that file being listed in `tasks.md` / `## Affected Files`, or a same-turn edit to `design.md` / `tasks.md` / `bugfix.md`, or `freeform` mode | `PreToolUse` |
| **INV-2** | A turn that touched source code must touch at least one spec document before stop | `Stop` |
| **INV-3** | Spec-doc writes are rejected if the session was evicted by another window | `PreToolUse` |
| **INV-4** | Edits to `requirements.md` / `bugfix.md` require a same-turn update of `tasks.md` (its `## 测试要点` section, derived from the changed SHALL statements) | `Stop` |
| **INV-5** | Each user turn injects a status block (`spec / phase / lock / turn`) into the model's context | `UserPromptSubmit` |
| **INV-6** | Source code edits are forbidden in pre-implementation phases (intake / requirements / bugfix / design / tasks) | `PreToolUse` |

INV-1 and INV-2 form the **Code-Doc Sync Guard (CDSG)**.

## Architecture

```
.claude-plugin/marketplace.json   ← single-plugin marketplace manifest
plugins/specode/
  .claude-plugin/plugin.json      ← plugin manifest
  hooks/hooks.json                ← 6 event handlers with shell short-circuit on sentinel
  hooks/hooks-probe.json          ← diagnostic probe (swap in for re-verification)
  skills/specode/               ← skill content (SKILL.md + references)
  commands/                       ← /spec, /continue, /status, /end, /task-swarm
  agents/                         ← task-swarm-{coder,reviewer,validator,planner}
  scripts/
    spec_guard.py                 ← hook entry; dispatches to handlers; audit log
    spec_state.py                 ← read-only state probe + sentinel + Claude-session record
    spec_sync.py                  ← INV-1/2/3/4/6 logic; ledger; phase gate; glob matcher
    spec_session.py               ← lock + phase + active-pointer model
    spec_init.py / spec_lint.py / spec_status.py / spec_choice.py / spec_vault.py
  tests/                          ← 19 pytest cases (unit + integration)
```

## Install

### From GitHub (recommended)

```sh
# Claude Code
claude plugin marketplace add https://github.com/qxbyte/specode
claude plugin install specode@specode

# CodeBuddy (verified on 2.97.1)
codebuddy plugin marketplace add https://github.com/qxbyte/specode
codebuddy plugin install specode@specode
```

Both harnesses clone the marketplace, locate the plugin under
`plugins/specode/`, and auto-load `hooks/`, `skills/`, `commands/`.
Updates land via `claude plugin update specode` or `claude plugin
marketplace update specode`.

### One-shot session (Claude Code only)

```sh
claude --plugin-url https://github.com/qxbyte/specode/archive/refs/heads/main.zip
```

Loads the plugin for the current session only; nothing persists.

### Local development

```sh
git clone https://github.com/qxbyte/specode.git
claude    --plugin-dir ./specode/plugins/specode
codebuddy --plugin-dir ./specode/plugins/specode
```

Once loaded:

```
/help                              # list /specode:* commands
/reload-plugins                    # after editing plugin files
```

Hook activity logs to `~/.specode/audit/<date>.log` (UTC).

Optional **local telemetry** of workflow events (spec lifecycle, INV
violations, task-swarm rounds) — disabled by default, enable with
`SPECODE_TELEMETRY=on`. Events go to a single `~/.specode/telemetry.jsonl`
(append-only, no remote upload, no daily rotation — grep-friendly). Run
`python3 scripts/spec_state.py telemetry-summary` for a local aggregate.

### Uninstall

```sh
# 1. Uninstall the plugin first
claude plugin uninstall specode@specode

# 2. Then remove the marketplace
claude plugin marketplace remove specode

# 3. (optional) Remove user-level runtime state — NOT touched by step 1
rm -rf ~/.specode ~/.config/specode
# also vault-side index if you want a fully clean slate:
#   find <obsidian-vault> -name '.active-specode.json' -delete
```

Notes:
- **Order matters**: uninstall the plugin *before* the marketplace, otherwise
  Claude Code reports an orphaned plugin on next start.
- `claude plugin uninstall` only removes the install record; the plugin cache
  under `~/.claude/plugins/cache/specode/` is garbage-collected ~7 days after
  it becomes orphaned. To reclaim disk immediately: `rm -rf ~/.claude/plugins/cache/specode/`.
- `~/.specode/` and `~/.config/specode/` are *user* state (audit logs, sessions,
  obsidianRoot config) and are deliberately **not** removed by the uninstall
  commands — you keep your spec history across reinstalls. Delete them manually
  if you want a clean slate.
- To temporarily disable without uninstalling: `claude plugin disable specode@specode`
  (and `enable` to bring it back).

## Task-Swarm Mode (multi-agent acceleration)

After tasks are confirmed, the "task execution" selector offers a third option
`用 task-swarm 多 agent 并发`. Selecting it delegates execution to **task-swarm**:
the specode session stays as orchestrator (lock, ledger, tasks.md writes),
but actual coding is fanned out to dedicated subagents — one **coder**
subagent per top-level stage, one **reviewer** subagent per stage, and the
existing "检查点" tasks become **validator** subagents.

Reviewer and validator subagents are spawned **without Edit/Write tools** —
they physically cannot modify code. This is the anti-self-approval guarantee:
the agent that wrote the code is never the agent that reviews or accepts it.

For a spec with 5 stages, 20 leaf tasks, and 5 checkpoint tasks, this dispatches
**15 subagents** rather than the naive 60 (1:3 expansion) — and that's just for the
**initial pass**. Each stage runs a `coder → reviewer → validator` loop with up to
3 rounds: reviewer P0 findings trigger a focused coder fix round (no scope creep,
only the listed P0s), then re-review; validator fail triggers another coder fix
round + re-review + re-validate. A stage's `[x]` is only written when reviewer
shows 0 P0s **and** validator passes. Default `--max-rounds 3`; reviewer and
validator each detect "same P0 / same failure as last round" to short-circuit
infinite loops.

→ Protocol: `plugins/specode/skills/specode/references/task-swarm.md`
→ Example tasks.md: `plugins/specode/skills/specode/references/task-swarm-example.md`
→ Manual entry: `/task-swarm <spec-dir>/tasks.md`

## Usage

Inside a Claude Code session with the plugin loaded:

```
/specode:spec --persist <requirement>     # start persistent spec session
/specode:continue [slug]                  # resume / switch
/specode:status                           # show current session
/specode:end                              # end persistent session

/specode:spec --freeform                  # relax INV-1 (INV-2 still enforced)
/specode:spec --strict                    # restore INV-1
/specode:spec --sync-status               # ledger / pending sync / last violation
```

Once a spec is active:

- Every user prompt is augmented with a `specode active` status block
  identifying the spec, phase, lock state, turn id, and freeform mode.
- Edits to project source files outside `tasks.md` are blocked unless a
  same-turn doc change preceded them (INV-1).
- Stopping a turn that touched code without touching docs fails until the
  model adds a `design.md` / `tasks.md` / `implementation-log.md` entry
  (INV-2).
- `requirements.md` / `bugfix.md` edits force a same-turn update to
  `tasks.md` (the `## 测试要点` section, INV-4).
- Code edits during `intake` / `requirements` / `bugfix` / `design` / `tasks`
  phases are absolutely refused — freeform does NOT exempt INV-6.

## Asymmetry note

INV-2 is **unidirectional**: source-code change ⇒ doc change required, but
doc-only edits (typo fixes, wording tweaks) do NOT require a code change.
`implementation-log.md` counts as a doc change to satisfy INV-2 cheaply —
`spec_lint.py` reports a soft WARNING for log entries shorter than 30 chars
or that don't reference any actual code file (the *cosmetic-doc* concern).

## Performance

| Hook | Wall-clock budget |
|---|---|
| `SessionStart` / `SessionEnd` | always runs Python; <500ms |
| `UserPromptSubmit` | only runs Python when `~/.specode/.any-active` sentinel exists; <80ms |
| `PreToolUse` / `PostToolUse` / `Stop` | same shell short-circuit; <100ms when running |

When no spec is active, the shell `[ ! -e ~/.specode/.any-active ]` check
exits before any Python startup → effectively free.

## CodeBuddy support

Verified on CodeBuddy 2.97.1: same `hooks/hooks.json` and
`scripts/spec_guard.py` run unmodified. CodeBuddy ships a Claude Code
2.1.142 agent under the hood and injects both `CLAUDE_PLUGIN_ROOT` and
`CODEBUDDY_PLUGIN_ROOT`, so the integration is byte-for-byte compatible.

## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for the runtime stdlib-only
rule, hook safety contract, and test conventions.

## License

MIT
