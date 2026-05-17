# Code-Doc Sync Guard (CDSG).
#
# Implements INV-1 (PreToolUse legality), INV-2 (Stop turn conservation), and
# INV-4 (Stop requirements↔tasks follow-mode) by maintaining a per-spec ledger
# at <spec-dir>/.sync-ledger.json that tracks code/doc changes within each turn.
#
# Concepts:
#   - turn:        one user → assistant → stop cycle (refreshed at UserPromptSubmit)
#   - D (docs):    files under <spec-dir>/, *.md (requirements/design/tasks/
#                  bugfix/implementation-log)
#   - C (code):    files under project_root/ but NOT under <spec-dir>/
#   - tasks_files: file paths explicitly listed in tasks.md or design.md
#                  "Affected Files" section, plus glob expansions
#
# Sync rule (INV-1): editing f ∈ C is legal iff
#   (a) f matches tasks_files, OR
#   (b) some d ∈ D was modified earlier in the same turn, OR
#   (c) freeform mode is on.
#
# Turn conservation (INV-2): a turn that touched any f ∈ C must also touch at
# least one d ∈ D before Stop. Freeform mode does NOT exempt INV-2 (per design
# decision 1A). implementation-log.md counts as a doc change (decision 2A).
#
# Tasks follow-mode (INV-4): a turn that touched requirements.md or bugfix.md
# must also touch tasks.md before Stop — the `## 测试要点` section of tasks.md
# is the tester-facing derivation of the SHALL statements and must stay in
# lockstep with requirements/bugfix changes.

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

# --- constants -------------------------------------------------------------

SPEC_DOC_FILENAMES = {
    "requirements.md",
    "bugfix.md",
    "design.md",
    "tasks.md",
    "implementation-log.md",
}

LEDGER_FILENAME = ".sync-ledger.json"
LEDGER_VERSION = 1

# Tasks-file extraction patterns.
TASKS_FILE_LINE_RE = re.compile(
    r"^\s*-\s*\[[ x~*\-]\]\s+(?:FILE|file|文件)[:：]\s*(?P<path>\S.*?)\s*$",
    re.MULTILINE,
)
# Inline `- [ ] ... (src/foo.py)` is *not* matched — too noisy. Use FILE: prefix.

AFFECTED_FILES_SECTION_RE = re.compile(
    r"^#{2,3}\s+(?:Affected Files|影响文件)\s*$",
    re.MULTILINE,
)
BULLET_PATH_RE = re.compile(r"^\s*[-*]\s+`?(?P<path>[^`\s]+)`?\s*$")


# --- time ------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- json io ---------------------------------------------------------------

def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


# --- tasks_files extraction -------------------------------------------------

