"""Business rules: decisions, written down once, with their thresholds in src/config.py.

Every rule is a plain function of its inputs. None of them reads the clock: the
run date is passed in, so a past run can be rebuilt and every edge can be tested
(tests/test_rules.py). docs/business_rules.md explains each one.
"""
import numpy as np
import pandas as pd

from src import config

UNASSIGNED = "XX"    # country code used when no populated place is within reach


def assign_country(nearest_country: pd.Series, distance_km: pd.Series,
                   max_km: float = config.COUNTRY_MAX_DISTANCE_KM) -> pd.Series:
    """BR-1. An earthquake belongs to the country of its nearest populated place,
    if that place is at most max_km (100 km) away; otherwise it is 'XX' (at sea or
    remote). Only LB, TR and CY count for the question; the other countries
    (Syria, Greece, Iran...) are kept so the map stays honest."""
    within = distance_km <= max_km
    return nearest_country.where(within, UNASSIGNED)


MAGNITUDE_BANDS = [
    # key, label, lower bound (included), upper bound (excluded), USGS class
    (1, "4.0-4.9", 4.0, 5.0, "light"),
    (2, "5.0-5.9", 5.0, 6.0, "moderate"),
    (3, "6.0+", 6.0, 99.0, "strong or larger"),
]


def magnitude_band(magnitude: pd.Series) -> pd.Series:
    """BR-2. Bands 4.0-4.9, 5.0-5.9 and 6.0+, lower bound included: 5.0 is 'moderate'.
    Returns the band key (1, 2, 3), or <NA> below 4.0."""
    conditions = [(magnitude >= lo) & (magnitude < hi) for _, _, lo, hi, _ in MAGNITUDE_BANDS]
    keys = [k for k, *_ in MAGNITUDE_BANDS]
    out = np.select(conditions, keys, default=0)
    return pd.Series(out, index=magnitude.index).replace(0, pd.NA).astype("Int64")


def exposure(pairs: pd.DataFrame, place_population: pd.Series, radii=config.EXPOSURE_RADII_KM) -> pd.DataFrame:
    """BR-3. Exposure of an earthquake = the settlements within 25 km and 50 km of the
    epicentre, and the sum of their populations (GeoNames). Sections of a city
    (PPLX) are not settlements, so a city's people are never counted twice.
    pairs: event_id, place_id, distance_km (already limited to the largest radius)."""
    p = pairs.assign(population=pairs["place_id"].map(place_population).astype(float))
    out = pd.DataFrame(index=pd.Index(p["event_id"].unique(), name="event_id"))
    for r in radii:
        inside = p[p["distance_km"] <= r]
        g = inside.groupby("event_id")
        out[f"places_within_{r}km"] = g.size()
        out[f"population_within_{r}km"] = g["population"].sum()
    return out.fillna(0).astype("int64").reset_index()


def depth_class(depth_km: pd.Series) -> pd.Series:
    """BR-5. Standard seismology classes: shallow (< 70 km), intermediate (70-300 km),
    deep (> 300 km). Shallow earthquakes do the most damage at the surface."""
    return pd.Series(np.select([depth_km < 70, depth_km <= 300], ["shallow", "intermediate"], "deep"),
                     index=depth_km.index).where(depth_km.notna())


def match_wikipedia(events: pd.DataFrame, wiki: pd.DataFrame,
                    max_days: int = config.WIKI_MATCH_DAYS,
                    max_magnitude_diff: float = config.WIKI_MATCH_MAGNITUDE) -> pd.DataFrame:
    """BR-4. A USGS event is a documented major earthquake when a row of the Wikipedia
    list has the same date (+/- 1 day, UTC) and the same magnitude (+/- 0.3). When
    several rows qualify, the closest date wins, then the closest magnitude.
    Returns one row per matched event: event_id, wiki_id."""
    if wiki.empty:
        return pd.DataFrame(columns=["event_id", "wiki_id"])
    e = events[["event_id", "event_time_utc", "magnitude"]].copy()
    e["day"] = e["event_time_utc"].dt.tz_convert(None).dt.normalize()
    w = wiki[["wiki_id", "quake_date", "magnitude"]].rename(columns={"magnitude": "wiki_magnitude"})
    cands = []
    for shift in range(-max_days, max_days + 1):
        ww = w.assign(day=w["quake_date"] + pd.Timedelta(days=shift), day_diff=abs(shift))
        cands.append(e.merge(ww, on="day", how="inner"))
    c = pd.concat(cands, ignore_index=True)
    c["mag_diff"] = (c["magnitude"] - c["wiki_magnitude"]).abs()
    c = c[c["mag_diff"] <= max_magnitude_diff + 1e-9]
    c = c.sort_values(["event_id", "day_diff", "mag_diff", "wiki_id"])
    return c.drop_duplicates("event_id")[["event_id", "wiki_id"]].reset_index(drop=True)


def in_study_period(event_time_utc: pd.Series, as_of) -> pd.Series:
    """BR-6. The question covers STUDY_START (included) to STUDY_END (excluded); an event
    after the run date cannot exist yet. as_of is passed in, never read from the clock."""
    start = pd.Timestamp(config.STUDY_START, tz="UTC")
    end = min(pd.Timestamp(config.STUDY_END, tz="UTC"), _utc(as_of) + pd.Timedelta(days=1))
    return (event_time_utc >= start) & (event_time_utc < end)


def _utc(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
