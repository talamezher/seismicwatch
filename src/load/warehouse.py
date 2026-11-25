"""Load the star schema into SQLite, idempotently.

Everything happens in ONE transaction:
  1. upsert the dimensions (INSERT ... ON CONFLICT DO UPDATE on the natural key),
  2. upsert the facts on their grain key,
  3. delete fact and dimension rows that are no longer in the new data,
  4. run the relationship tests (R1-R6),
  5. commit, or roll back if any of them fails.
Keys are natural or deterministic (date_key = yyyymmdd, place_key = geonameid,
event_id = USGS id), so loading the same data twice gives exactly the same
tables; table_fingerprint() proves it, and run_pipeline.py logs the fingerprint.
"""
import hashlib
import json
import logging
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from src import config
from src.quality.checks import STOP, PipelineStop, TestResult, relationship_sql
from src.transform.rules import MAGNITUDE_BANDS, UNASSIGNED

log = logging.getLogger(__name__)
SCHEMA = Path(__file__).with_name("schema.sql")
STAR_TABLES = {
    "dim_date": ["date_key"], "dim_country": ["country_key"], "dim_place": ["place_key"],
    "dim_magnitude_band": ["magnitude_band_key"], "dim_event_version": ["event_version_key"],
    "fact_earthquake": ["event_id"], "fact_place_exposure": ["event_id", "place_key"],
}
COUNTRY_NAMES = {
    "LB": "Lebanon", "TR": "Turkey", "CY": "Cyprus", "SY": "Syria", "IL": "Israel", "PS": "Palestine",
    "JO": "Jordan", "IQ": "Iraq", "IR": "Iran", "GR": "Greece", "BG": "Bulgaria", "GE": "Georgia",
    "AM": "Armenia", "AZ": "Azerbaijan", "EG": "Egypt", "SA": "Saudi Arabia", "RU": "Russia", "UA": "Ukraine",
    "RO": "Romania", "MK": "North Macedonia", "XK": "Kosovo", "RS": "Serbia", "AL": "Albania",
    "MD": "Moldova", "KW": "Kuwait", "LY": "Libya", UNASSIGNED: "At sea or remote (> 100 km from a town)",
}
VERSION_ATTRS = ["magnitude", "mag_type", "review_status", "latitude", "longitude", "depth_km", "place_text"]


def connect(path=None) -> sqlite3.Connection:
    path = Path(path or config.WAREHOUSE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA.read_text())
    return conn


# ---------------------------------------------------------------------------
# build the star tables from the enriched data
# ---------------------------------------------------------------------------
def population_class(pop: pd.Series) -> pd.Series:
    return pd.Series(np.select([pop < 1_000, pop < 10_000, pop < 100_000, pop < 1_000_000],
                               ["< 1k", "1k-10k", "10k-100k", "100k-1M"], "1M+"), index=pop.index)


def build_dim_date(event_times: pd.Series) -> pd.DataFrame:
    last = max(pd.Timestamp(config.STUDY_END), event_times.dt.tz_convert(None).max().normalize())
    days = pd.date_range(config.STUDY_START, last, freq="D")
    return pd.DataFrame({
        "date_key": days.strftime("%Y%m%d").astype(int), "full_date": days.strftime("%Y-%m-%d"),
        "year": days.year, "quarter": days.quarter, "month": days.month, "month_name": days.strftime("%B"),
        "day": days.day, "day_of_week": days.strftime("%A"), "is_weekend": (days.dayofweek >= 5).astype(int)})


def build_dim_country(codes) -> pd.DataFrame:
    codes = sorted(set(codes) | {UNASSIGNED} | set(config.STUDY_COUNTRIES))
    # fixed keys: the study countries first, then alphabetical, so keys never depend on the data order
    ordered = list(config.STUDY_COUNTRIES) + [c for c in codes if c not in config.STUDY_COUNTRIES]
    return pd.DataFrame({
        "country_key": range(1, len(ordered) + 1), "country_code": ordered,
        "country_name": [COUNTRY_NAMES.get(c, c) for c in ordered],
        "is_study_country": [int(c in config.STUDY_COUNTRIES) for c in ordered]})


def build_dim_magnitude_band() -> pd.DataFrame:
    return pd.DataFrame(MAGNITUDE_BANDS, columns=["magnitude_band_key", "band_label", "min_magnitude",
                                                   "max_magnitude", "usgs_class"])


