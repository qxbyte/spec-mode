#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


ACTIVE_FILE = ".active-spec-mode.json"
ACTIVE_VERSION = 2
SESSION_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
PHASES = {
    "intake",
    "requirements",
    "bugfix",
    "design",
    "tasks",
    "implementation",
    "acceptance",
    "iteration",
    "ended",
}
TASK_RE = re.compile(r"^\s*-\s*\[( |x|~|\*|-)\]\s+(.+)$", re.MULTILINE)
TASK_LABELS = {" ": "pending", "x": "completed", "~": "in_progress", "*": "optional", "-": "skipped"}

# Document filenames managed by the spec workflow. Used for dynamic column width
# in `command_load` so adding a new document does not silently break alignment.
DOC_FILENAMES = (
    "requirements.md",
    "bugfix.md",
    "design.md",
    "tasks.md",
    "acceptance-checklist.md",
)
DOC_COL_WIDTH = max(len(name) for name in DOC_FILENAMES) + 2

# Lock staleness: a lock whose lastHeartbeatAt is older than this is silently
# reclaimable by another session. Overridable via SPEC_MODE_LOCK_STALE_SECONDS.
LOCK_STALE_SECONDS = int(os.environ.get("SPEC_MODE_LOCK_STALE_SECONDS") or 1800)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def normalize_session_id(raw: str | None) -> str:
    value = raw or os.environ.get("TERM_SESSION_ID") or os.environ.get("SPEC_SESSION_ID") or "default"
    value = SESSION_RE.sub("-", value.strip()).strip("-._")
    return value[:80] or "default"


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


