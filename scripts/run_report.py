"""Print the last runs, their data tests and the row counts as Markdown.

    python scripts/run_report.py            (also used for the GitHub Actions job summary)
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import config  # noqa: E402


def main(db=None, runs=10):
    conn = sqlite3.connect(db or config.WAREHOUSE_PATH)
    log = pd.read_sql(f"SELECT run_id, mode, status, records_extracted, events_staged, events_loaded, "
                      f"events_quarantined, tests_failed, warehouse_hash, message FROM run_log "
                      f"ORDER BY run_id DESC LIMIT {runs}", conn)
    print("## SeismicWatch run log\n")
    print(log.to_markdown(index=False))
    if log.empty:
        return
    last = log.iloc[0]["run_id"]
    tests = pd.read_sql("SELECT test_id, severity, CASE passed WHEN 1 THEN 'pass' ELSE 'FAIL' END AS result, "
                        "failing_rows, name, details FROM data_test_results WHERE run_id = ? ORDER BY test_id",
                        conn, params=[last])
    print(f"\n## Data tests of run {last}\n")
    print(tests.to_markdown(index=False))
    q = pd.read_sql("SELECT source, test_id, reason, COUNT(*) AS rows FROM quarantine WHERE run_id = ? "
                    "GROUP BY source, test_id, reason ORDER BY rows DESC", conn, params=[last])
    print(f"\n## Quarantine of run {last}\n")
    print(q.to_markdown(index=False) if len(q) else "(empty)")
    joins = pd.read_sql("SELECT join_name, rows_before, rows_after, unmatched FROM join_log WHERE run_id = ?",
                        conn, params=[last])
    print(f"\n## Joins of run {last}\n")
    print(joins.to_markdown(index=False))


if __name__ == "__main__":
    main(*sys.argv[1:2])
