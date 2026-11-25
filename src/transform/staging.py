"""Staging layer: one cleaned table per source, at the same grain as the raw data.

Each staging function follows the six steps of the Week 5 lecture, always in the
same order: 1 rename, 2 cast, 3 standardize, 4 placeholders to missing,
5 keep the newest version / remove duplicates, 6 flag invalid rows (never delete).
They read only the raw layer, never change it, and depend only on their inputs
and the run date, so running them twice gives the same tables (idempotent).
Joins and business rules come later (src/transform/enrich.py).
"""
import csv
import io
import json
import logging
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from src import config
from src.quality.checks import flag_events

log = logging.getLogger(__name__)

# ============================================================================
# USGS events
# ============================================================================
EVENT_RENAME = {
    "id": "event_id", "properties.mag": "magnitude", "properties.magType": "mag_type",
    "properties.place": "place_text", "properties.time": "event_time_utc",
    "properties.updated": "updated_at_utc", "properties.status": "review_status",
    "properties.type": "event_type", "properties.net": "network", "properties.sig": "significance",
    "properties.felt": "felt_reports", "properties.tsunami": "tsunami_flag", "properties.alert": "pager_alert",
    "properties.nst": "stations", "properties.gap": "azimuthal_gap", "properties.rms": "rms",
}


def usgs_raw_files(raw_dir=None):
    """The newest full extraction plus every incremental run made after it.

    Raw files are never deleted; an older full extraction is simply superseded."""
    raw_dir = Path(raw_dir or config.RAW_DIR) / "usgs"
    fulls = sorted(p for p in (raw_dir / "full").glob("*") if p.is_dir())
    if not fulls:
        raise FileNotFoundError("no USGS full extraction in the raw layer: run with --full")
    latest_full = fulls[-1]
    files = sorted(latest_full.glob("*.geojson"))
    # incremental folders are named by run id (YYYYMMDDTHHMMSSZ); keep those of the same day or later
    cutoff = latest_full.name.replace("-", "")
    for inc in sorted(p for p in (raw_dir / "incremental").glob("*") if p.is_dir()):
        if inc.name[:8] >= cutoff:
            files += sorted(inc.glob("*.geojson"))
    return files


def all_usgs_files(raw_dir=None):
    """Every USGS raw file ever saved (for the history of revisions)."""
    raw_dir = Path(raw_dir or config.RAW_DIR) / "usgs"
    return sorted(raw_dir.glob("*/*/*.geojson"))


def read_usgs_raw(files) -> pd.DataFrame:
    frames = []
    for f in files:
        doc = json.loads(Path(f).read_text(encoding="utf-8"))
        feats = doc.get("features", [])
        if not feats:
            continue
        df = pd.json_normalize(feats)
        coords = df.pop("geometry.coordinates")
        df["longitude"] = coords.str[0]
        df["latitude"] = coords.str[1]
        df["depth_km"] = coords.str[2]
        df["raw_file"] = f"{Path(f).parent.name}/{Path(f).name}"
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["id"])
    return pd.concat(frames, ignore_index=True)