def build_versions(versions: pd.DataFrame, event_ids, existing: pd.DataFrame) -> pd.DataFrame:
    """Type 2 history of each event: a new row only when a tracked attribute changed.

    versions: every version seen in the raw layer (staging keeps them before dedup).
    existing: event_version_key, event_id, source_updated_at already in the warehouse,
    so a version keeps its key from one run to the next."""
    v = versions[versions["event_id"].isin(set(event_ids))].copy()
    v["source_updated_at"] = v["updated_at_utc"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f").str[:-3] + "Z"
    v = v.sort_values(["event_id", "updated_at_utc"]).drop_duplicates(["event_id", "source_updated_at"])
    # collapse consecutive versions whose tracked attributes did not change
    sig = v[VERSION_ATTRS].astype("string").fillna("<NA>").agg("|".join, axis=1)
    changed = sig.ne(sig.groupby(v["event_id"]).shift())
    v = v[changed].copy()
    v["version_no"] = v.groupby("event_id").cumcount() + 1
    v["valid_from"] = v["source_updated_at"]
    v["valid_to"] = v.groupby("event_id")["source_updated_at"].shift(-1)
    v["is_current"] = v["valid_to"].isna().astype(int)
    keys = dict(zip(zip(existing["event_id"], existing["source_updated_at"]), existing["event_version_key"]))
    v["event_version_key"] = [keys.get(k) for k in zip(v["event_id"], v["source_updated_at"])]
    new = v["event_version_key"].isna()
    start = int(existing["event_version_key"].max()) + 1 if len(existing) else 1
    v.loc[new, "event_version_key"] = range(start, start + int(new.sum()))
    v["event_version_key"] = v["event_version_key"].astype(int)
    return v[["event_version_key", "event_id", "version_no", *VERSION_ATTRS, "source_updated_at",
              "valid_from", "valid_to", "is_current"]]


def build_star(events: pd.DataFrame, pairs: pd.DataFrame, places: pd.DataFrame, versions: pd.DataFrame,
               conn: sqlite3.Connection) -> dict:
    dim_country = build_dim_country(places["country_code"])
    ckey = dict(zip(dim_country["country_code"], dim_country["country_key"]))
    dim_place = pd.DataFrame({
        "place_key": places["place_id"].astype(int), "place_name": places["name"],
        "place_name_ascii": places["name_ascii"], "country_key": places["country_code"].map(ckey),
        "admin1_code": places["admin1_code"], "feature_code": places["feature_code"],
        "latitude": places["latitude"], "longitude": places["longitude"],
        "population": places["population"].astype(int),
        "population_class": population_class(places["population"].astype(float)),
        "modified_on": places["modified_on"].dt.strftime("%Y-%m-%d")})
    existing = pd.read_sql("SELECT event_version_key, event_id, source_updated_at FROM dim_event_version", conn)
    dim_version = build_versions(versions, events["event_id"], existing)
    current = dim_version[dim_version["is_current"] == 1].set_index("event_id")["event_version_key"]
    dk = events["event_time_utc"].dt.strftime("%Y%m%d").astype(int)
    fact = pd.DataFrame({
        "event_id": events["event_id"].astype(str), "event_version_key": events["event_id"].map(current),
        "date_key": dk, "place_key": events["nearest_place_id"].astype(int),
        "country_key": events["country_code"].map(ckey),
        "magnitude_band_key": events["magnitude_band_key"].astype(int),
        "event_time_utc": events["event_time_utc"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f").str[:-3] + "Z",
        "magnitude": events["magnitude"], "mag_type": events["mag_type"], "depth_km": events["depth_km"],
        "depth_is_default": events["depth_is_default"].astype(int), "depth_class": events["depth_class"],
        "latitude": events["latitude"], "longitude": events["longitude"],
        "usgs_place_text": events["place_text"], "review_status": events["review_status"],
        "distance_to_place_km": events["distance_km"],
        "places_within_25km": events["places_within_25km"], "places_within_50km": events["places_within_50km"],
        "population_within_25km": events["population_within_25km"],
        "population_within_50km": events["population_within_50km"],
        "in_study_period": events["in_study_period"].astype(int),
        "is_documented": events["is_documented"].astype(int), "wiki_place": events["wiki_place"],
        "wiki_deaths": events["wiki_deaths"].astype("Int64"),
        "significance": events["significance"], "felt_reports": events["felt_reports"],
        "tsunami_flag": events["tsunami_flag"]})
    date_of_event = dict(zip(fact["event_id"], fact["date_key"]))
    exposure = pd.DataFrame({
        "event_id": pairs["event_id"].astype(str), "place_key": pairs["place_id"].astype(int),
        "date_key": pairs["event_id"].astype(str).map(date_of_event), "distance_km": pairs["distance_km"],
        "within_25km": pairs["within_25km"].astype(int)})
    return {"dim_date": build_dim_date(events["event_time_utc"]), "dim_country": dim_country,
            "dim_place": dim_place, "dim_magnitude_band": build_dim_magnitude_band(),
            "dim_event_version": dim_version, "fact_earthquake": fact, "fact_place_exposure": exposure}


# ---------------------------------------------------------------------------
# idempotent load
# ---------------------------------------------------------------------------
def _records(df: pd.DataFrame):
    clean = df.astype(object).where(df.notna(), None)
    return [tuple(_py(v) for v in row) for row in clean.itertuples(index=False, name=None)]


def _py(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.bool_,)):
        return int(v)
    return v


def upsert(conn, table, df, key_cols):
    cols = list(df.columns)
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in key_cols)
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
           f"ON CONFLICT ({', '.join(key_cols)}) DO "
           + (f"UPDATE SET {updates}" if updates else "NOTHING"))
    conn.executemany(sql, _records(df))


