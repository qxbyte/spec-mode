#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import spec_session
from spec_session import TASK_RE, task_section


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _placeholder_checklist_rows(text: str) -> list[str]:
    """Return template placeholder rows still present in acceptance-checklist."""
    findings: list[str] = []
    placeholder_terms = ("核心能力", "异常输入", "回归行为", "_agent 待填充_")
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        if any(term in line for term in placeholder_terms):
            findings.append(line.strip())
    return findings


def lint(spec_dir: Path) -> list[str]:
    errors: list[str] = []
    warnings: list[str] = []
    config_data: dict = {}

    req = spec_dir / "requirements.md"
    bug = spec_dir / "bugfix.md"
    design = spec_dir / "design.md"
    tasks = spec_dir / "tasks.md"
    acceptance = spec_dir / "acceptance-checklist.md"
    config = spec_dir / ".config.json"

    if req.exists() and bug.exists():
        errors.append("Spec should not contain both requirements.md and bugfix.md.")
    if not req.exists() and not bug.exists():
        errors.append("Missing requirements.md or bugfix.md.")
    if not design.exists():
        errors.append("Missing design.md.")
    if not tasks.exists():
        errors.append("Missing tasks.md.")
    if not acceptance.exists():
        errors.append("Missing acceptance-checklist.md.")
    if not config.exists():
        warnings.append("Missing .config.json.")
    else:
        try:
            config_data = json.loads(config.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f".config.json is invalid JSON: {exc}")
            config_data = {}
        if config_data:
            document_root = Path(config_data.get("documentRoot") or spec_dir.parent).expanduser().resolve()
            try:
                spec_session.ensure_within_root(spec_dir, document_root)
            except SystemExit as exc:
                errors.append(str(exc))
            if not config_data.get("specId"):
                errors.append(".config.json is missing specId.")
            sessions = config_data.get("sessions")
            if sessions is not None and not isinstance(sessions, dict):
                errors.append(".config.json sessions must be an object keyed by session id.")
            current_phase = config_data.get("currentPhase")
            if current_phase and current_phase not in spec_session.PHASES:
                errors.append(f".config.json currentPhase is invalid: {current_phase}")
            session_status = config_data.get("sessionStatus")
            if session_status and session_status not in {"active", "ended"}:
                errors.append(f".config.json sessionStatus is invalid: {session_status}")
            if config_data.get("persistentMode") and session_status == "ended":
                warnings.append(".config.json persistentMode is true but the current session is ended.")
            if session_status == "ended" and not config_data.get("endedAt"):
                warnings.append(".config.json ended session has no endedAt timestamp.")
            if sessions:
                for session_id, session in sessions.items():
                    status = session.get("status")
                    phase = session.get("currentPhase")
                    if status not in {"active", "ended"}:
                        errors.append(f"Session {session_id} has invalid status: {status}")
                    if phase not in spec_session.PHASES:
                        errors.append(f"Session {session_id} has invalid currentPhase: {phase}")

            # Lock field structural check
            lock = config_data.get("lock")
            if lock is not None:
                if not isinstance(lock, dict):
                    errors.append(".config.json lock must be an object or null.")
                else:
                    for key in ("sessionId", "acquiredAt", "lastHeartbeatAt"):
                        if not lock.get(key):
                            errors.append(f".config.json lock is missing required field: {key}")
                    # Stale lock advisory
                    last_hb = lock.get("lastHeartbeatAt") or lock.get("acquiredAt")
                    try:
                        ts = datetime.fromisoformat(last_hb)
                        elapsed = (datetime.now(timezone.utc) - ts).total_seconds()
                        if elapsed > spec_session.LOCK_STALE_SECONDS:
                            warnings.append(
                                f".config.json lock has been stale for {int(elapsed)}s "
                                f"(threshold {spec_session.LOCK_STALE_SECONDS}s); next acquire will reclaim it."
                            )
                    except (TypeError, ValueError):
                        pass
            evicted = config_data.get("evictedSessions")
            if evicted is not None and not isinstance(evicted, list):
                errors.append(".config.json evictedSessions must be a list.")

    first_doc = bug if bug.exists() else req
    if first_doc and first_doc.exists():
        text = read(first_doc)
        if "SHALL" not in text:
            warnings.append(f"{first_doc.name} has no EARS-style SHALL criteria.")
        placeholder_markers = ["待补充", "[问题]", "[需求", "[触发条件]", "[期望行为]"]
        if any(marker in text for marker in placeholder_markers):
            warnings.append(f"{first_doc.name} still contains template placeholder markers.")

    if design.exists():
        text = read(design)
        for heading in ["## 概述", "## 架构", "## 测试策略"]:
            if heading not in text:
                warnings.append(f"design.md is missing {heading}.")

    if tasks.exists():
        text = read(tasks)
        section = task_section(text)
        task_matches = list(TASK_RE.finditer(section))
        if not task_matches:
            errors.append("tasks.md has no checkbox tasks.")
        if config.exists():
            active_task_exists = any(match.group(1) == "~" for match in task_matches)
            if active_task_exists and config_data.get("currentPhase") not in {"implementation", "acceptance"}:
                warnings.append("tasks.md has in-progress tasks but currentPhase is not implementation or acceptance.")
        if "验证：" not in section and "Validation:" not in section:
            warnings.append("tasks.md does not contain validation notes.")
        if "_需求：" not in section and "Requirements:" not in section and "Behavior:" not in section:
            warnings.append("tasks.md does not contain requirement traceability.")

    if acceptance.exists():
        text = read(acceptance)
        for heading in ["## 前置条件", "## 验收步骤", "## 验收结论"]:
            if heading not in text:
                warnings.append(f"acceptance-checklist.md is missing {heading}.")
        for column in ["操作步骤", "预期结果", "实际结果", "结论"]:
            if column not in text:
                warnings.append(f"acceptance-checklist.md is missing checklist column: {column}.")
        placeholder_rows = _placeholder_checklist_rows(text)
        if placeholder_rows:
            warnings.append(
                f"acceptance-checklist.md still contains {len(placeholder_rows)} placeholder row(s); "
                "agent must replace them with rows derived from requirements.md SHALL statements."
            )
        # Mtime follow-mode check (P0-2): checklist should be no older than the
        # source requirement document.
        if first_doc and first_doc.exists():
            checklist_ts = acceptance.stat().st_mtime
            req_ts = first_doc.stat().st_mtime
            if checklist_ts < req_ts - 1:
                warnings.append(
                    f"acceptance-checklist.md is older than {first_doc.name}. "
                    "Per follow-mode rule, regenerate it in the same turn as the requirements update."
                )

    return [f"ERROR: {item}" for item in errors] + [f"WARNING: {item}" for item in warnings]


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint a spec-mode folder.")
    parser.add_argument("spec_dir", type=Path)
    args = parser.parse_args()
    messages = lint(args.spec_dir)
    if messages:
        print("\n".join(messages))
    else:
        print("Spec lint passed.")
    return 1 if any(msg.startswith("ERROR:") for msg in messages) else 0


if __name__ == "__main__":
    raise SystemExit(main())
