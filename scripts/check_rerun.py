"""Proof of idempotency: rebuild from the raw layer twice and compare the warehouse fingerprints.

    python scripts/check_rerun.py [--run-date YYYY-MM-DD]

Exit code 0 when the second run changed nothing in the star tables.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_pipeline  # noqa: E402
from src import config  # noqa: E402
from src.load import warehouse as wh  # noqa: E402


def fingerprint():
    with sqlite3.connect(config.WAREHOUSE_PATH) as conn:
        return wh.table_fingerprint(conn)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-date")
    args = ap.parse_args()
    extra = ["--run-date", args.run_date] if args.run_date else []
    if run_pipeline.main(["--skip-extract", *extra]) != 0:
        sys.exit("first run did not succeed")
    first = fingerprint()
    if run_pipeline.main(["--skip-extract", *extra]) != 0:
        sys.exit("second run did not succeed")
    second = fingerprint()
    print("\n| table | rows | sha256 run 1 | sha256 run 2 | same |\n|---|---:|---|---|---|")
    for t in first:
        print(f"| {t} | {second[t]['rows']:,} | {first[t]['sha256']} | {second[t]['sha256']} | "
              f"{'yes' if first[t] == second[t] else 'NO'} |")
    if first != second:
        sys.exit("the second run changed the warehouse")
    print("\nThe second run produced exactly the same tables.")


if __name__ == "__main__":
    main()