def delete_missing(conn, table, df, key_cols):
    """Delete the rows whose key is not in df (an event USGS deleted, a place that disappeared)."""
    tmp = f"_keep_{table}"
    conn.execute(f"DROP TABLE IF EXISTS temp.{tmp}")
    conn.execute(f"CREATE TEMP TABLE {tmp} ({', '.join(key_cols)}, PRIMARY KEY ({', '.join(key_cols)})) "
                 "WITHOUT ROWID")
    conn.executemany(f"INSERT INTO temp.{tmp} VALUES ({', '.join('?' for _ in key_cols)})",
                     _records(df[key_cols]))
    cond = " AND ".join(f"k.{c} = t.{c}" for c in key_cols)
    cur = conn.execute(f"DELETE FROM {table} AS t WHERE NOT EXISTS (SELECT 1 FROM temp.{tmp} k WHERE {cond})")
    return cur.rowcount


def load_star(conn: sqlite3.Connection, star: dict):
    """Returns (relationship test results, rows deleted per table). Raises PipelineStop on an orphan."""
    deleted = {}
    try:
        conn.execute("BEGIN")
        for table in ["dim_date", "dim_country", "dim_magnitude_band", "dim_place", "dim_event_version"]:
            upsert(conn, table, star[table], STAR_TABLES[table])
        upsert(conn, "fact_earthquake", star["fact_earthquake"], STAR_TABLES["fact_earthquake"])
        upsert(conn, "fact_place_exposure", star["fact_place_exposure"], STAR_TABLES["fact_place_exposure"])
        # children before parents, so the foreign keys stay satisfied
        for table in ["fact_place_exposure", "fact_earthquake", "dim_event_version", "dim_place",
                      "dim_magnitude_band", "dim_country", "dim_date"]:
            deleted[table] = delete_missing(conn, table, star[table], STAR_TABLES[table])
        results = []
        for tid, name, sql in relationship_sql():
            orphans = conn.execute(sql).fetchone()[0]
            results.append(TestResult(tid, name, STOP, "warehouse", orphans, orphans == 0))
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        results.append(TestResult("R7", "SQLite foreign key check", STOP, "warehouse", len(fk), not fk))
        if any(not r.passed for r in results):
            raise PipelineStop("relationship test failed: " +
                               ", ".join(f"{r.test_id} ({r.failing_rows})" for r in results if not r.passed))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return results, deleted


def table_fingerprint(conn, tables=STAR_TABLES) -> dict:
    """Row count and SHA-256 of every star table, rows sorted by key: equal fingerprints
    after two runs prove that the second run changed nothing."""
    out = {}
    for table, keys in tables.items():
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY {', '.join(keys)}").fetchall()
        h = hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()[:16]
        out[table] = {"rows": len(rows), "sha256": h}
    return out


def combined_hash(fp: dict) -> str:
    return hashlib.sha256(json.dumps(fp, sort_keys=True).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# operational tables
# ---------------------------------------------------------------------------
def start_run(conn, run_id, started_at, mode):
    with conn:
        conn.execute("INSERT OR REPLACE INTO run_log (run_id, started_at, mode, status) VALUES (?, ?, ?, 'running')",
                     (run_id, started_at, mode))


def finish_run(conn, run_id, **fields):
    sets = ", ".join(f"{k} = ?" for k in fields)
    with conn:
        conn.execute(f"UPDATE run_log SET {sets} WHERE run_id = ?", (*[_py(v) for v in fields.values()], run_id))


def write_test_results(conn, run_id, results):
    with conn:
        conn.execute("DELETE FROM data_test_results WHERE run_id = ?", (run_id,))
        conn.executemany("INSERT INTO data_test_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         [(run_id, r.test_id, r.name, r.severity, r.table_name, int(r.failing_rows),
                           int(r.passed), r.details) for r in results])


def write_quarantine(conn, run_id, quarantined_at, rows: pd.DataFrame):
    """rows: source, record_key, test_id, severity, reason, record_json."""
    with conn:
        conn.execute("DELETE FROM quarantine WHERE run_id = ?", (run_id,))
        conn.executemany("INSERT INTO quarantine VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         [(run_id, r.source, str(r.record_key), r.test_id, r.severity, r.reason, quarantined_at,
                           r.record_json) for r in rows.itertuples(index=False)])


def write_join_log(conn, run_id, joins):
    with conn:
        conn.executemany("INSERT OR REPLACE INTO join_log VALUES (?, ?, ?, ?, ?)",
                         [(run_id, j["join"], j["rows_before"], j["rows_after"], j["unmatched"]) for j in joins])


def fact_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM fact_earthquake").fetchone()[0]
