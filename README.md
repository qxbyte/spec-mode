<p align="right"><strong>English</strong> | <a href="./README.zh-CN.md">中文</a></p>

# spec-mode

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
plugins/spec-mode/
  .claude-plugin/plugin.json      ← plugin manifest
  hooks/hooks.json                ← 6 event handlers with shell short-circuit on sentinel
  hooks/hooks-probe.json          ← diagnostic probe (swap in for re-verification)
  skills/spec-mode/               ← skill content (SKILL.md + references)
  commands/                       ← /spec, /continue, /status, /end
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
claude plugin marketplace add https://github.com/qxbyte/spec-mode
claude plugin install spec-mode@spec-mode

# CodeBuddy (verified on 2.97.1)
codebuddy plugin marketplace add https://github.com/qxbyte/spec-mode
codebuddy plugin install spec-mode@spec-mode
```

Both harnesses clone the marketplace, locate the plugin under
`plugins/spec-mode/`, and auto-load `hooks/`, `skills/`, `commands/`.
Updates land via `claude plugin update spec-mode` or `claude plugin
marketplace update spec-mode`.

### One-shot session (Claude Code only)

```sh
claude --plugin-url https://github.com/qxbyte/spec-mode/archive/refs/heads/main.zip
```

Loads the plugin for the current session only; nothing persists.

### Local development

```sh
git clone https://github.com/qxbyte/spec-mode.git
claude    --plugin-dir ./spec-mode/plugins/spec-mode
codebuddy --plugin-dir ./spec-mode/plugins/spec-mode
```

Once loaded:

```
/help                              # list /spec-mode:* commands
/reload-plugins                    # after editing plugin files
```

Hook activity logs to `~/.spec-mode/audit/<date>.log` (UTC).

## Usage

Inside a Claude Code session with the plugin loaded:

```
/spec-mode:spec --persist <requirement>     # start persistent spec session
/spec-mode:continue [slug]                  # resume / switch
/spec-mode:status                           # show current session
/spec-mode:end                              # end persistent session

/spec-mode:spec --freeform                  # relax INV-1 (INV-2 still enforced)
/spec-mode:spec --strict                    # restore INV-1
/spec-mode:spec --sync-status               # ledger / pending sync / last violation
```

Once a spec is active:

- Every user prompt is augmented with a `spec-mode active` status block
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
| `UserPromptSubmit` | only runs Python when `~/.spec-mode/.any-active` sentinel exists; <80ms |
| `PreToolUse` / `PostToolUse` / `Stop` | same shell short-circuit; <100ms when running |

When no spec is active, the shell `[ ! -e ~/.spec-mode/.any-active ]` check
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
