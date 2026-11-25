"""Loading: a second load of the same data changes nothing; a revision updates, never appends."""
import pandas as pd
import pytest

from src.load import warehouse as wh
from src.quality.checks import PipelineStop
from src.transform import enrich, staging


def _pipeline_frames(make_feature, run_date, features):
    df = pd.json_normalize(features)
    coords = df.pop("geometry.coordinates")
    df["longitude"], df["latitude"], df["depth_km"] = coords.str[0], coords.str[1], coords.str[2]
    df["raw_file"] = "t/p.geojson"
    events, _ = staging.stg_events(df, run_date)
    versions = staging.event_versions(df)
    events = events[events["is_valid"]].drop(columns=["invalid_reason", "failed_test", "is_valid"])
    places = pd.DataFrame({"place_id": [1, 2], "name": ["Malatya", "Beirut"], "name_ascii": ["Malatya", "Beirut"],
                           "country_code": ["TR", "LB"], "admin1_code": ["81", "04"], "feature_code": ["PPLA", "PPLC"],
                           "latitude": [38.35, 33.89], "longitude": [38.31, 35.50],
                           "population": pd.array([426381, 1916100], dtype="Int64"),
                           "modified_on": pd.to_datetime(["2024-01-01", "2024-01-01"])})
    wiki = pd.DataFrame(columns=["wiki_id", "quake_date", "place", "magnitude", "deaths"])
    ev, pairs, _ = enrich.build(events, places, wiki, run_date)
    return ev, pairs, places, versions


def test_second_load_gives_identical_tables(tmp_config, make_feature, run_date):
    conn = wh.connect(tmp_config.WAREHOUSE_PATH)
    feats = [make_feature("us1", lat=38.4, lon=38.3), make_feature("us2", mag=5.2, lat=33.9, lon=35.6)]
    star = wh.build_star(*_pipeline_frames(make_feature, run_date, feats), conn)
    wh.load_star(conn, star)
    first = wh.table_fingerprint(conn)
    star = wh.build_star(*_pipeline_frames(make_feature, run_date, feats), conn)
    wh.load_star(conn, star)
    assert wh.table_fingerprint(conn) == first
    assert first["fact_earthquake"]["rows"] == 2


def test_revision_updates_the_fact_and_keeps_history(tmp_config, make_feature, run_date):
    conn = wh.connect(tmp_config.WAREHOUSE_PATH)
    v1 = make_feature("us1", mag=4.4, lat=38.4, lon=38.3, updated_ms=1_600_100_000_000)
    wh.load_star(conn, wh.build_star(*_pipeline_frames(make_feature, run_date, [v1]), conn))
    v2 = make_feature("us1", mag=4.7, lat=38.4, lon=38.3, updated_ms=1_700_000_000_000)
    wh.load_star(conn, wh.build_star(*_pipeline_frames(make_feature, run_date, [v1, v2]), conn))
    assert conn.execute("SELECT COUNT(*), MAX(magnitude) FROM fact_earthquake").fetchone() == (1, 4.7)
    hist = conn.execute("SELECT version_no, magnitude, is_current, valid_to IS NULL FROM dim_event_version "
                        "ORDER BY version_no").fetchall()
    assert hist == [(1, 4.4, 0, 0), (2, 4.7, 1, 1)]
    cur = conn.execute("SELECT f.event_version_key = v.event_version_key FROM fact_earthquake f "
                       "JOIN dim_event_version v ON v.event_id = f.event_id AND v.is_current = 1").fetchone()
    assert cur == (1,)


def test_orphan_fact_row_stops_the_load_and_rolls_back(tmp_config, make_feature, run_date):
    conn = wh.connect(tmp_config.WAREHOUSE_PATH)
    star = wh.build_star(*_pipeline_frames(make_feature, run_date, [make_feature("us1", lat=38.4, lon=38.3)]), conn)
    star["fact_place_exposure"].loc[0, "place_key"] = 999          # a place that is not in dim_place
    with pytest.raises(Exception):
        wh.load_star(conn, star)
    assert conn.execute("SELECT COUNT(*) FROM fact_earthquake").fetchone() == (0,)   # nothing was kept


def test_stop_exception_type_is_exported():
    assert issubclass(PipelineStop, RuntimeError)
