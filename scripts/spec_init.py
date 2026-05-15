#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import spec_session
import spec_vault


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "assets" / "templates"


SLUG_INVALID = re.compile(r"[^a-z0-9-]+")


def normalize_slug(value: str) -> str:
    """Format-normalize a slug. Does not infer semantics from Chinese; agent must
    pass a semantically meaningful English slug via --name."""
    value = value.strip().lower()
    value = SLUG_INVALID.sub("-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:64]


def resolve_document_root(root: str | None) -> tuple[Path, str]:
    """Three-tier resolution: --root → SPEC_MODE_ROOT/config → Obsidian.

    Returns (resolved_root, source_tag). On total failure raises SystemExit with
    a guidance message and a JSON error code on stderr for agent consumption.
    """
    if root:
        return Path(root).expanduser().resolve(), "explicit"
    vault_root, source = spec_vault.resolve_spec_root()
    if vault_root is not None:
        return vault_root, source
    raise SystemExit(json.dumps({
        "error": "no_spec_root",
        "message": (
            "未检测到 Obsidian vault，且未配置 spec 根目录。请选择以下方式之一：\n"
            "  1. 安装 Obsidian 后重试（推荐）\n"
            "  2. /spec --set-vault <vault路径>\n"
            "  3. /spec --set-root <自定义目录>"
        ),
    }, ensure_ascii=False))


def read_source(args: argparse.Namespace) -> str:
    chunks: list[str] = []
    if args.source_file:
        chunks.append(Path(args.source_file).expanduser().read_text(encoding="utf-8"))
    if args.source_text:
        chunks.append(args.source_text)
    if not chunks:
        chunks.append("New spec initialized without a source requirement.")
    return "\n\n".join(chunks).strip()


def render(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def write_if_missing(path: Path, content: str, force: bool) -> bool:
    if path.exists() and not force:
        return False
    path.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize a Kiro-style spec-mode document folder.")
    parser.add_argument("--root", help="Document management root. The script creates <root>/<name>/.")
    parser.add_argument("--name", required=True,
                        help="Semantic slug (lowercase, hyphen-separated). The agent must compute and pass this; "
                             "the script does not infer slugs from Chinese.")
    parser.add_argument("--requirement-name", help="Display name for the spec. Defaults to --name.")
    parser.add_argument("--source-text", help="Requirement text, usually the text after /spec.")
    parser.add_argument("--source-file", help="Path to a requirement source document.")
    parser.add_argument("--workflow", choices=["requirements-first", "design-first", "bugfix"], default="requirements-first")
    parser.add_argument("--spec-type", choices=["feature", "bugfix"], default="feature")
    parser.add_argument("--persistent", action="store_true", help="Bind this spec to an active persistent session.")
    parser.add_argument("--session", help="Window/thread/session id for persistent mode.")
    parser.add_argument("--agent", help="Agent name recorded into lock metadata when --persistent.")
    parser.add_argument(
        "--current-phase",
        choices=sorted(spec_session.PHASES - {"ended"}),
        default="intake",
        help="Initial phase for persistent mode.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing generated documents.")
    args = parser.parse_args()

    slug = normalize_slug(args.name)
    if not slug:
        print(json.dumps({
            "error": "invalid_name",
            "message": "--name 必须是合法 slug（小写字母/数字/连字符），由 agent 根据需求语义生成。",
        }, ensure_ascii=False), file=sys.stderr)
        return 2

    name = (args.requirement_name or args.name).strip()
    spec_type = "bugfix" if args.workflow == "bugfix" else args.spec_type

    source = read_source(args)
    document_root, root_source = resolve_document_root(args.root)
    spec_dir = document_root / slug
    spec_dir.mkdir(parents=True, exist_ok=True)

    summary = source
    if len(summary) > 1200:
        summary = summary[:1200].rstrip() + "\n\n[Source truncated in seed document. Read the source file for full context.]"

    values = {
        "name": name,
        "slug": slug,
        "summary": summary,
        "workflow": args.workflow,
        "spec_type": "Bugfix" if spec_type == "bugfix" else "Feature",
    }

    created: list[str] = []
    first_doc = "bugfix.md" if spec_type == "bugfix" or args.workflow == "bugfix" else "requirements.md"
    for template_name, output_name in [
        (first_doc, first_doc),
        ("design.md", "design.md"),
        ("tasks.md", "tasks.md"),
        ("acceptance-checklist.md", "acceptance-checklist.md"),
    ]:
        template = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
        target = spec_dir / output_name
        if write_if_missing(target, render(template, values), args.force):
            created.append(str(target))

    config = {
        "specId": str(uuid.uuid4()),
        "workflowType": args.workflow,
        "specType": spec_type,
        "documentRoot": str(document_root),
        "requirementName": name,
        "slug": slug,
        "sourceFile": str(Path(args.source_file).expanduser().resolve()) if args.source_file else None,
        "createdBy": "spec-mode",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "persistentMode": False,
        "sessionStatus": None,
        "currentSessionId": None,
        "currentPhase": None,
        "lastActivityAt": None,
        "endedAt": None,
        "endedReason": None,
        "sessions": {},
        "lock": None,
        "evictedSessions": [],
        "iterationRound": 0,
        "iterationHistory": [],
    }
    config_path = spec_dir / ".config.json"
    if write_if_missing(config_path, json.dumps(config, ensure_ascii=False, indent=2) + "\n", args.force):
        created.append(str(config_path))

    session: dict[str, object] | None = None
    if args.persistent:
        current_config = json.loads(config_path.read_text(encoding="utf-8"))
        current_config.setdefault("lock", None)
        current_config.setdefault("evictedSessions", [])
        session_id = spec_session.normalize_session_id(args.session)
        # Acquire lock before binding the session. New specs are unlocked, so
        # this should never raise LockHeld; we use force=False on purpose.
        spec_session._acquire(spec_dir, session_id, force=False, agent=args.agent)
        current_config = json.loads(config_path.read_text(encoding="utf-8"))
        current_config = spec_session.update_config_session(
            spec_dir,
            current_config,
            session_id,
            "active",
            args.current_phase,
        )
        active = spec_session.load_active(document_root)
        active["sessions"][session_id] = spec_session.entry_for(
            spec_dir,
            current_config,
            session_id,
        )
        spec_session.save_active(document_root, active)
        session = {
            "sessionId": session_id,
            "status": "active",
            "currentPhase": args.current_phase,
            "activeFile": str(spec_session.active_path(document_root)),
        }

    print(json.dumps({
        "specDir": str(spec_dir),
        "documentRoot": str(document_root),
        "documentRootSource": root_source,
        "created": created,
        "session": session,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
