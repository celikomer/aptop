"""Command-line entry point for aptop."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from aptop import __version__
from aptop.collector import Collector, default_state_path
from aptop.formatting import render_plain


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only local terminal monitor for Apex/XDMA accelerator hosts."
    )
    parser.add_argument(
        "--device",
        action="append",
        default=[],
        metavar="XDMA",
        help="monitor an explicit device such as xdma0; repeat for two cards",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=default_state_path(),
        help="optional aptop-workload/v1 JSON state file",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="sampling interval in seconds (minimum 0.25)",
    )
    parser.add_argument("--once", action="store_true", help="print one plain snapshot and exit")
    parser.add_argument("--json", action="store_true", help="emit JSON; implies --once")
    parser.add_argument(
        "--history",
        action="store_true",
        help="include completed runtime mappings in plain output",
    )
    parser.add_argument("--version", action="version", version=f"aptop {__version__}")
    return parser


def _sample_for_output(collector: Collector) -> dict:
    collector.snapshot()
    time.sleep(0.1)
    return collector.snapshot()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.interval < 0.25:
        raise SystemExit("--interval must be at least 0.25 seconds")
    try:
        collector = Collector(tuple(args.device), args.state_file)
        if args.once or args.json:
            snapshot = _sample_for_output(collector)
            if args.json:
                print(json.dumps(snapshot, indent=2, sort_keys=True))
            else:
                print(render_plain(snapshot, show_history=args.history))
            return 0
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print("aptop: interactive mode requires a TTY; use --once", file=sys.stderr)
            return 2
        initial = collector.snapshot()
    except (OSError, ValueError) as exc:
        print(f"aptop: {exc}", file=sys.stderr)
        return 2

    from aptop.tui import AptopApp

    try:
        AptopApp(collector, initial, interval=args.interval).run()
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
