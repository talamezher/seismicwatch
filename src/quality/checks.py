"""Data tests, run on every pipeline run, each with a severity decided in advance.

    stop        load nothing: the run fails and the warehouse keeps the last good data
    quarantine  set the failing rows aside in the quarantine table, with the reason; load the rest
    warn        load everything, but write the problem to the log and the test-results table

Row-level tests on the earthquakes (Q-tests) are also what the staging step uses to
flag invalid rows, so each rule is written once. Table-level tests (S- and W-tests)
look at whole tables; the relationship tests (R-tests) run inside the warehouse,
before the load is committed.
"""
import logging
from dataclasses import asdict, dataclass

import pandas as pd

from src import config

log = logging.getLogger(__name__)

STOP, QUARANTINE, WARN = "stop", "quarantine", "warn"


class PipelineStop(RuntimeError):
    """A stop-severity test failed: nothing is loaded."""


@dataclass
class TestResult:
    test_id: str
    name: str
    severity: str
    table_name: str
    failing_rows: int
    passed: bool
    details: str = ""

    def as_row(self):
        return asdict(self)


# ---------------------------------------------------------------------------
# Row-level tests on staged earthquakes (severity: quarantine), in priority order:
# a row failing several tests is set aside under the first one.
# ---------------------------------------------------------------------------
def _box(df):
    b = config.BBOX
    return (df["latitude"].between(b["minlatitude"], b["maxlatitude"])
            & df["longitude"].between(b["minlongitude"], b["maxlongitude"]))


