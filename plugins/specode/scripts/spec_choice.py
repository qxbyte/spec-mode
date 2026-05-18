#!/usr/bin/env python3
"""Stateless selector emitter for specode confirmations.

Design (post-0.4.0): this script is **non-interactive only**. It prints the
title + option block + a machine-readable sentinel and exits 0. The agent
(Claude Code / CodeBuddy main session) is expected to relay the stdout to
the user verbatim and resume work when the user replies with a number.

Why no `input()` / curses anymore: agent Bash tools do not have a real TTY,
and any blocking stdin read leaves the process hanging indefinitely. The
prior TTY-only paths were a hang risk under CodeBuddy's piped stdin (the
process never received EOF). This module now physically cannot block.

The `--no-curses` flag is kept for back-compat but is a no-op.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass


@dataclass
class Option:
    label: str
    description: str = ""
    recommended: bool = False


def parse_option(raw: str) -> Option:
    parts = raw.split("::")
    label = parts[0].strip()
    description = parts[1].strip() if len(parts) > 1 else ""
    flags = {part.strip().lower() for part in parts[2:]}
    return Option(label=label, description=description, recommended="recommended" in flags)


def print_result(index: int, option: Option, as_json: bool) -> None:
    result = {"index": index + 1, "label": option.label, "description": option.description}
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(option.label)


def emit_options(title: str, options: list[Option], default: int) -> None:
    """Print the option block + sentinel. Never reads stdin."""
    print(title)
    for index, option in enumerate(options, start=1):
        suffix = " (Recommended)" if option.recommended else ""
        print(f"{index}. {option.label}{suffix}")
        if option.description:
            print(f"   {option.description}")
    prompt = f"Select 1-{len(options)}"
    if default >= 0:
        prompt += f" [{default + 1}]"
    prompt += ": "
    sys.stdout.write(prompt)
    sys.stdout.write("\n")
    sys.stdout.write("[specode:non-interactive] 选项已就绪：请把上方选项原样转发给用户，并在对话中等待编号回复。\n")
    sys.stdout.write("[specode:non-interactive] AWAITING_USER_CHOICE\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Selector emitter for specode confirmations (non-interactive).")
    parser.add_argument("--title", required=True)
    parser.add_argument("--option", action="append", required=True, help="label::description::recommended")
    parser.add_argument("--json", action="store_true", help="Used with --print-default: emit JSON.")
    parser.add_argument("--default-index", type=int, help="1-based default index.")
    parser.add_argument("--no-curses", action="store_true", help="No-op; retained for back-compat.")
    parser.add_argument("--print-default", action="store_true", help="Print the default selection without prompting.")
    args = parser.parse_args()

    options = [parse_option(raw) for raw in args.option]
    default = (args.default_index - 1) if args.default_index else next(
        (index for index, option in enumerate(options) if option.recommended),
        0,
    )
    if not 0 <= default < len(options):
        default = 0

    if args.print_default:
        print_result(default, options[default], args.json)
        return 0

    emit_options(args.title, options, default)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