@contextmanager
def _file_lock(target: Path) -> Iterator[None]:
    """Process-level advisory file lock. Cross-platform best-effort.

    Used to guard read-modify-write sequences on .config.json and the active
    pointer file against two parallel `spec_session.py` invocations racing.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(target.suffix + ".lock")
    handle = open(lock_path, "a+")
    locked = False
    try:
        try:
            import fcntl  # type: ignore[import-not-found]
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            locked = True
        except (ImportError, OSError):
            try:
                import msvcrt  # type: ignore[import-not-found]
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                locked = True
            except (ImportError, OSError):
                # Platform without supported locking → proceed unguarded. Atomic
                # rename in write_json still prevents torn writes.
                pass
        yield
    finally:
        if locked:
            try:
                try:
                    import fcntl  # type: ignore[import-not-found]
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except (ImportError, OSError):
                    import msvcrt  # type: ignore[import-not-found]
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        handle.close()


def load_config(spec_dir: Path) -> dict[str, Any]:
    config_path = spec_dir / ".config.json"
    if not config_path.exists():
        raise SystemExit(f"Missing config: {config_path}")
    config = read_json(config_path, {})
    if not config.get("specId"):
        raise SystemExit(f"Missing specId in config: {config_path}")
    config.setdefault("lock", None)
    config.setdefault("evictedSessions", [])
    return config


def save_config(spec_dir: Path, config: dict[str, Any]) -> None:
    """Forced write of .config.json. Any caller that mutated config must persist."""
    write_json(spec_dir / ".config.json", config)


def document_root_for(spec_dir: Path, config: dict[str, Any]) -> Path:
    root = config.get("documentRoot")
    if root:
        return Path(root).expanduser().resolve()
    return spec_dir.resolve().parent


def active_path(document_root: Path) -> Path:
    return document_root.resolve() / ACTIVE_FILE


def ensure_within_root(spec_dir: Path, document_root: Path) -> None:
    spec_resolved = spec_dir.resolve()
    root_resolved = document_root.resolve()
    try:
        spec_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise SystemExit(f"Spec dir is outside document root: {spec_resolved} not under {root_resolved}") from exc


def _migrate_active_v1_to_v2(data: dict[str, Any], document_root: Path) -> dict[str, Any]:
    sessions = data.get("sessions") or {}
    migrated: dict[str, Any] = {}
    for sid, entry in sessions.items():
        slug = entry.get("slug") or (Path(entry["specDir"]).name if entry.get("specDir") else None)
        migrated[sid] = {
            "sessionId": sid,
            "specSlug": slug,
            "specId": entry.get("specId"),
            "status": entry.get("status") or "active",
            "boundAt": entry.get("startedAt") or entry.get("updatedAt"),
            "lastActivityAt": entry.get("updatedAt") or entry.get("lastActivityAt"),
        }
    data["version"] = ACTIVE_VERSION
    data["sessions"] = migrated
    data["documentRoot"] = str(document_root.resolve())
    return data


def load_active(document_root: Path) -> dict[str, Any]:
    path = active_path(document_root)
    data = read_json(path, {})
    if not data:
        return {
            "version": ACTIVE_VERSION,
            "documentRoot": str(document_root.resolve()),
            "updatedAt": None,
            "sessions": {},
        }
    if data.get("version", 1) < ACTIVE_VERSION:
        data = _migrate_active_v1_to_v2(data, document_root)
    data.setdefault("version", ACTIVE_VERSION)
    data.setdefault("sessions", {})
    data["documentRoot"] = str(document_root.resolve())
    return data


def save_active(document_root: Path, data: dict[str, Any]) -> None:
    data["documentRoot"] = str(document_root.resolve())
    data["updatedAt"] = now()
    write_json(active_path(document_root), data)


def active_sessions(config: dict[str, Any]) -> list[str]:
    sessions = config.get("sessions") or {}
    return [
        session_id
        for session_id, session in sessions.items()
        if session.get("status") == "active"
    ]


# ---------------------------------------------------------------------------
# Lock primitives (acquire / release / verify / force_acquire / heartbeat)
# ---------------------------------------------------------------------------


class LockHeld(SystemExit):
    """Raised when acquire() finds the spec is held by a different session."""

    def __init__(self, holder_id: str, last_heartbeat: str | None) -> None:
        self.holder_id = holder_id
        self.last_heartbeat = last_heartbeat
        super().__init__(json.dumps({
            "error": "lock_held",
            "holderSessionId": holder_id,
            "lastHeartbeatAt": last_heartbeat,
        }, ensure_ascii=False))


def _lock_is_stale(lock: dict[str, Any]) -> bool:
    ts = _parse_ts(lock.get("lastHeartbeatAt") or lock.get("acquiredAt"))
    if ts is None:
        return True
    elapsed = (datetime.now(timezone.utc) - ts).total_seconds()
    return elapsed > LOCK_STALE_SECONDS


def _record_eviction(config: dict[str, Any], holder: dict[str, Any], new_session: str, reason: str) -> None:
    config.setdefault("evictedSessions", []).append({
        "sessionId": holder.get("sessionId"),
        "evictedAt": now(),
        "evictedBy": new_session,
        "reason": reason,
    })


def _acquire(spec_dir: Path, session_id: str, *, force: bool, agent: str | None) -> dict[str, Any]:
    config_path = spec_dir / ".config.json"
    with _file_lock(config_path):
        config = load_config(spec_dir)
        lock = config.get("lock") or None
        if lock and lock.get("sessionId") == session_id:
            lock["lastHeartbeatAt"] = now()
            config["lock"] = lock
            save_config(spec_dir, config)
            return {"action": "renewed", "lock": lock, "config": config}
        if lock:
            if _lock_is_stale(lock):
                _record_eviction(config, lock, session_id, "stale")
            elif force:
                _record_eviction(config, lock, session_id, "force_acquire")
            else:
                raise LockHeld(
                    holder_id=lock.get("sessionId", "unknown"),
                    last_heartbeat=lock.get("lastHeartbeatAt"),
                )
        new_lock = {
            "sessionId": session_id,
            "acquiredAt": now(),
            "lastHeartbeatAt": now(),
            "agent": agent or os.environ.get("SPEC_MODE_AGENT") or "unknown",
            "pid": os.getpid(),
        }
        config["lock"] = new_lock
        save_config(spec_dir, config)
        return {"action": "acquired" if not lock else "evicted", "lock": new_lock, "config": config}


def _release(spec_dir: Path, session_id: str) -> dict[str, Any]:
    config_path = spec_dir / ".config.json"
    with _file_lock(config_path):
        config = load_config(spec_dir)
        lock = config.get("lock") or None
        if lock and lock.get("sessionId") == session_id:
            config["lock"] = None
            save_config(spec_dir, config)
            return {"action": "released", "lock": None}
        return {"action": "noop", "lock": lock}


def _heartbeat(spec_dir: Path, session_id: str) -> dict[str, Any]:
    config_path = spec_dir / ".config.json"
    with _file_lock(config_path):
        config = load_config(spec_dir)
        lock = config.get("lock") or None
        if not lock or lock.get("sessionId") != session_id:
            holder = lock.get("sessionId") if lock else None
            raise SystemExit(json.dumps({
                "error": "lock_lost",
                "expectedSessionId": session_id,
                "actualHolder": holder,
            }, ensure_ascii=False))
        lock["lastHeartbeatAt"] = now()
        config["lock"] = lock
        save_config(spec_dir, config)
        return {"action": "heartbeat", "lock": lock}


def _verify(spec_dir: Path, session_id: str) -> dict[str, Any]:
    config = load_config(spec_dir)
    lock = config.get("lock") or None
    evicted = config.get("evictedSessions") or []
    if lock and lock.get("sessionId") == session_id:
        return {"status": "ok", "lock": lock}
    if any(e.get("sessionId") == session_id for e in evicted):
        latest = max(
            (e for e in evicted if e.get("sessionId") == session_id),
            key=lambda e: e.get("evictedAt", ""),
        )
        return {"status": "evicted", "lock": lock, "eviction": latest}
    return {"status": "not_held", "lock": lock}


def command_acquire(args: argparse.Namespace) -> int:
    spec_dir = Path(args.spec_dir).expanduser().resolve()
    session_id = normalize_session_id(args.session)
    result = _acquire(spec_dir, session_id, force=args.force, agent=args.agent)
    print(json.dumps({k: v for k, v in result.items() if k != "config"}, ensure_ascii=False, indent=2))
    return 0


def command_release(args: argparse.Namespace) -> int:
    spec_dir = Path(args.spec_dir).expanduser().resolve()
    session_id = normalize_session_id(args.session)
    result = _release(spec_dir, session_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_heartbeat(args: argparse.Namespace) -> int:
    spec_dir = Path(args.spec_dir).expanduser().resolve()
    session_id = normalize_session_id(args.session)
    result = _heartbeat(spec_dir, session_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_verify(args: argparse.Namespace) -> int:
    spec_dir = Path(args.spec_dir).expanduser().resolve()
    session_id = normalize_session_id(args.session)
    result = _verify(spec_dir, session_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 3


# ---------------------------------------------------------------------------
# Existing session lifecycle (start / continue / status / end / list / load)
# ---------------------------------------------------------------------------


def update_config_session(
    spec_dir: Path,
    config: dict[str, Any],
    session_id: str,
    status: str,
    phase: str,
    reason: str | None = None,
) -> dict[str, Any]:
    timestamp = now()
    sessions = config.setdefault("sessions", {})
    session = sessions.setdefault(session_id, {"startedAt": timestamp})
    session["status"] = status
    session["currentPhase"] = phase
    session["lastActivityAt"] = timestamp
    if status == "active":
        session.setdefault("startedAt", timestamp)
        session["endedAt"] = None
        session["endedReason"] = None
    else:
        session["endedAt"] = timestamp
        session["endedReason"] = reason or "ended"

    config["currentSessionId"] = session_id
    config["sessionStatus"] = status
    config["currentPhase"] = phase
    config["lastActivityAt"] = timestamp
    config["persistentMode"] = bool(active_sessions(config))
    if status != "active" and not config["persistentMode"]:
        config["endedAt"] = timestamp
        config["endedReason"] = reason or "ended"
    else:
        config["endedAt"] = None
        config["endedReason"] = None
    save_config(spec_dir, config)
    return config


def entry_for(spec_dir: Path, config: dict[str, Any], session_id: str) -> dict[str, Any]:
    return {
        "sessionId": session_id,
        "specSlug": config.get("slug") or spec_dir.name,
        "specId": config["specId"],
        "status": "active",
        "boundAt": now(),
        "lastActivityAt": now(),
    }


def resolve_active(document_root: Path, session_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    active = load_active(document_root)
    entry = active.get("sessions", {}).get(session_id)
    if not entry or entry.get("status") != "active":
        raise SystemExit(f"No active spec session '{session_id}' under {document_root}")
    slug = entry.get("specSlug") or entry.get("slug")
    if not slug:
        raise SystemExit(f"Active pointer entry for '{session_id}' has no specSlug")
    spec_dir = (document_root / slug).resolve()
    config = load_config(spec_dir)
    ensure_within_root(spec_dir, document_root)
    if config.get("specId") != entry.get("specId"):
        raise SystemExit(
            f"Active pointer specId mismatch for session '{session_id}'. "
            f"Refusing to continue to avoid cross-spec contamination."
        )
    return spec_dir, config, entry


def _bind_session(spec_dir: Path, config: dict[str, Any], session_id: str, phase: str) -> dict[str, Any]:
    document_root = document_root_for(spec_dir, config)
    ensure_within_root(spec_dir, document_root)
    config = update_config_session(spec_dir, config, session_id, "active", phase)
    active = load_active(document_root)
    active["sessions"][session_id] = entry_for(spec_dir, config, session_id)
    save_active(document_root, active)
    return active["sessions"][session_id]


def command_start(args: argparse.Namespace) -> int:
    session_id = normalize_session_id(args.session)
    spec_dir = Path(args.spec_dir).expanduser().resolve()
    config = load_config(spec_dir)
    document_root = document_root_for(spec_dir, config)
    ensure_within_root(spec_dir, document_root)

    requested_phase = args.phase
    if not requested_phase:
        requested_phase = config.get("currentPhase") or "intake"
    if requested_phase not in PHASES or requested_phase == "ended":
        raise SystemExit(f"Invalid active phase: {requested_phase}")

    if getattr(args, "acquire", True):
        _acquire(spec_dir, session_id, force=getattr(args, "force", False), agent=getattr(args, "agent", None))
        config = load_config(spec_dir)

    entry = _bind_session(spec_dir, config, session_id, requested_phase)
    print(json.dumps({"active": entry, "activeFile": str(active_path(document_root))}, ensure_ascii=False, indent=2))
    return 0


def command_status(args: argparse.Namespace) -> int:
    session_id = normalize_session_id(args.session)
    if args.spec_dir:
        spec_dir = Path(args.spec_dir).expanduser().resolve()
        config = load_config(spec_dir)
        document_root = document_root_for(spec_dir, config)
        ensure_within_root(spec_dir, document_root)
        entry = load_active(document_root).get("sessions", {}).get(session_id)
        if entry and entry.get("specId") != config.get("specId"):
            raise SystemExit(
                f"Active pointer specId mismatch for session '{session_id}'. "
                f"Refusing to report a different spec."
            )
    else:
        if not args.root:
            raise SystemExit("status without spec_dir requires --root")
        document_root = Path(args.root).expanduser().resolve()
        spec_dir, config, entry = resolve_active(document_root, session_id)

    lock = config.get("lock") or None
    result = {
        "sessionId": session_id,
        "specDir": str(spec_dir),
        "specId": config.get("specId"),
        "requirementName": config.get("requirementName"),
        "workflowType": config.get("workflowType"),
        "specType": config.get("specType"),
        "persistentMode": config.get("persistentMode", False),
        "sessionStatus": (config.get("sessions") or {}).get(session_id, {}).get("status", config.get("sessionStatus")),
        "currentPhase": (config.get("sessions") or {}).get(session_id, {}).get("currentPhase", config.get("currentPhase")),
        "iterationRound": config.get("iterationRound"),
        "activeFile": str(active_path(document_root)),
        "activePointer": entry,
        "lock": lock,
        "lockHeldBy": (lock or {}).get("sessionId"),
        "lockOwnedByCurrentSession": bool(lock and lock.get("sessionId") == session_id),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Session: {result['sessionId']}")
        print(f"Spec: {result['requirementName'] or Path(result['specDir']).name}")
        print(f"Path: {result['specDir']}")
        print(f"Status: {result['sessionStatus'] or 'unknown'}")
        print(f"Phase: {result['currentPhase'] or 'unknown'}")
        if result["iterationRound"]:
            print(f"Iteration round: {result['iterationRound']}")
        print(f"Persistent: {str(result['persistentMode']).lower()}")
        if lock:
            owned = "本会话" if result["lockOwnedByCurrentSession"] else f"其他: {result['lockHeldBy']}"
            print(f"Lock: {owned}  (last heartbeat: {lock.get('lastHeartbeatAt')})")
        else:
            print("Lock: 空闲")
        print(f"Active file: {result['activeFile']}")
    return 0


def command_end(args: argparse.Namespace) -> int:
    session_id = normalize_session_id(args.session)
    if args.spec_dir:
        spec_dir = Path(args.spec_dir).expanduser().resolve()
        config = load_config(spec_dir)
        document_root = document_root_for(spec_dir, config)
        ensure_within_root(spec_dir, document_root)
    else:
        if not args.root:
            raise SystemExit("end without spec_dir requires --root")
        document_root = Path(args.root).expanduser().resolve()
        spec_dir, config, _entry = resolve_active(document_root, session_id)

    update_config_session(spec_dir, config, session_id, "ended", "ended", args.reason)
    _release(spec_dir, session_id)
    active = load_active(document_root)
    entry = active.get("sessions", {}).get(session_id)
    if entry:
        if entry.get("specId") and entry.get("specId") != config.get("specId"):
            raise SystemExit(
                f"Active pointer specId mismatch for session '{session_id}'. "
                f"Refusing to end a different spec."
            )
        active["sessions"].pop(session_id, None)
        save_active(document_root, active)

    print(json.dumps({"sessionId": session_id, "specDir": str(spec_dir), "status": "ended"}, ensure_ascii=False, indent=2))
    return 0


def command_list(args: argparse.Namespace) -> int:
    document_root = Path(args.root).expanduser().resolve()
    active = load_active(document_root)
    sessions = active.get("sessions", {})
    if args.json:
        print(json.dumps({"documentRoot": str(document_root), "sessions": sessions}, ensure_ascii=False, indent=2))
    else:
        print(f"Document root: {document_root}")
        if not sessions:
            print("No active spec sessions.")
            return 0
        for session_id, entry in sorted(sessions.items()):
            print(
                f"- {session_id}: {entry.get('specSlug') or entry.get('slug')} "
                f"({entry.get('status')}, lastActivity: {entry.get('lastActivityAt')})"
            )
    return 0


def command_list_specs(args: argparse.Namespace) -> int:
    document_root = Path(args.root).expanduser().resolve()
    if not document_root.exists():
        raise SystemExit(f"Document root does not exist: {document_root}")

    specs: list[dict[str, Any]] = []
    for child in sorted(document_root.iterdir(), key=lambda item: item.name):
        if not child.is_dir():
            continue
        config_path = child / ".config.json"
        if not config_path.exists():
            continue
        try:
            config = load_config(child)
            ensure_within_root(child, document_root)
        except SystemExit as exc:
            specs.append({
                "slug": child.name,
                "specDir": str(child.resolve()),
                "valid": False,
                "error": str(exc),
            })
            continue
        lock = config.get("lock") or None
        specs.append({
            "slug": config.get("slug") or child.name,
            "requirementName": config.get("requirementName"),
            "specDir": str(child.resolve()),
            "specId": config.get("specId"),
            "workflowType": config.get("workflowType"),
            "specType": config.get("specType"),
            "currentPhase": config.get("currentPhase"),
            "sessionStatus": config.get("sessionStatus"),
            "iterationRound": config.get("iterationRound"),
            "lastActivityAt": config.get("lastActivityAt"),
            "lock": lock,
            "lockHeldBy": (lock or {}).get("sessionId"),
            "lockStale": bool(lock and _lock_is_stale(lock)),
            "valid": True,
        })

    if args.json:
        print(json.dumps({"documentRoot": str(document_root), "specs": specs}, ensure_ascii=False, indent=2))
    else:
        print(f"Document root: {document_root}")
        if not specs:
            print("No specs found.")
            return 0
        for spec in specs:
            if not spec.get("valid"):
                print(f"- {spec.get('slug')}: invalid ({spec.get('error')})")
                continue
            lock_state = "空闲"
            if spec["lockHeldBy"]:
                lock_state = f"锁定于 {spec['lockHeldBy']}"
                if spec["lockStale"]:
                    lock_state += "（已过期）"
            iteration = f", iter {spec['iterationRound']}" if spec.get("iterationRound") else ""
            print(
                f"- {spec.get('slug')}: {spec.get('requirementName') or spec.get('slug')} "
                f"({spec.get('currentPhase') or 'unknown'}{iteration}, {lock_state})"
            )
    return 0


# ---------------------------------------------------------------------------
# Document loading (used by /spec-continue context restoration and read-only)
# ---------------------------------------------------------------------------


def task_section(text: str) -> str:
    """Extract the ## 任务 section from tasks.md text, or return whole text."""
    start = text.find("## 任务")
    if start == -1:
        return text
    tail = text[start:]
    end_match = re.search(r"\n##\s+", tail[len("## 任务"):])
    if not end_match:
        return tail
    return tail[: len("## 任务") + end_match.start()]