def _utc(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


EVENT_ROW_TESTS = [
    # id, name, reason written in the quarantine table, function(df, run_date) -> True where the row FAILS
    ("Q1", "event id and time not null", "missing event id or time",
     lambda df, d: df["event_id"].isna() | df["event_time_utc"].isna()),
    ("Q2", "event not deleted at the source", "deleted by USGS",
     lambda df, d: df["review_status"].eq("deleted")),
    ("Q3", "magnitude not null and at least 4.0", "magnitude missing or below 4.0 (revised)",
     lambda df, d: df["magnitude"].isna() | (df["magnitude"] < config.MIN_MAGNITUDE)),
    ("Q4", "coordinates inside the study box, depth -5 to 700 km", "invalid or out-of-box location",
     lambda df, d: df["latitude"].isna() | df["longitude"].isna() | ~_box(df)
     | ~df["depth_km"].between(-5, 700)),
    ("Q5", "event time in the study period and not after the run date", "time outside the period",
     lambda df, d: (df["event_time_utc"] < pd.Timestamp(config.STUDY_START, tz="UTC"))
     | (df["event_time_utc"] > _utc(d) + pd.Timedelta(days=1))),
    ("Q6", "event type is earthquake", "not an earthquake",
     lambda df, d: df["event_type"].fillna("earthquake") != "earthquake"),
    ("Q7", "not a duplicate of another event (same earthquake under two ids)", "probable duplicate",
     lambda df, d: df["duplicate_of"].notna()),
]

DUPLICATE_SECONDS = 2.0     # two ids closer than this in time...
DUPLICATE_KM = 5.0          # ...and in space are one earthquake recorded twice


def probable_duplicates(df: pd.DataFrame) -> pd.Series:
    """For each event, the id of another event it duplicates (or NA).

    Two USGS ids whose origin times are at most 2 s apart and whose epicentres are at
    most 5 km apart are the same earthquake recorded twice (two keys, one entity).
    The record with the higher significance (more stations, more information) is kept;
    on a tie, the smaller id is kept, so the choice never depends on row order."""
    from src.transform.geo import haversine_km
    e = df[["event_id", "event_time_utc", "latitude", "longitude", "significance"]].dropna(
        subset=["event_time_utc", "latitude", "longitude"]).sort_values("event_time_utc")
    t = e["event_time_utc"].astype("int64").to_numpy() / 1e9
    ids, lat, lon = e["event_id"].to_numpy(), e["latitude"].to_numpy(), e["longitude"].to_numpy()
    sig = e["significance"].fillna(0).to_numpy()
    out = {}
    for i in range(len(e)):
        j = i + 1
        while j < len(e) and t[j] - t[i] <= DUPLICATE_SECONDS:
            if haversine_km(lat[i], lon[i], lat[j], lon[j]) <= DUPLICATE_KM:
                keep_i = sig[i] > sig[j] or (sig[i] == sig[j] and str(ids[i]) < str(ids[j]))
                drop, keep = (ids[j], ids[i]) if keep_i else (ids[i], ids[j])
                out.setdefault(drop, keep)
            j += 1
    return df["event_id"].map(out).astype("string")



def flag_events(df: pd.DataFrame, run_date) -> pd.DataFrame:
    """Adds is_valid, invalid_reason and failed_test (the first failing Q-test)."""
    reason = pd.Series(pd.NA, index=df.index, dtype="string")
    test = pd.Series(pd.NA, index=df.index, dtype="string")
    for tid, _, text, fails in reversed(EVENT_ROW_TESTS):   # reversed: the first test wins
        mask = fails(df, run_date).fillna(True).astype(bool)
        if tid == "Q6":
            detail = "not an earthquake (" + df["event_type"].fillna("?") + ")"
            reason = reason.mask(mask, detail)
        elif tid == "Q7":
            reason = reason.mask(mask, "probable duplicate of " + df["duplicate_of"].fillna(""))
        else:
            reason = reason.mask(mask, text)
        test = test.mask(mask, tid)
    out = df.copy()
    out["invalid_reason"] = reason
    out["failed_test"] = test
    out["is_valid"] = reason.isna()
    return out


def event_row_results(flagged: pd.DataFrame):
    results = []
    for tid, name, _, _ in EVENT_ROW_TESTS:
        n = int((flagged["failed_test"] == tid).sum())
        results.append(TestResult(tid, name, QUARANTINE, "stg_events", n, n == 0,
                                  f"{n} row(s) quarantined" if n else ""))
    return results


# ---------------------------------------------------------------------------
# Table-level tests
# ---------------------------------------------------------------------------
ACCEPTED_MAG_TYPES = {"mb", "mw", "mwr", "mww", "mwc", "mwb", "ml", "md", "ms", "ms_20", "mb_lg", "mlg",
                      "mlv", "mi", "mh", "m", "mwp", "mww", "mint", "fa", "mlr"}
ACCEPTED_STATUS = {"reviewed", "automatic"}


def table_tests(events_clean: pd.DataFrame, places: pd.DataFrame, wiki: pd.DataFrame,
                facts_in_warehouse: int, nearest_distance: pd.Series | None = None):
    r = []
    n = len(events_clean)
    need = config.MIN_VALID_EVENTS
    r.append(TestResult("S1", f"at least {need:,} valid earthquakes", STOP, "stg_events",
                        0 if n >= need else need - n, n >= need, f"{n:,} valid earthquakes"))
    floor = int(facts_in_warehouse * 0.99)
    ok = n >= floor
    r.append(TestResult("S2", "no more than 1% fewer earthquakes than the warehouse holds", STOP, "stg_events",
                        0 if ok else floor - n, ok,
                        f"{n:,} now vs {facts_in_warehouse:,} loaded last time (an extract that silently "
                        "returned less data must not replace good data)"))
    dup = int(events_clean["event_id"].duplicated().sum() + events_clean["event_id"].isna().sum())
    r.append(TestResult("S3", "event_id unique and not null", STOP, "stg_events", dup, dup == 0))
    dup_p = int(places["place_id"].duplicated().sum() + places["place_id"].isna().sum())
    r.append(TestResult("S4", "place_id unique and not null", STOP, "stg_places", dup_p, dup_p == 0))
    bad_mt = events_clean["mag_type"].dropna()
    bad_mt = bad_mt[~bad_mt.isin(ACCEPTED_MAG_TYPES)]
    r.append(TestResult("W1", "magnitude type in the accepted list", WARN, "stg_events", len(bad_mt),
                        bad_mt.empty, ", ".join(f"{k}: {v}" for k, v in bad_mt.value_counts().items())))
    bad_st = events_clean["review_status"].fillna("missing")
    bad_st = bad_st[~bad_st.isin(ACCEPTED_STATUS)]
    r.append(TestResult("W2", "review status is reviewed or automatic", WARN, "stg_events", len(bad_st),
                        bad_st.empty, ", ".join(f"{k}: {v}" for k, v in bad_st.value_counts().items())))
    if nearest_distance is not None:
        far = int((nearest_distance > config.COUNTRY_MAX_DISTANCE_KM).sum())
        share = far / max(len(nearest_distance), 1)
        r.append(TestResult("W3", "at most 10% of earthquakes farther than 100 km from any town", WARN,
                            "events_enriched", far, share <= 0.10, f"{far} ({share:.1%}) at sea or remote"))
    default_depth = float(events_clean["depth_is_default"].mean()) if n else 0.0
    r.append(TestResult("W5", "at most 50% of depths fixed at the 10 km default", WARN, "stg_events",
                        int(events_clean["depth_is_default"].sum()), default_depth <= 0.5,
                        f"{default_depth:.0%} of depths are the USGS default: the depth classes are rough"))
    damaged = int(events_clean["place_text"].str.contains("?", regex=False, na=False).sum())
    r.append(TestResult("W6", "USGS place text without lost characters ('?')", WARN, "stg_events", damaged,
                        damaged == 0, f"{damaged} place texts lost their accented letters at the source; "
                        "the country comes from GeoNames, not from this text"))
    valid_wiki = int(wiki["is_valid"].sum()) if len(wiki) else 0
    r.append(TestResult("W4", "at least 50 dated historical earthquakes read from Wikipedia", WARN,
                        "stg_wiki_quakes", 0 if valid_wiki >= 50 else 50 - valid_wiki, valid_wiki >= 50,
                        f"{valid_wiki} valid rows"))
    return r


def relationship_sql():
    """R-tests, run in the warehouse before commit. Each query counts orphan rows."""
    return [
        ("R1", "fact_earthquake.date_key exists in dim_date",
         "SELECT COUNT(*) FROM fact_earthquake f LEFT JOIN dim_date d USING (date_key) WHERE d.date_key IS NULL"),
        ("R2", "fact_earthquake.place_key exists in dim_place",
         "SELECT COUNT(*) FROM fact_earthquake f LEFT JOIN dim_place p USING (place_key) WHERE p.place_key IS NULL"),
        ("R3", "fact_earthquake.country_key exists in dim_country",
         "SELECT COUNT(*) FROM fact_earthquake f LEFT JOIN dim_country c USING (country_key) "
         "WHERE c.country_key IS NULL"),
        ("R4", "fact_earthquake.magnitude_band_key exists in dim_magnitude_band",
         "SELECT COUNT(*) FROM fact_earthquake f LEFT JOIN dim_magnitude_band b USING (magnitude_band_key) "
         "WHERE b.magnitude_band_key IS NULL"),
        ("R5", "fact_earthquake.event_version_key is the current version in dim_event_version",
         "SELECT COUNT(*) FROM fact_earthquake f LEFT JOIN dim_event_version v "
         "ON v.event_version_key = f.event_version_key AND v.is_current = 1 WHERE v.event_version_key IS NULL"),
        ("R6", "fact_place_exposure rows point to an earthquake and a place that exist",
         "SELECT COUNT(*) FROM fact_place_exposure x LEFT JOIN fact_earthquake f USING (event_id) "
         "LEFT JOIN dim_place p ON p.place_key = x.place_key WHERE f.event_id IS NULL OR p.place_key IS NULL"),
    ]


def summarize(results):
    stops = [t for t in results if t.severity == STOP and not t.passed]
    for t in results:
        level = logging.INFO if t.passed else (logging.ERROR if t.severity == STOP else logging.WARNING)
        log.log(level, "test %-3s %-10s %-4s %s%s", t.test_id, t.severity, "PASS" if t.passed else "FAIL",
                t.name, f" | {t.details}" if t.details else "")
    return stops
