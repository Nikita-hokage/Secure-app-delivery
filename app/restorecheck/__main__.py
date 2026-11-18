"""Command line interface for synthetic rehearsals and application-owned snapshots."""

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from .audit import STAGES, audit
from .contract import load
from .demo import SCENARIOS, rehearse, write_fixture


def display(report, as_json):
    if as_json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return
    print(f"{report['status'].upper()}  {report['total'] - report['failed']}/{report['total']} checks passed")
    for item in report["checks"]:
        if item["code"] != "OK":
            print("{stage:14} {table}/{role}: {code}  expected={expected} actual={actual} "
                  "removed={removed} added={added}".format(**item))


def export_fixture(destination, scenario):
    destination = destination.absolute()
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        stage = Path(temp)
        write_fixture(stage, scenario)
        destination.mkdir()  # Reserve a new directory before exposing the finished files.
        moved = []
        try:
            for source in sorted(stage.iterdir()):
                target = destination / source.name
                source.rename(target)
                moved.append(target)
        except OSError:
            for target in moved:
                target.unlink(missing_ok=True)
            destination.rmdir()
            raise


def main(argv=None):
    cli = argparse.ArgumentParser(prog="restorecheck", description=__doc__)
    sub = cli.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="rehearse restore and replay on synthetic data")
    fixture = sub.add_parser("fixtures", help="export a synthetic drill to a new directory")
    for parser in (demo, fixture):
        parser.add_argument("--scenario", choices=SCENARIOS, default="clean")
    fixture.add_argument("directory", type=Path)
    check = sub.add_parser("audit", help="inspect three standalone SQLite snapshots, read-only")
    check.add_argument("--contract", required=True, type=Path)
    for stage in STAGES:
        check.add_argument("--" + stage, required=True, type=Path)
    for parser in (demo, check):
        parser.add_argument("--json", action="store_true", help="emit a versioned JSON report")
    args = cli.parse_args(argv)
    try:
        if args.command == "fixtures":
            export_fixture(args.directory, args.scenario)
            print(args.directory)
            return 0
        if args.command == "demo":
            report = rehearse(args.scenario)
        else:
            report = audit([getattr(args, stage) for stage in STAGES], load(args.contract))
        display(report, args.json)
        return 1 if report["failed"] else 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"version": 1, "status": "inconclusive", "error": str(exc)}))
        else:
            print(f"INCONCLUSIVE  {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
