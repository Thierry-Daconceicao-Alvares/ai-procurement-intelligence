"""Command line.

python -m procurement_intel build [--only STEP ...]   build the warehouse and marts
python -m procurement_intel evaluate                 measure the detectors on
                                                     injected errors (after a build)
"""

from __future__ import annotations

import argparse
import logging

from .config import Settings
from .pipeline import STEPS, build, evaluate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m procurement_intel")
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build", help="build the warehouse, models and marts")
    build_parser.add_argument(
        "--only", nargs="+", choices=[step.name for step in STEPS], help="run these steps only"
    )
    commands.add_parser("evaluate", help="measure the amount detectors on injected errors")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.command == "build":
        build(Settings.from_env(), args.only)
    elif args.command == "evaluate":
        evaluate(Settings.from_env())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
