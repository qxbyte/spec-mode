# Contributing

Project-level conventions for changes under `plugins/specode/`. Read
this before opening a PR or cutting a release.

## Runtime is stdlib-only

Any runtime code under `plugins/specode/scripts/` MUST use only the
Python standard library. Plugin users install via the host CLI's
`plugin install`; they don't `pip install -r requirements.txt`.
Pulling third-party packages in either silently breaks for users
without them or forces a heavier install path.

Tests under `plugins/specode/tests/` MAY use `pytest` (it's a dev
dependency, not runtime).

## CLI invocation contract

Every script under `plugins/specode/scripts/` is a CLI invoked from
hook commands (`hooks.json`) or directly by the main agent. **All
invocations MUST go through the `run.sh` wrapper with the full
`$CLAUDE_PLUGIN_ROOT` (fallback `$CODEBUDDY_PLUGIN_ROOT`) path**:

```sh
sh "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/run.sh" \
   "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/<name>.py" \
   <verb> <args...>
```

Why:

- `run.sh` probes `python3 → python → py` so it works on any host
  with Python 3.8+ on PATH.
- Both `CLAUDE_PLUGIN_ROOT` and `CODEBUDDY_PLUGIN_ROOT` are
  platform-injected env vars; the `:-` fallback covers both Claude
  Code and CodeBuddy without forcing the user to pick one.
- Bare `python3 spec_session.py …` calls fail in most cwds because
  the scripts are not on PATH and the agent doesn't know where it
  is. This was observed as a real failure mode pre-0.8.0; see
  `SKILL.md §CLI 调用规约（强制）` for the hard rule.

`hooks/hooks.json` and the `commands/*.md` "立即调用" sections all
use this template — match them when adding new entry points.

## Test conventions

Run the suite from the repo root:

```sh
python3 -m pytest plugins/specode/tests/ -v
```

153 tests cover: 3-tier vault resolution, spec scaffolding with
rollback, business lock state machine, all 7 hooks across the mode
matrix, `SELECTOR_PROMPTS` snapshot, lint rules (3 surviving rules
after the 0.9.0 cleanup), legacy-field migration for `session_id`,
the task-swarm CLI / state machine / outbox parser / writeback / hook,
and an end-to-end SessionStart → /specode:spec → /specode:end →
SessionEnd event chain.

When adding behavior, prefer:

- Unit tests that call the CLI script through `subprocess.run` (the
  scripts are CLIs, not importable modules).
- Use `tmp_path` + `monkeypatch.setenv('HOME', tmp_path)` to keep
  tests isolated from real `~/.specode/`.
- For hook tests, feed stdin payloads matching the host CLI hook
  schema and assert against the JSON `additionalContext`.
- For any persisted schema change (sessions / state.json / lock
  fields), add a "legacy file migration" regression test pinning
  read-side backwards compatibility — see
  `test_read_session_migrates_legacy_claude_session_id` and
  `test_load_migrates_legacy_claude_session_id` as templates.

## Hook safety contract

Every hook handler in `spec_session.py` MUST:

1. Catch all exceptions internally and return 0 (the `@_safe_hook`
   decorator does this).
2. **Never `exit 2`.** All hooks are advisory only. If you need to
   influence the model, inject `additionalContext` JSON to stdout
   and still `exit 0`.
3. Honour `SPECODE_GUARD=off` for global bypass — return early with
   no output and no state writes.
4. Detect non-TTY stdin (hook payload arrives via pipe). On TTY, the
   script must not block; `_read_stdin_payload()` already handles
   this.

## On-disk schema fields

Two schemas the plugin owns:

- `~/.specode/sessions/<session_id>.json` — per-host-session state
- `<spec-dir>/.config.json` — per-spec config + lock field

Conventions:

- New writes use neutral field names (`session_id`, not
  `claude_session_id`; `holder`, not `claude_session_id` for lock
  holders). Avoid host-specific naming in persisted schema.
- Read sites MUST fall back through any historical names before
  giving up — for `session_id` the order is `session_id` →
  `claude_session_id`; for lock holder it's `holder` →
  `session_id` → `claude_session_id`. `read_session()` and
  `StateMachine.load()` auto-migrate on read so the next write
  lands the new key without manual user action.
- Bump **minor** for schema field renames that ship a read-side
  fallback (existing files keep working). Bump **major** if a
  rename breaks reads.