def _file_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    return {
        "exists": True,
        "modifiedAt": mtime,
        "modifiedTs": path.stat().st_mtime,
        "text": path.read_text(encoding="utf-8"),
    }


def command_load(args: argparse.Namespace) -> int:
    spec_dir = Path(args.spec_dir).expanduser().resolve()
    config = load_config(spec_dir)
    document_root = document_root_for(spec_dir, config)
    ensure_within_root(spec_dir, document_root)

    req_info = _file_info(spec_dir / "requirements.md")
    bug_info = _file_info(spec_dir / "bugfix.md")
    design_info = _file_info(spec_dir / "design.md")
    tasks_info = _file_info(spec_dir / "tasks.md")
    acceptance_info = _file_info(spec_dir / "acceptance-checklist.md")

    req_doc = req_info if req_info["exists"] else bug_info
    req_name = "requirements.md" if req_info["exists"] else "bugfix.md"
    shall_count = 0
    req_open_questions = False
    if req_doc.get("exists"):
        shall_count = req_doc["text"].count("SHALL")
        req_open_questions = "待确认问题" in req_doc["text"]

    design_open_questions = False
    if design_info.get("exists"):
        design_open_questions = "待确认问题" in design_info["text"]

    counts: dict[str, int] = {label: 0 for label in TASK_LABELS.values()}
    counts["total"] = 0
    in_progress: list[str] = []
    if tasks_info.get("exists"):
        section = task_section(tasks_info["text"])
        for match in TASK_RE.finditer(section):
            status_label = TASK_LABELS.get(match.group(1), "pending")
            counts["total"] += 1
            counts[status_label] += 1
            if status_label == "in_progress":
                in_progress.append(match.group(2).strip())

    # Detect checklist staleness vs. requirements/bugfix mtime (P0-2 follow-mode).
    checklist_stale = False
    if acceptance_info.get("exists") and req_doc.get("exists"):
        if acceptance_info["modifiedTs"] < req_doc["modifiedTs"] - 1:  # 1s slack
            checklist_stale = True

    lock = config.get("lock") or None
    session_id = normalize_session_id(getattr(args, "session", None))
    result: dict[str, Any] = {
        "specDir": str(spec_dir),
        "slug": config.get("slug") or spec_dir.name,
        "specId": config.get("specId"),
        "requirementName": config.get("requirementName"),
        "currentPhase": config.get("currentPhase"),
        "iterationRound": config.get("iterationRound"),
        "sessionStatus": config.get("sessionStatus"),
        "currentSessionId": config.get("currentSessionId"),
        "lastActivityAt": config.get("lastActivityAt"),
        "lock": lock,
        "lockHeldBy": (lock or {}).get("sessionId"),
        "lockOwnedByCurrentSession": bool(lock and lock.get("sessionId") == session_id),
        "checklistStale": checklist_stale,
        "documents": {
            req_name: {
                "exists": req_doc.get("exists", False),
                "modifiedAt": req_doc.get("modifiedAt"),
                "shallCount": shall_count,
                "hasOpenQuestions": req_open_questions,
            },
            "design.md": {
                "exists": design_info.get("exists", False),
                "modifiedAt": design_info.get("modifiedAt"),
                "hasOpenQuestions": design_open_questions,
            },
            "tasks.md": {
                "exists": tasks_info.get("exists", False),
                "modifiedAt": tasks_info.get("modifiedAt"),
                "counts": counts,
                "inProgress": in_progress,
            },
            "acceptance-checklist.md": {
                "exists": acceptance_info.get("exists", False),
                "modifiedAt": acceptance_info.get("modifiedAt"),
                "stale": checklist_stale,
            },
        },
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    w = DOC_COL_WIDTH
    slug = result["slug"]
    phase = result["currentPhase"] or "unknown"
    session_label = result["currentSessionId"] or "unknown"
    s_status = result["sessionStatus"] or "unknown"
    print(f"已加载 spec: {slug}")
    print(f"  specId:  {result['specId']}")
    print(f"  phase:   {phase}")
    if result["iterationRound"]:
        print(f"  iteration: 第 {result['iterationRound']} 轮")
    print(f"  session: {session_label} ({s_status})")
    if lock:
        owner = "本会话持有" if result["lockOwnedByCurrentSession"] else f"⚠ 锁定于 {result['lockHeldBy']}"
        print(f"  lock:    {owner}  (last heartbeat: {lock.get('lastHeartbeatAt')})")
    else:
        print("  lock:    空闲")
    print()
    req_d = result["documents"][req_name]
    if req_d["exists"]:
        q = " | 有待确认问题" if req_d["hasOpenQuestions"] else ""
        print(f"  {req_name:<{w}} ← {req_d['shallCount']} 条验收标准{q}  |  修改: {req_d['modifiedAt']}")
    else:
        print(f"  {req_name:<{w}} ← 不存在")
    design_d = result["documents"]["design.md"]
    if design_d["exists"]:
        q = " | 有待确认问题" if design_d["hasOpenQuestions"] else ""
        print(f"  {'design.md':<{w}} ←{q}  |  修改: {design_d['modifiedAt']}")
    else:
        print(f"  {'design.md':<{w}} ← 不存在")
    tasks_d = result["documents"]["tasks.md"]
    if tasks_d["exists"]:
        c = tasks_d["counts"]
        prog = f", 进行中: {', '.join(tasks_d['inProgress'])}" if tasks_d["inProgress"] else ""
        print(f"  {'tasks.md':<{w}} ← {c['completed']}/{c['total']} 已完成, {c['pending']} 待处理{prog}  |  修改: {tasks_d['modifiedAt']}")
    else:
        print(f"  {'tasks.md':<{w}} ← 不存在")
    acceptance_d = result["documents"]["acceptance-checklist.md"]
    if acceptance_d["exists"]:
        stale_tag = " | ⚠ 落后于 requirements.md" if acceptance_d["stale"] else ""
        print(f"  {'acceptance-checklist.md':<{w}} ← 验收操作清单{stale_tag}  |  修改: {acceptance_d['modifiedAt']}")
    else:
        print(f"  {'acceptance-checklist.md':<{w}} ← 不存在")
    return 0


# ---------------------------------------------------------------------------
# Iteration bookkeeping
# ---------------------------------------------------------------------------


def command_iterate(args: argparse.Namespace) -> int:
    """Advance a spec into a new iteration round. Used at /spec-accept moment."""
    spec_dir = Path(args.spec_dir).expanduser().resolve()
    config_path = spec_dir / ".config.json"
    with _file_lock(config_path):
        config = load_config(spec_dir)
        current_round = int(config.get("iterationRound") or 0)
        history = config.setdefault("iterationHistory", [])
        if current_round > 0:
            for entry in reversed(history):
                if entry.get("round") == current_round and "completedAt" not in entry:
                    entry["completedAt"] = now()
                    entry["newReqCount"] = args.new_req_count or 0
                    break
        new_round = current_round + 1
        config["iterationRound"] = new_round
        history.append({
            "round": new_round,
            "startedAt": now(),
            "newReqCount": 0,
        })
        config["currentPhase"] = "iteration"
        save_config(spec_dir, config)
    print(json.dumps({"iterationRound": new_round, "specDir": str(spec_dir)}, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage persistent spec-mode sessions.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    session_help = "Window/thread/session id. Defaults to $TERM_SESSION_ID or 'default'."

    start = subparsers.add_parser("start", help="Bind a session to a spec and mark it active.")
    start.add_argument("spec_dir")
    start.add_argument("--session", help=session_help)
    start.add_argument("--phase", choices=sorted(PHASES - {"ended"}), default="intake")
    start.add_argument("--no-acquire", dest="acquire", action="store_false")
    start.add_argument("--force", action="store_true", help="Force-acquire lock even if held by another session.")
    start.add_argument("--agent", help="Agent name recorded into lock metadata.")
    start.set_defaults(func=command_start, acquire=True)

    cont = subparsers.add_parser("continue", help="Resume or switch the current session to a spec.")
    cont.add_argument("spec_dir")
    cont.add_argument("--session", help=session_help)
    cont.add_argument("--phase", choices=sorted(PHASES - {"ended"}), default=None,
                      help="Override phase. Defaults to .config.json.currentPhase.")
    cont.add_argument("--no-acquire", dest="acquire", action="store_false")
    cont.add_argument("--force", action="store_true", help="Force-acquire lock from another session.")
    cont.add_argument("--agent", help="Agent name recorded into lock metadata.")
    cont.set_defaults(func=command_start, acquire=True)

    status = subparsers.add_parser("status", help="Show session/spec lifecycle status.")
    status.add_argument("spec_dir", nargs="?")
    status.add_argument("--root", help="Document root used when spec_dir is omitted.")
    status.add_argument("--session", help=session_help)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)

    end = subparsers.add_parser("end", help="End the active session without deleting spec documents.")
    end.add_argument("spec_dir", nargs="?")
    end.add_argument("--root", help="Document root used when spec_dir is omitted.")
    end.add_argument("--session", help=session_help)
    end.add_argument("--reason", default="user ended")
    end.set_defaults(func=command_end)

    list_cmd = subparsers.add_parser("list", help="List active sessions under a document root.")
    list_cmd.add_argument("--root", required=True)
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(func=command_list)

    list_specs_cmd = subparsers.add_parser("list-specs", help="List spec folders under a configured document root.")
    list_specs_cmd.add_argument("--root", required=True)
    list_specs_cmd.add_argument("--json", action="store_true")
    list_specs_cmd.set_defaults(func=command_list_specs)

    load_cmd = subparsers.add_parser("load", help="Load and summarize spec documents for context restoration.")
    load_cmd.add_argument("spec_dir")
    load_cmd.add_argument("--session", help=session_help)
    load_cmd.add_argument("--json", action="store_true")
    load_cmd.set_defaults(func=command_load)

    acquire_cmd = subparsers.add_parser("acquire", help="Acquire the spec lock for this session.")
    acquire_cmd.add_argument("spec_dir")
    acquire_cmd.add_argument("--session", help=session_help)
    acquire_cmd.add_argument("--force", action="store_true", help="Force-acquire even if held by another session.")
    acquire_cmd.add_argument("--agent", help="Agent name recorded into lock metadata.")
    acquire_cmd.set_defaults(func=command_acquire)

    release_cmd = subparsers.add_parser("release", help="Release the spec lock if held by this session.")
    release_cmd.add_argument("spec_dir")
    release_cmd.add_argument("--session", help=session_help)
    release_cmd.set_defaults(func=command_release)

    hb_cmd = subparsers.add_parser("heartbeat", help="Refresh lock lastHeartbeatAt; fail if lock lost.")
    hb_cmd.add_argument("spec_dir")
    hb_cmd.add_argument("--session", help=session_help)
    hb_cmd.set_defaults(func=command_heartbeat)

    verify_cmd = subparsers.add_parser("verify-lock", help="Check whether this session still holds the spec lock.")
    verify_cmd.add_argument("spec_dir")
    verify_cmd.add_argument("--session", help=session_help)
    verify_cmd.set_defaults(func=command_verify)

    iter_cmd = subparsers.add_parser("iterate", help="Advance the spec into a new iteration round.")
    iter_cmd.add_argument("spec_dir")
    iter_cmd.add_argument("--new-req-count", type=int, default=0)
    iter_cmd.set_defaults(func=command_iterate)

    args = parser.parse_args()
    try:
        return args.func(args)
    except LockHeld as exc:
        print(str(exc), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
