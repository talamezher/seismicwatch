"""Combine the three staging tables and apply the business rules.

Join 1  earthquake -> nearest populated place (GeoNames), then -> that place's row
Join 2  earthquake -> Wikipedia historical list (documented major earthquakes)
Join 3  earthquake x places within 50 km -> place rows (the exposure table)

Every join is a left join from the table whose rows must all be kept, with
validate= and a row count before and after (Week 6: the duplicate trap).
"""
import logging

import pandas as pd

from src import config
from src.transform import rules
from src.transform.geo import nearest_and_within

log = logging.getLogger(__name__)


class JoinCheck:
    """Records every join so the run log and the report show the counts."""

    def __init__(self):
        self.rows = []

    def merge(self, name, left, right, **kw):
        before = len(left)
        out = left.merge(right, **kw)
        assert len(out) == before, f"{name}: {before:,} rows in, {len(out):,} out"
        unmatched = int((out["_merge"] == "left_only").sum()) if kw.get("indicator") else None
        self.rows.append({"join": name, "rows_before": before, "rows_after": len(out), "unmatched": unmatched})
        log.info("join %-38s %7s rows in, %7s out%s", name, f"{before:,}", f"{len(out):,}",
                 f", {unmatched:,} without a match" if unmatched is not None else "")
        return out


def build(events: pd.DataFrame, places: pd.DataFrame, wiki: pd.DataFrame, run_date):
    """events: valid staged earthquakes; places: valid settlements; wiki: valid list rows."""
    jc = JoinCheck()
    radius = max(config.EXPOSURE_RADII_KM)
    nearest, pairs = nearest_and_within(events, places, radius)

    # ---- Join 1: nearest populated place ---------------------------------------
    ev = jc.merge("events -> nearest place (distance)", events, nearest, on="event_id", how="left",
                  validate="one_to_one")
    place_cols = places[["place_id", "name", "country_code", "population"]].rename(
        columns={"place_id": "nearest_place_id", "name": "nearest_place", "country_code": "nearest_country",
                 "population": "nearest_population"})
    ev = jc.merge("events -> nearest place (attributes)", ev, place_cols, on="nearest_place_id", how="left",
                  validate="many_to_one", indicator=True).drop(columns="_merge")

    # ---- business rules ------------------------------------------------------------
    ev["country_code"] = rules.assign_country(ev["nearest_country"], ev["distance_km"])        # BR-1
    ev["is_study_country"] = ev["country_code"].isin(config.STUDY_COUNTRIES)
    ev["magnitude_band_key"] = rules.magnitude_band(ev["magnitude"])                           # BR-2
    ev["depth_class"] = rules.depth_class(ev["depth_km"])                                       # BR-5
    ev["in_study_period"] = rules.in_study_period(ev["event_time_utc"], run_date)               # BR-6

    # ---- BR-3: exposure, from the event x place pairs -----------------------------------
    pop = places.set_index("place_id")["population"]
    exp = rules.exposure(pairs, pop)
    ev = jc.merge("events -> exposure counts", ev, exp, on="event_id", how="left", validate="one_to_one")
    for c in exp.columns.drop("event_id"):
        ev[c] = ev[c].fillna(0).astype("int64")

    # ---- Join 2: Wikipedia list (BR-4) --------------------------------------------------
    matches = rules.match_wikipedia(ev, wiki)
    w = wiki.set_index("wiki_id")[["quake_date", "place", "magnitude", "deaths"]].rename(
        columns={"quake_date": "wiki_date", "place": "wiki_place", "magnitude": "wiki_magnitude",
                 "deaths": "wiki_deaths"}).reset_index()
    m = matches.merge(w, on="wiki_id", how="left", validate="many_to_one")
    ev = jc.merge("events -> Wikipedia historical list", ev, m, on="event_id", how="left",
                  validate="one_to_one", indicator=True)
    ev["is_documented"] = ev["_merge"].eq("both")
    ev = ev.drop(columns="_merge")

    # ---- Join 3: exposure pairs -> place attributes ---------------------------------------
    pairs = jc.merge("exposure pairs -> places", pairs,
                     places[["place_id", "country_code"]].rename(columns={"country_code": "place_country"}),
                     on="place_id", how="left", validate="many_to_one")
    pairs["within_25km"] = pairs["distance_km"] <= min(config.EXPOSURE_RADII_KM)
    log.info("exposure: %s earthquake-place pairs within %d km", f"{len(pairs):,}", radius)
    return ev, pairs, jc.rows