def normalize_events(raw: pd.DataFrame) -> pd.DataFrame:
    """Steps 1 to 4 of the staging model, shared by stg_events and event_versions."""
    # 1. rename -----------------------------------------------------------------
    keep = [c for c in EVENT_RENAME if c in raw.columns] + ["longitude", "latitude", "depth_km", "raw_file"]
    df = raw[keep].rename(columns=EVENT_RENAME)
    for col in EVENT_RENAME.values():          # an optional field absent from every page
        if col not in df.columns:
            df[col] = np.nan
    # 2. cast -------------------------------------------------------------------
    df["event_id"] = df["event_id"].astype("string")
    df["event_time_utc"] = pd.to_datetime(df["event_time_utc"], unit="ms", utc=True)
    df["updated_at_utc"] = pd.to_datetime(df["updated_at_utc"], unit="ms", utc=True)
    for col in ["magnitude", "longitude", "latitude", "depth_km", "azimuthal_gap", "rms"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["significance", "felt_reports", "tsunami_flag", "stations"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    # 3. standardize --------------------------------------------------------------
    for col in ["mag_type", "review_status", "event_type", "network", "pager_alert"]:
        df[col] = df[col].astype("string").str.strip().str.lower()
    df["place_text"] = df["place_text"].astype("string").str.strip()
    # 4. placeholders to missing ---------------------------------------------------
    for col in ["mag_type", "review_status", "event_type", "place_text", "pager_alert"]:
        df[col] = df[col].replace({"": pd.NA})
    # USGS fixes the depth at exactly 10 km when it cannot resolve it: a default, not a measure.
    df["depth_is_default"] = df["depth_km"].eq(10.0)
    return df


def stg_events(raw: pd.DataFrame, run_date: pd.Timestamp):
    """raw: the newest full extraction plus the later incremental runs.
    Returns (staged events, accounting dict): one row per event id, its newest version."""
    acc = {"raw_records": len(raw)}
    df = normalize_events(raw)
    # 5. newest version of each event ------------------------------------------------
    df = df.sort_values(["event_id", "updated_at_utc", "raw_file"])
    before = len(df)
    df = df.drop_duplicates(subset=["event_id"], keep="last").reset_index(drop=True)
    acc["older_versions_removed"] = before - len(df)
    # 6. flag invalid rows with the Q-tests of src/quality/checks.py; never delete them here
    df = flag_events(df, run_date)
    acc["staged_events"] = len(df)
    return df, acc


def event_versions(raw_all: pd.DataFrame) -> pd.DataFrame:
    """Every version of every event in the whole raw layer (all runs), for the
    type 2 history dimension: the raw layer is the memory of what USGS published."""
    return normalize_events(raw_all).sort_values(["event_id", "updated_at_utc"]).reset_index(drop=True)


# ============================================================================
# GeoNames places
# ============================================================================
GEONAMES_COLUMNS = ["geonameid", "name", "asciiname", "alternatenames", "latitude", "longitude",
                    "feature_class", "feature_code", "country_code", "cc2", "admin1_code", "admin2_code",
                    "admin3_code", "admin4_code", "population", "elevation", "dem", "timezone",
                    "modification_date"]
# Feature codes of real settlements. PPLX is a section of a city (its people are already
# counted in the city), PPLH/PPLQ/PPLW are historical, abandoned or destroyed places.
SETTLEMENT_CODES = {"PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLA5", "PPLC", "PPLG", "PPLF", "PPLS", "PPLL"}


def read_geonames_zip(path: Path, member: str | None = None) -> pd.DataFrame:
    member = member or path.stem + ".txt"
    with zipfile.ZipFile(path) as zf, zf.open(member) as f:
        text = io.TextIOWrapper(f, encoding="utf-8")       # GeoNames is UTF-8; say so
        df = pd.read_csv(text, sep="\t", header=None, names=GEONAMES_COLUMNS, quoting=csv.QUOTE_NONE,
                         dtype=str, keep_default_na=False, na_values=[""])
    df["source_file"] = path.name
    return df


def stg_places(frames, neighbour_bbox_margin=1.5):
    """frames: list of raw GeoNames tables (country files first, then cities1000)."""
    raw = pd.concat(frames, ignore_index=True)
    acc = {"raw_records": len(raw)}
    # keep only populated places (feature class P): the question is about people
    raw = raw[raw["feature_class"] == "P"]
    acc["not_populated_place"] = acc["raw_records"] - len(raw)
    # 1. rename
    df = raw.rename(columns={"geonameid": "place_id", "asciiname": "name_ascii",
                             "elevation": "elevation_m", "dem": "dem_m",
                             "modification_date": "modified_on"})
    # 2. cast: ids and codes stay text where they are labels (admin1 '04' keeps its zero)
    df["place_id"] = pd.to_numeric(df["place_id"], errors="coerce").astype("Int64")
    for col in ["latitude", "longitude"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["population", "elevation_m", "dem_m"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    df["modified_on"] = pd.to_datetime(df["modified_on"], format="%Y-%m-%d", errors="coerce")
    # 3. standardize
    df["country_code"] = df["country_code"].str.strip().str.upper()
    df["feature_code"] = df["feature_code"].str.strip().str.upper()
    df["name"] = df["name"].str.strip()
    # 4. placeholders to missing: population 0 means "unknown", dem -9999 means "no data"
    df["population"] = df["population"].mask(df["population"] == 0)
    df["dem_m"] = df["dem_m"].mask(df["dem_m"] == -9999)
    # neighbours: cities1000 covers the world; keep the towns around the study box
    b = config.BBOX
    near = (df["latitude"].between(b["minlatitude"] - neighbour_bbox_margin, b["maxlatitude"] + neighbour_bbox_margin)
            & df["longitude"].between(b["minlongitude"] - neighbour_bbox_margin,
                                      b["maxlongitude"] + neighbour_bbox_margin))
    from_country_file = df["source_file"] != f"{config.GEONAMES_NEIGHBOURS_FILE}.zip"
    acc["outside_region"] = int((~(near | from_country_file)).sum())
    df = df[near | from_country_file]
    # 5. duplicates: a town of LB/TR/CY is in its country file and in cities1000
    df["_pref"] = (~from_country_file.loc[df.index]).astype(int)    # 0 = country file, preferred
    df = df.sort_values(["place_id", "_pref"])
    before = len(df)
    df = df.drop_duplicates(subset=["place_id"], keep="first").drop(columns="_pref")
    acc["duplicates_removed"] = before - len(df)
    # 6. flag
    df["is_settlement"] = df["feature_code"].isin(SETTLEMENT_CODES)
    reason = pd.Series(pd.NA, index=df.index, dtype="string")
    reason = reason.mask(~df["is_settlement"], "not a settlement (" + df["feature_code"] + ")")
    reason = reason.mask(df["population"].isna(), "population unknown")
    reason = reason.mask(~df["latitude"].between(-90, 90) | ~df["longitude"].between(-180, 180),
                         "invalid coordinates")
    df["invalid_reason"] = reason
    df["is_valid"] = reason.isna()
    df = df[["place_id", "name", "name_ascii", "country_code", "admin1_code", "feature_code", "latitude",
             "longitude", "population", "elevation_m", "dem_m", "timezone", "modified_on", "source_file",
             "is_settlement", "is_valid", "invalid_reason"]].reset_index(drop=True)
    acc["staged_places"] = len(df)
    acc["valid_places"] = int(df["is_valid"].sum())
    return df, acc


# ============================================================================
# Wikipedia historical earthquakes
# ============================================================================
def _flat(col):
    if isinstance(col, tuple):
        parts = [str(c) for c in col if not str(c).startswith("Unnamed")]
        col = " ".join(dict.fromkeys(parts))
    return re.sub(r"\[.*?\]", "", str(col)).strip()


def _pick(columns, *keys, exclude=()):
    for c in columns:
        low = c.lower()
        if any(k in low for k in keys) and not any(x in low for x in exclude):
            return c
    return None


FOOTNOTE = re.compile(r"\[[^\]]*\]")
NUMBER = re.compile(r"\d+(?:\.\d+)?")


def parse_magnitude(value):
    """'7.8 Mw' -> 7.8, '6.6–6.9' -> 6.6, 'Mw 7.4' -> 7.4, '–' -> NaN."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    text = FOOTNOTE.sub("", str(value)).replace(",", ".")
    for m in NUMBER.findall(text):
        v = float(m)
        if 1.0 <= v <= 10.0:
            return v
    return np.nan


def parse_count(value):
    """'50,783' -> 50783, '1,000+' -> 1000, '–' or '' -> NaN (unknown, not zero)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    text = FOOTNOTE.sub("", str(value)).replace(",", "")
    m = re.search(r"\d+", text)
    return float(m.group()) if m else np.nan


def parse_date(value):
    text = FOOTNOTE.sub("", str(value)).strip()
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%B %d, %Y", "%d %b %Y", "%Y %B %d"):
        try:
            return pd.to_datetime(text, format=fmt)
        except (ValueError, TypeError):
            continue
    return pd.NaT


def stg_wiki_quakes(pages):
    """pages: list of (page title, html text). One row per historical earthquake."""
    from src.extract.wikipedia import earthquake_tables
    rows = []
    for title, html in pages:
        for t_no, t in enumerate(earthquake_tables(html)):
            t = t.copy()
            t.columns = [_flat(c) for c in t.columns]
            cols = list(t.columns)
            c_date = _pick(cols, "date")
            c_year = _pick(cols, "year")
            c_time = _pick(cols, "time")
            c_place = _pick(cols, "place", "location", "epicent", "region", "province", "area", "name")
            c_mag = _pick(cols, "mag")
            c_deaths = _pick(cols, "death", "fatal", "casualt", "killed")
            c_lat = _pick(cols, "lat")
            c_lon = _pick(cols, "lon", exclude=("long name",))
            for r_no, r in t.iterrows():
                rows.append({
                    "page": title, "table_no": t_no, "row_no": r_no,
                    "date_raw": r.get(c_date) if c_date else None,
                    "year_raw": r.get(c_year) if c_year else None,
                    "time_raw": r.get(c_time) if c_time else None,
                    "place_raw": r.get(c_place) if c_place else None,
                    "magnitude_raw": r.get(c_mag) if c_mag else None,
                    "deaths_raw": r.get(c_deaths) if c_deaths else None,
                    "lat_raw": r.get(c_lat) if c_lat else None,
                    "lon_raw": r.get(c_lon) if c_lon else None,
                })
    raw = pd.DataFrame(rows)
    acc = {"raw_records": len(raw)}
    if raw.empty:
        return raw, acc
    # 1-2. rename and cast
    df = pd.DataFrame({
        "page": raw["page"],
        "quake_date": raw["date_raw"].map(parse_date),
        "place": raw["place_raw"].astype("string").str.replace(FOOTNOTE, "", regex=True).str.strip(),
        "magnitude": raw["magnitude_raw"].map(parse_magnitude),
        "deaths": raw["deaths_raw"].map(parse_count),
        "latitude": pd.to_numeric(raw["lat_raw"].astype("string").str.extract(r"(-?\d+(?:\.\d+)?)")[0],
                                  errors="coerce"),
        "longitude": pd.to_numeric(raw["lon_raw"].astype("string").str.extract(r"(-?\d+(?:\.\d+)?)")[0],
                                   errors="coerce"),
    })
    # a row with only a year cannot be matched to a day; keep the year for the report
    df["year"] = df["quake_date"].dt.year.astype("Int64")
    no_date = df["year"].isna() & raw["year_raw"].notna()
    df.loc[no_date, "year"] = pd.to_numeric(raw.loc[no_date, "year_raw"].astype("string").str.extract(r"(\d{3,4})")[0],
                                            errors="coerce").astype("Int64")
    # 3. standardize: one spacing, no footnote marks
    df["place"] = df["place"].str.replace(r"\s+", " ", regex=True)
    # 4. placeholders: '–', '?', 'Unknown' already became NaN in the parsers
    df["place"] = df["place"].replace({"": pd.NA, "–": pd.NA, "-": pd.NA, "nan": pd.NA})
    # 5. duplicates: the same earthquake listed in two tables of the page
    before = len(df)
    df = df.drop_duplicates(subset=["quake_date", "magnitude", "place"]).reset_index(drop=True)
    acc["duplicates_removed"] = before - len(df)
    # 6. flag
    reason = pd.Series(pd.NA, index=df.index, dtype="string")
    reason = reason.mask(df["magnitude"].isna(), "magnitude not readable")
    reason = reason.mask(df["quake_date"].isna(), "no full date (cannot be matched to an event)")
    df["invalid_reason"] = reason
    df["is_valid"] = reason.isna()
    df.insert(0, "wiki_id", range(1, len(df) + 1))
    acc["staged_rows"] = len(df)
    acc["valid_rows"] = int(df["is_valid"].sum())
    return df, acc