def extract_tasks_files(spec_dir: Path) -> list[str]:
    """Pull every path mentioned in tasks.md / design.md.

    Sources:
      1. tasks.md lines like `- [ ] FILE: src/foo.py`  (also matches 文件/file)
      2. tasks.md / design.md `## Affected Files` (or 影响文件) section bullets

    Glob patterns (`*`, `**`) are preserved as-is; callers expand against the
    project root.
    """
    paths: list[str] = []
    for fname in ("tasks.md", "design.md"):
        f = spec_dir / fname
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in TASKS_FILE_LINE_RE.finditer(text):
            paths.append(m.group("path"))
        paths.extend(_extract_affected_files_section(text))
    # Dedup while preserving order.
    seen: set[str] = set()
    out = []
    for p in paths:
        p = p.strip().strip("`").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _extract_affected_files_section(text: str) -> list[str]:
    lines = text.splitlines()
    out: list[str] = []
    in_section = False
    for line in lines:
        if AFFECTED_FILES_SECTION_RE.match(line):
            in_section = True
            continue
        if in_section:
            if line.startswith("#"):
                # Reached the next heading.
                in_section = False
                continue
            m = BULLET_PATH_RE.match(line)
            if m:
                out.append(m.group("path"))
    return out


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Convert a path glob (with `**` recursive support) into a regex.

    Rules:
      - `**/`  → zero or more path components (incl. none)
      - `**`   → any sequence (incl. `/`)
      - `*`    → any sequence not containing `/`
      - `?`    → any single non-`/` character
      - `[...]` → character class (passed through)
      - other  → literal
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                if i + 2 < n and pattern[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = pattern.find("]", i)
            if j == -1:
                out.append(re.escape(c))
                i += 1
            else:
                out.append(pattern[i : j + 1])
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def matches_tasks_files(target: Path, tasks_files: Iterable[str], project_root: Path) -> bool:
    """Check if `target` matches any entry in tasks_files (literal or glob)."""
    try:
        rel = target.resolve().relative_to(project_root.resolve())
    except ValueError:
        return False
    rel_str = str(rel)
    for entry in tasks_files:
        if not entry:
            continue
        if "*" in entry or "?" in entry or "[" in entry:
            if _glob_to_regex(entry).match(rel_str):
                return True
        else:
            if rel_str == entry or rel_str == entry.lstrip("./"):
                return True
    return False


# --- path classification ----------------------------------------------------

def is_under(target: Path, root: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def classify_path(target: Path, spec_dir: Path, project_root: Optional[Path]) -> str:
    """Return one of: 'spec-doc', 'project-code', 'outside'."""
    if is_under(target, spec_dir):
        return "spec-doc"
    if project_root and is_under(target, project_root):
        return "project-code"
    return "outside"


# --- ledger ----------------------------------------------------------------

def ledger_path(spec_dir: Path) -> Path:
    return spec_dir / LEDGER_FILENAME


def read_ledger(spec_dir: Path) -> dict:
    data = _read_json(ledger_path(spec_dir))
    if not data:
        return _new_ledger(spec_dir)
    data.setdefault("version", LEDGER_VERSION)
    data.setdefault("turn_code_changes", [])
    data.setdefault("turn_doc_changes", [])
    return data


def write_ledger(spec_dir: Path, data: dict) -> None:
    _write_json(ledger_path(spec_dir), data)


def _new_ledger(spec_dir: Path) -> dict:
    return {
        "version": LEDGER_VERSION,
        "spec_slug": spec_dir.name,
        "project_root": None,
        "freeform_mode": False,
        "tasks_files": [],
        "turn_id": None,
        "turn_started_at": None,
        "turn_code_changes": [],
        "turn_doc_changes": [],
        "last_violation": None,
        "updated_at": None,
    }


def start_new_turn(ledger: dict, project_root: Optional[Path], tasks_files: list[str]) -> None:
    ledger["turn_id"] = f"turn-{uuid.uuid4().hex[:8]}"
    ledger["turn_started_at"] = _now()
    ledger["turn_code_changes"] = []
    ledger["turn_doc_changes"] = []
    if project_root:
        ledger["project_root"] = str(project_root)
    ledger["tasks_files"] = tasks_files
    ledger["updated_at"] = _now()


def append_change(ledger: dict, kind: str, file_path: str, tool: str) -> None:
    bucket = "turn_code_changes" if kind == "code" else "turn_doc_changes"
    ledger[bucket].append({
        "file": file_path,
        "tool": tool,
        "at": _now(),
    })
    ledger["updated_at"] = _now()


def reset_turn(ledger: dict) -> None:
    ledger["turn_code_changes"] = []
    ledger["turn_doc_changes"] = []
    ledger["updated_at"] = _now()


def has_doc_change_this_turn(ledger: dict) -> bool:
    return bool(ledger.get("turn_doc_changes"))


def has_code_change_this_turn(ledger: dict) -> bool:
    return bool(ledger.get("turn_code_changes"))


def doc_changes_files(ledger: dict) -> set[str]:
    return {Path(c["file"]).name for c in ledger.get("turn_doc_changes", [])}


# --- decisions --------------------------------------------------------------

INV1_MESSAGE_TMPL = (
    "代码-文档同步守卫 (INV-1): 文件 {target} 不在 tasks.md 列表内, "
    "且本回合未先修改 design/tasks/bugfix 文档。\n"
    "两条合法路径任选其一:\n"
    "  (A) 先在 design.md 或 tasks.md 中加上该文件的修改说明, 再写代码;\n"
    "  (B) 如确需自由实现, 运行 /spec --freeform 切换至自由阶段。"
)

INV3_EVICTED_MSG = (
    "代码-文档同步守卫 (INV-3): 当前 session 已被另一个窗口抢占 (evicted)。\n"
    "本会话对 spec '{slug}' 的写权限已被回收。请运行 /continue {slug} 重新取回 lock, "
    "或停止编辑以避免覆盖另一会话的工作。"
)

INV6_MESSAGE_TMPL = (
    "代码-文档同步守卫 (INV-6): 当前阶段 [{phase}] 禁止修改源码。\n"
    "必须先完成 requirements/design/tasks 阶段确认 (推进到 implementation 后才能写代码)。\n"
    "phase gate 是绝对规则, freeform 模式也不豁免。"
)

# Phases that forbid source-code edits. Spec-doc edits within these phases
# are still allowed (and in fact expected).
PHASES_FORBID_CODE = {"intake", "requirements", "bugfix", "design", "tasks"}

INV2_MESSAGE_TMPL = (
    "代码-文档同步守卫 (INV-2): 本回合修改了 {n} 个源码文件但未同步任何 spec 文档。\n"
    "必须在本回合内完成下列之一:\n"
    "  - 在 design.md 中描述代码变更的设计意图;\n"
    "  - 在 tasks.md 中把对应 task 标记为完成 (或补充新 task);\n"
    "  - 在 implementation-log.md 中追加本次变更的纪要。"
)

INV4_MESSAGE_TMPL = (
    "代码-文档同步守卫 (INV-4): 本回合修改了 {req} 但未同步 tasks.md 的"
    " 测试要点。\n需求/bug 行为变化时, 测试人员需要的验证场景也必须同 turn 跟进 ——"
    " 请在 tasks.md `## 测试要点` 节增删对应行。"
)

def check_pre_edit(
    target: Path,
    spec_dir: Path,
    project_root: Optional[Path],
    ledger: dict,
) -> tuple[str, str]:
    """Return ('ok' | 'deny', message). Spec-doc edits bypass INV-1 (handled elsewhere)."""
    cls = classify_path(target, spec_dir, project_root)
    if cls != "project-code":
        return "ok", ""
    # project-code branch
    tasks_files = ledger.get("tasks_files") or []
    if project_root and matches_tasks_files(target, tasks_files, project_root):
        return "ok", ""
    if has_doc_change_this_turn(ledger):
        return "ok", ""
    if ledger.get("freeform_mode"):
        return "ok", ""
    return "deny", INV1_MESSAGE_TMPL.format(target=str(target))


def check_phase_gate(current_phase: str) -> tuple[str, str]:
    """INV-6: forbid source-code edits in pre-implementation phases.

    freeform mode does NOT exempt INV-6 (phase gate is absolute).
    """
    if current_phase in PHASES_FORBID_CODE:
        return "deny", INV6_MESSAGE_TMPL.format(phase=current_phase)
    return "ok", ""


def check_verify_lock(spec_dir: Path, session_id: str, slug: str) -> tuple[str, str]:
    """INV-3: lock-ownership check before spec-doc write.

    Imports spec_session lazily (heavy module). Returns deny only on 'evicted';
    other non-ok statuses (not_held / stale_lock) are allowed with a soft signal
    so existing pre-Phase-4 specs without a lock keep working.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import spec_session  # type: ignore
    except Exception as e:
        return "ok", f"verify-lock-import-failed: {e}"

    try:
        result = spec_session._verify(spec_dir, session_id)
    except SystemExit as e:
        # spec_session raises SystemExit for missing config; treat as "no lock model"
        return "ok", f"verify-lock-skipped: {e}"
    except Exception as e:
        return "ok", f"verify-lock-error: {e}"

    status = result.get("status")
    if status == "evicted":
        return "deny", INV3_EVICTED_MSG.format(slug=slug)
    # status in {"ok", "not_held", "stale_lock"} -> allow.
    return "ok", status or ""


def check_stop(ledger: dict) -> list[dict]:
    """Return a list of violation dicts. Empty list = pass."""
    violations: list[dict] = []
    if has_code_change_this_turn(ledger) and not has_doc_change_this_turn(ledger):
        violations.append({
            "id": "INV-2",
            "msg": INV2_MESSAGE_TMPL.format(n=len(ledger.get("turn_code_changes", []))),
        })
    doc_files = doc_changes_files(ledger)
    req_touched = {"requirements.md", "bugfix.md"} & doc_files
    if req_touched and "tasks.md" not in doc_files:
        violations.append({
            "id": "INV-4",
            "msg": INV4_MESSAGE_TMPL.format(req=" + ".join(sorted(req_touched))),
        })
    return violations


# --- CLI -------------------------------------------------------------------

def _resolve_active_spec_dir() -> Optional[Path]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import spec_state  # local import to avoid hard dep at import time
    info = spec_state.find_active_spec(prefer_session_id=os.environ.get("TERM_SESSION_ID"))
    if not info:
        return None
    return Path(info["spec_dir"])


def _spec_config_path(spec_dir: Path) -> Path:
    return spec_dir / ".config.json"


def _read_spec_config(spec_dir: Path) -> dict:
    return _read_json(_spec_config_path(spec_dir)) or {}


def _write_spec_config(spec_dir: Path, data: dict) -> None:
    _write_json(_spec_config_path(spec_dir), data)


def _cmd_status(_args: argparse.Namespace) -> int:
    spec_dir = _resolve_active_spec_dir()
    if not spec_dir:
        print("(no active spec)")
        return 0
    ledger = read_ledger(spec_dir)
    config = _read_spec_config(spec_dir)
    print(f"spec_dir:       {spec_dir}")
    print(f"slug:           {ledger.get('spec_slug')}")
    print(f"project_root:   {ledger.get('project_root')}")
    print(f"freeform:       {bool(config.get('freeformMode'))}")
    print(f"tasks_files:    {len(ledger.get('tasks_files') or [])} entries")
    print(f"turn_id:        {ledger.get('turn_id')}")
    print(f"turn started:   {ledger.get('turn_started_at')}")
    print(f"code changes:   {len(ledger.get('turn_code_changes') or [])}")
    print(f"doc changes:    {len(ledger.get('turn_doc_changes') or [])}")
    print(f"last violation: {ledger.get('last_violation')}")
    return 0


def _cmd_freeform(args: argparse.Namespace) -> int:
    spec_dir = _resolve_active_spec_dir()
    if not spec_dir:
        print("ERR: no active spec", file=sys.stderr)
        return 2
    config = _read_spec_config(spec_dir)
    desired = (args.state == "on")
    config["freeformMode"] = desired
    _write_spec_config(spec_dir, config)
    ledger = read_ledger(spec_dir)
    ledger["freeform_mode"] = desired
    write_ledger(spec_dir, ledger)
    print(f"✓ freeform_mode = {desired} for spec '{spec_dir.name}'")
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    spec_dir = Path(args.spec_dir).expanduser() if args.spec_dir else _resolve_active_spec_dir()
    if not spec_dir:
        print("ERR: no spec_dir", file=sys.stderr)
        return 2
    files = extract_tasks_files(spec_dir)
    print(json.dumps(files, ensure_ascii=False, indent=2))
    return 0


def main(argv) -> int:
    p = argparse.ArgumentParser(prog="spec_sync.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Print ledger summary for active spec")
    sp = sub.add_parser("freeform", help="Toggle freeform mode")
    sp.add_argument("state", choices=["on", "off"])
    sx = sub.add_parser("extract", help="Print tasks_files for a spec")
    sx.add_argument("--spec-dir")
    args = p.parse_args(argv)
    return {
        "status": _cmd_status,
        "freeform": _cmd_freeform,
        "extract": _cmd_extract,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
