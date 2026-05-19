# Contributing

> **0.6.0 note**: the runtime is rebuilt on top of the 0.5.0 skeleton.
> Six core scripts (`spec_vault.py` / `spec_init.py` / `spec_session.py` /
> `spec_lint.py` / `spec_status.py` + `run.sh`/`run.cmd`), four advisory
> hooks (SessionStart / UserPromptSubmit / Stop / SessionEnd), the
> `spec-writer` agent, SKILL.md + 6 references, and 75 pytest tests are
> shipped. **INV-1 ~ INV-11 and `spec_choice.py` remain removed.** Hooks
> never `exit 2`; they only inject `additionalContext`.

## Runtime is stdlib-only

Any runtime code added back under `plugins/specode/scripts/` should use only
the Python standard library. Plugin users install via `--plugin-dir`; they
don't run `pip install -r requirements.txt`. Pulling third-party packages in
either silently breaks for users without them or forces a heavier install
path.

Tests under `tests/` MAY use `pytest` (it's a dev dependency, not runtime).

## Test conventions

Run the suite from the repo root:

```sh
python3 -m pytest plugins/specode/tests/ -v
```

75 tests cover: 3-tier vault resolution, spec scaffolding with rollback,
business lock state machine, four hooks across mode matrix, SELECTOR_PROMPTS
snapshot, lint rules, and an end-to-end SessionStart → /spec → /end →
SessionEnd event chain.

When adding behavior, prefer:

- Unit tests that call the CLI script through `subprocess.run` (the
  scripts are CLIs, not importable modules).
- Use `tmp_path` + `monkeypatch.setenv('HOME', tmp_path)` to keep tests
  isolated from real `~/.specode/`.
- For hook tests, feed stdin payloads matching the host CLI hook
  schema and assert against the JSON `additionalContext`.

## Hook safety contract

Every hook handler in `spec_session.py` MUST:

1. Catch all exceptions internally and return 0 (the `@_safe_hook`
   decorator does this).
2. **Never `exit 2`.** All v0.6 hooks are advisory only. If you need
   to influence the model, inject `additionalContext` JSON to stdout
   and still `exit 0`.
3. Honour `SPECODE_GUARD=off` for global bypass — return early with
   no output and no state writes.
4. Detect non-TTY stdin (hook payload arrives via pipe). On TTY, the
   script must not block; `_read_stdin_payload()` already handles
   this.

## Performance budget (guideline)

| Hook | Budget |
|---|---|
| `SessionStart` / `SessionEnd` | <500 ms |
| `UserPromptSubmit` | <80 ms (fires every user turn — keep it cheap) |
| `PreToolUse` / `PostToolUse` | <100 ms |
| `Stop` | <300 ms (runs once per turn) |

If a change crosses these budgets, profile first; don't accept the regression.

## Release

Public release procedure for plugin maintainers.

### Version manifests (must agree)

Two manifests carry `version`. They MUST match or the plugin tag tooling
(`claude plugin tag` / equivalent) refuses to operate:

- `plugins/specode/.claude-plugin/plugin.json` → `"version": "X.Y.Z"`
- `.claude-plugin/marketplace.json` → `plugins[0].version: "X.Y.Z"`

### Picking the next version (semver)

"API" = the slash command set, agent names, and any persisted-state schema
that future runtime code introduces.

| Bump | When | Examples |
| --- | --- | --- |
| **major** | A user feels a breaking change after a plugin update | rename a slash command; remove an agent; rename a hook event |
| **minor** | Backwards-compatible new capability | new slash command; new agent; new optional label |
| **patch** | Bug fix / docs / internal refactor with no surface change | fix a typo in a prompt; clarify a reference; CI-only |

When in doubt, bump higher.

### Cutting a release

```sh
# 1. Bump both manifests to the new version
$EDITOR plugins/specode/.claude-plugin/plugin.json
$EDITOR .claude-plugin/marketplace.json

# 2. Land CHANGELOG.md: rename `## Unreleased` → `## X.Y.Z (YYYY-MM-DD)`,
#    then add a fresh empty `## Unreleased` above it for the next cycle
$EDITOR CHANGELOG.md

# 3. Commit + push
git commit -am "Bump to X.Y.Z: <summary>"
git push

# 4. Dry-run the tag first
claude plugin tag --dry-run plugins/specode

# 5. Create + push the annotated tag
claude plugin tag plugins/specode --push
```

Tag format: `specode--v{version}` (annotated, message `specode {version}`).
The plugin is **not** packaged into a tarball or registry artifact — host
CLIs fetch the marketplace manifest directly from GitHub and resolve plugins
by git tag. **Pushing the tag IS the release.**

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
claude plugin marketplace update specode
claude plugin install specode@specode         # or `update`
claude plugin list | grep specode             # confirm new version
```

CodeBuddy users follow the same procedure substituting `codebuddy`.
