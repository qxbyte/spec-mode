#!/usr/bin/env python3
from __future__ import annotations

import argparse
import curses
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


def choose_numbered(title: str, options: list[Option], default: int) -> int:
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
    try:
        value = input(prompt).strip()
    except EOFError:
        # stdin is not a real terminal (e.g. Claude Code Bash tool).
        # This is an EXPECTED path under CLI coding agents: print the option block
        # plus a machine-readable sentinel on stdout, and exit 0.
        # The agent must relay the stdout block to the user verbatim and end the turn.
        sys.stdout.write("\n")
        sys.stdout.write("[spec-mode:non-interactive] 选项已就绪：请把上方选项原样转发给用户，并在对话中等待编号回复。\n")
        sys.stdout.write("[spec-mode:non-interactive] AWAITING_USER_CHOICE\n")
        sys.stdout.flush()
        raise SystemExit(0)
    if not value and default >= 0:
        return default
    if value.isdigit():
        selected = int(value) - 1
        if 0 <= selected < len(options):
            return selected
    print("Invalid selection.", file=sys.stderr)
    return choose_numbered(title, options, default)


def choose_curses(title: str, options: list[Option], default: int) -> int:
    def run(stdscr: curses.window) -> int:
        curses.curs_set(0)
        current = max(default, 0)
        while True:
            stdscr.clear()
            stdscr.addstr(0, 0, title, curses.A_BOLD)
            stdscr.addstr(1, 0, "Use ↑/↓ or number keys, then Enter.")
            row = 3
            for index, option in enumerate(options):
                active = index == current
                attr = curses.A_REVERSE if active else curses.A_NORMAL
                marker = "›" if active else " "
                suffix = "  Recommended" if option.recommended else ""
                stdscr.addstr(row, 0, f"{marker} {index + 1}. {option.label}{suffix}", attr)
                row += 1
                if option.description:
                    stdscr.addstr(row, 5, option.description)
                    row += 1
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                current = (current - 1) % len(options)
            elif key in (curses.KEY_DOWN, ord("j")):
                current = (current + 1) % len(options)
            elif key in (curses.KEY_ENTER, 10, 13):
                return current
            elif ord("1") <= key <= ord(str(min(len(options), 9))):
                current = key - ord("1")
                return current

    return curses.wrapper(run)


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive selector for spec-mode confirmations.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--option", action="append", required=True, help="label::description::recommended")
    parser.add_argument("--json", action="store_true", help="Print selected option as JSON.")
    parser.add_argument("--default-index", type=int, help="1-based default index.")
    parser.add_argument("--no-curses", action="store_true", help="Force numbered prompt mode.")
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

    if not sys.stdin.isatty() or not sys.stdout.isatty() or args.no_curses:
        selected = choose_numbered(args.title, options, default)
    else:
        selected = choose_curses(args.title, options, default)
    print_result(selected, options[selected], args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

