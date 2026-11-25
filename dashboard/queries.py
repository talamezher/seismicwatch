"""SQL the dashboard runs against the warehouse. Kept apart from the layout so it can be tested."""
import sqlite3
from pathlib import Path

import pandas as pd

from src import config


def connect(db_path=None):
    path = Path(db_path or config.WAREHOUSE_PATH)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run `python run_pipeline.py` first")
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)   # the dashboard only reads


def _where(countries, mag_range, date_range, alias=""):
    a = f"{alias}." if alias else ""
    clauses = [f"{a}magnitude BETWEEN ? AND ?", f"substr({a}event_time_utc, 1, 10) BETWEEN ? AND ?"]
    params = [mag_range[0], mag_range[1], date_range[0], date_range[1]]
    return clauses, params


def earthquakes(conn, countries, mag_range, date_range):
    clauses, params = _where(countries, mag_range, date_range)
    clauses.append(f"country_code IN ({','.join('?' * len(countries))})")
    params += list(countries)
    sql = f"SELECT * FROM v_earthquakes WHERE {' AND '.join(clauses)} ORDER BY event_time_utc"
    df = pd.read_sql(sql, conn, params=params)
    df["event_time_utc"] = pd.to_datetime(df["event_time_utc"])
    return df


def town_ranking(conn, countries, mag_range, date_range, radius_km=50, top=15):
    """Settlements of the selected countries ranked by the number of M4+ earthquakes within the radius."""
    clauses, params = _where(countries, mag_range, date_range)
    clauses.append(f"place_country IN ({','.join('?' * len(countries))})")
    params += list(countries)
    if radius_km == 25:
        clauses.append("within_25km = 1")
    sql = f"""
        SELECT place_key, place_name, place_country, population, place_lat, place_lon,
               COUNT(*) AS earthquakes, MAX(magnitude) AS max_magnitude, MIN(distance_km) AS closest_km,
               SUM(magnitude >= 5) AS m5_plus
        FROM v_place_exposure
        WHERE {' AND '.join(clauses)}
        GROUP BY place_key
        ORDER BY earthquakes DESC, population DESC
        LIMIT ?"""
    return pd.read_sql(sql, conn, params=params + [top])


def events_near_place(conn, place_key, mag_range, date_range, radius_km=50):
    clauses, params = _where(None, mag_range, date_range, alias="f")
    clauses.append("x.place_key = ?")
    params.append(int(place_key))
    if radius_km == 25:
        clauses.append("x.within_25km = 1")
    sql = f"""
        SELECT f.event_id, substr(f.event_time_utc, 1, 16) AS time_utc, f.magnitude, f.depth_km,
               x.distance_km, f.usgs_place_text, f.is_documented, f.wiki_deaths
        FROM fact_place_exposure x JOIN fact_earthquake f USING (event_id)
        WHERE {' AND '.join(clauses)}
        ORDER BY f.magnitude DESC, f.event_time_utc"""
    return pd.read_sql(sql, conn, params=params)


def last_run(conn):
    row = pd.read_sql("SELECT run_id, finished_at, mode, status, events_loaded, events_quarantined "
                      "FROM run_log WHERE status = 'success' ORDER BY run_id DESC LIMIT 1", conn)
    return row.iloc[0].to_dict() if len(row) else None


def bounds(conn):
    r = conn.execute("SELECT MIN(magnitude), MAX(magnitude), MIN(substr(event_time_utc,1,10)), "
                     "MAX(substr(event_time_utc,1,10)) FROM fact_earthquake").fetchone()
    return {"mag_min": r[0], "mag_max": r[1], "date_min": r[2], "date_max": r[3]}
