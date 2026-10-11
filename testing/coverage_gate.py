"""Prevent a well-tested package from masking an untested new module."""
import argparse
import json
from pathlib import Path


def failures(report, minimum=90):
    files = report["files"]
    if not files:
        raise ValueError("The coverage report contains no files.")
    failed = []
    for name, entry in sorted(files.items()):
        summary = entry["summary"]
        total = summary["num_statements"] + summary["num_branches"]
        covered = summary["covered_lines"] + summary["covered_branches"]
        if total and covered * 100 / total < minimum:
            failed.append(f"{name}: {covered * 100 / total:.2f}% (minimum {minimum}%)")
    return failed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    failed = failures(json.loads(args.report.read_text()))
    if failed:
        parser.exit(1, "Per-module coverage failed:\n" + "\n".join(failed) + "\n")
    print("Every measured module meets 90% combined statement/branch coverage.")


if __name__ == "__main__":  # pragma: no cover
    main()