## Debugging with session logs (0.10.0+)

specode 默认收集每个 session 的日志到 `~/.specode/logs/<session_id>.jsonl`，
含 hook 触发、主代理工具调用、CLI 调用、phase / lock 变化。

```sh
# 回放一个 session 的事件流（按时序）
sh "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/run.sh" \
   "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/spec_log.py" \
   replay --session <session_id>

# 查看 logs/ 占用
sh "$CLAUDE_PLUGIN_ROOT/scripts/run.sh" \
   "$CLAUDE_PLUGIN_ROOT/scripts/spec_log.py" status

# 临时关日志
export SPECODE_LOG=off

# 永久关：编辑 ~/.config/specode/config.json 加 "logging": false
```

排查"主代理为什么走偏"类问题时，用 replay 看 hook 时序 + 工具调用顺序，
通常能定位到「该呈现 selector 没呈现」「fork spec-writer 漏了」「Status
字段被越权改」之类的违规点。新增 hook / CLI 子命令时记得在入口加
`_log_event("event_name", payload, session_id)`，便于日后调试。

## Performance budget (guideline)

| Hook | Budget |
|---|---|
| `SessionStart` / `SessionEnd` | <500 ms |
| `UserPromptSubmit` | <80 ms (fires every user turn — keep it cheap) |
| `PreToolUse` / `PostToolUse Task` | <100 ms |
| `Stop` | <300 ms (runs once per turn) |

If a change crosses these budgets, profile first; don't accept the
regression.

## Release

Public release procedure for plugin maintainers.

### Version manifests (must agree)

Two manifests carry `version`. They MUST match or the plugin tag
tooling refuses to operate:

- `plugins/specode/.claude-plugin/plugin.json` → `"version": "X.Y.Z"`
- `.claude-plugin/marketplace.json` → `plugins[0].version: "X.Y.Z"`

### Picking the next version (semver)

"API surface" for semver purposes = the slash command set, agent
names, hook event names, and persisted-state schema fields that
users or future runtime code can observe.

| Bump | When | Examples |
| --- | --- | --- |
| **major** | A user feels a breaking change after a plugin update | rename a slash command; remove an agent; rename a hook event; rename a schema field with no read-side fallback |
| **minor** | Backwards-compatible new capability or evolution | new slash command; new agent; new optional label; schema field rename **with** read-side fallback |
| **patch** | Bug fix / docs / internal refactor with no surface change | fix a typo in a prompt; clarify a reference; CI-only; remove dev-only files from the repo |

When in doubt, bump higher.

### Cutting a release

```sh
# 1. Bump both manifests to the new version
$EDITOR plugins/specode/.claude-plugin/plugin.json
$EDITOR .claude-plugin/marketplace.json

# 2. Land CHANGELOG.md: rename `## Unreleased` → `## X.Y.Z (YYYY-MM-DD)`,
#    then add a fresh empty `## Unreleased` above it for the next cycle
$EDITOR CHANGELOG.md

# 3. Run the test suite one more time
python3 -m pytest plugins/specode/tests/ -q

# 4. Commit + push
git commit -am "Bump to X.Y.Z: <summary>"
git push

# 5. Dry-run the tag first
claude plugin tag --dry-run plugins/specode
# (or codebuddy plugin tag --dry-run plugins/specode — pick whichever
#  host CLI is installed; both wrap the same git operations)

# 6. Create + push the annotated tag
claude plugin tag plugins/specode --push
```

Tag format: `specode--v{version}` (annotated, message
`specode {version}`). The plugin is **not** packaged into a tarball
or registry artifact — host CLIs fetch the marketplace manifest
directly from GitHub and resolve plugins by git tag. **Pushing the
tag IS the release.**

### Re-tagging the same version

Only safe if no user has installed it yet:

```sh
git tag -d specode--vX.Y.Z
git push --delete origin specode--vX.Y.Z
claude plugin tag plugins/specode --push      # re-create
```

Once a release is in user hands, prefer a new patch version.

### Verifying after release

```sh
# Adjust the CLI name for whichever host you use (claude / codebuddy).
claude plugin marketplace update specode
claude plugin install specode@specode         # or `update`
claude plugin list | grep specode             # confirm new version
```

Users on a different host follow the same procedure with their host's
CLI name (`codebuddy plugin …`).
