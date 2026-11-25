"""The whole pipeline on the sample of real raw files, then the dashboard queries on its warehouse."""
import shutil
import sqlite3
from pathlib import Path

import pytest

import run_pipeline
from dashboard import queries as q

FIXTURES = Path(__file__).parent / "fixtures" / "raw"


@pytest.fixture
def built_warehouse(tmp_config, monkeypatch):
    shutil.copytree(FIXTURES, tmp_config.RAW_DIR)
    monkeypatch.setattr(tmp_config, "LOG_DIR", tmp_config.DATA_DIR / "logs")
    monkeypatch.setattr(tmp_config, "MIN_VALID_EVENTS", 50)
    assert run_pipeline.main(["--skip-extract", "--run-date", "2026-11-14"]) == 0
    return tmp_config.WAREHOUSE_PATH


def test_pipeline_accounts_for_every_row(built_warehouse):
    conn = sqlite3.connect(built_warehouse)
    loaded, quarantined, status = conn.execute(
        "SELECT events_loaded, events_quarantined, status FROM run_log").fetchone()
    assert status == "success"
    assert loaded == conn.execute("SELECT COUNT(*) FROM fact_earthquake").fetchone()[0]
    reasons = dict(conn.execute("SELECT test_id, reason FROM quarantine WHERE source = 'usgs'").fetchall())
    assert reasons["Q7"] == "probable duplicate of us70009ad8"          # found by profiling the real data
    assert reasons["Q5"] == "time outside the period"                   # after the run date passed in
    stops = conn.execute("SELECT COUNT(*) FROM data_test_results WHERE severity = 'stop' AND passed = 0").fetchone()
    assert stops == (0,)


def test_dashboard_queries_and_app(built_warehouse):
    conn = q.connect(built_warehouse)
    ev = q.earthquakes(conn, ["TR", "LB", "CY"], [4.0, 9.0], ("2016-11-14", "2026-11-14"))
    assert len(ev) > 0 and set(ev["country_code"]) <= {"TR", "LB", "CY"}
    towns = q.town_ranking(conn, ["TR"], [4.0, 9.0], ("2016-11-14", "2026-11-14"), 50, min_population=10_000)
    assert len(towns) > 0 and (towns["population"] >= 10_000).all()
    assert towns["earthquakes"].is_monotonic_decreasing
    near = q.events_near_place(conn, towns.iloc[0]["place_key"], [4.0, 9.0], ("2016-11-14", "2026-11-14"))
    assert len(near) == towns.iloc[0]["earthquakes"]            # the table agrees with the bar
    from dashboard.app import create_app
    app = create_app(built_warehouse)
    assert app.layout is not None
