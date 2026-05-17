#!/usr/bin/env bash
# specode 从 spec-mode 改名而来的一次性迁移脚本。
#
# 迁移以下用户运行时状态：
#   ~/.spec-mode/                  → ~/.specode/
#   ~/.config/spec-mode/           → ~/.config/specode/
#   <vault>/.active-spec-mode.json → <vault>/.active-specode.json
#   <vault>/spec-in/.active-spec-mode.json → ...active-specode.json
#
# 并提示用户修改 shell 配置里的 SPEC_MODE_ROOT 等环境变量。
#
# 用法:
#   ./migrate-from-spec-mode.sh             # 实际迁移
#   ./migrate-from-spec-mode.sh --dry-run   # 只看会做什么

set -euo pipefail

DRY=0
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY=1
fi

if [[ -t 1 ]]; then
    G="\033[32m"; Y="\033[33m"; R="\033[31m"; B="\033[1m"; D="\033[2m"; N="\033[0m"
else
    G=""; Y=""; R=""; B=""; D=""; N=""
fi

log()  { printf "%b\n" "${B}>${N} $*"; }
ok()   { printf "%b\n" "${G}✔${N} $*"; }
warn() { printf "%b\n" "${Y}!${N} $*"; }
skip() { printf "%b\n" "${D}·${N} $*"; }

run() {
    if (( DRY )); then
        printf "  ${D}(dry-run)${N} %s\n" "$*"
    else
        eval "$@"
    fi
}

move_if_exists() {
    local from="$1" to="$2"
    if [[ ! -e "$from" ]]; then
        skip "skip   $from (不存在)"
        return
    fi
    if [[ -e "$to" ]]; then
        warn "存在  $to (目标已存在)"
        warn "      请手动检查并合并: $from -> $to"
        return
    fi
    run "mkdir -p '$(dirname "$to")'"
    run "mv '$from' '$to'"
    ok "迁移  $from → $to"
}

log "specode 迁移脚本 (mode=$([ $DRY -eq 1 ] && echo dry-run || echo apply))"
echo

# ---------- 1. 用户级状态目录 ----------
log "1. 用户级状态目录"
move_if_exists "$HOME/.spec-mode" "$HOME/.specode"
move_if_exists "$HOME/.config/spec-mode" "$HOME/.config/specode"
echo

# ---------- 2. Vault 内 active 索引文件 ----------
log "2. Vault 内 active 索引文件"

# 找 obsidianRoot
OBS_ROOT=""
if [[ -f "$HOME/.config/specode/config.json" ]]; then
    OBS_ROOT="$(python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.config/specode/config.json'))); print(d.get('obsidianRoot','') or d.get('documentRoot',''))" 2>/dev/null || true)"
fi
# 也试老配置目录（迁移前的）
if [[ -z "$OBS_ROOT" && -f "$HOME/.config/spec-mode/config.json" ]]; then
    OBS_ROOT="$(python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.config/spec-mode/config.json'))); print(d.get('obsidianRoot','') or d.get('documentRoot',''))" 2>/dev/null || true)"
fi

if [[ -z "$OBS_ROOT" ]]; then
    warn "找不到 obsidianRoot 配置，跳过 vault 内 .active-spec-mode.json 迁移"
    warn "如有需要请手动: find <vault> -name '.active-spec-mode.json' -exec rename ..."
else
    log "  obsidianRoot: $OBS_ROOT"
    # vault 根下 / spec-in 下都找
    while IFS= read -r -d '' f; do
        target="${f%/.active-spec-mode.json}/.active-specode.json"
        move_if_exists "$f" "$target"
    done < <(find "$OBS_ROOT" -maxdepth 6 -name ".active-spec-mode.json" -print0 2>/dev/null)
fi
echo

# ---------- 3. 项目内 .claude-plugin marketplace 引用 ----------
log "3. 检查 Claude Code / CodeBuddy 已安装 plugin"

CC_PLUGIN_PATH="$HOME/.claude/plugins/spec-mode"
if [[ -d "$CC_PLUGIN_PATH" ]]; then
    warn "发现 Claude Code 已安装老 plugin: $CC_PLUGIN_PATH"
    warn "  建议: claude plugin uninstall spec-mode 然后重新装 specode"
fi
CB_PLUGIN_PATH="$HOME/.codebuddy/plugins/spec-mode"
if [[ -d "$CB_PLUGIN_PATH" ]]; then
    warn "发现 CodeBuddy 已安装老 plugin: $CB_PLUGIN_PATH"
    warn "  建议: codebuddy plugin uninstall spec-mode 然后重新装 specode"
fi
echo

# ---------- 4. 环境变量提示 ----------
log "4. 环境变量改名提示 (脚本无法替你改 shell 配置)"
ENV_FOUND=0
for var in SPEC_MODE_ROOT SPEC_MODE_GUARD; do
    if printenv "$var" > /dev/null 2>&1; then
        new_var="${var/SPEC_MODE_/SPECODE_}"
        cur="$(printenv "$var")"
        warn "检测到 $var=$cur"
        warn "  请改为: export $new_var=$cur"
        ENV_FOUND=1
    fi
done
if (( ENV_FOUND == 0 )); then
    ok "未检测到 SPEC_MODE_* 环境变量 (无需手动改)"
fi
echo

# ---------- 5. 结束 ----------
if (( DRY )); then
    log "dry-run 完成。确认无误后跑 ./migrate-from-spec-mode.sh 实际执行。"
else
    ok "迁移完成"
    echo
    echo "下一步:"
    echo "  - 重新装 plugin: claude plugin install specode@specode"
    echo "  - 检查 shell rc (.bashrc/.zshrc) 里 SPEC_MODE_* 改成 SPECODE_*"
    echo "  - 跑 /specode:status 验证"
fi
