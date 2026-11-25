"""Business rules: test the edges, where real data rarely sits (Week 6)."""
import pandas as pd
import pytest

from src.transform import rules
from src.transform.geo import haversine_km


def test_haversine_known_distance():
    # Beirut (33.8938, 35.5018) to Tripoli, Lebanon (34.4367, 35.8497): about 69 km
    d = haversine_km(33.8938, 35.5018, 34.4367, 35.8497)
    assert d == pytest.approx(69.0, abs=1.5)
    assert haversine_km(36.0, 36.0, 36.0, 36.0) == 0


@pytest.mark.parametrize("mag, band", [(4.0, 1), (4.99, 1), (5.0, 2), (5.99, 2), (6.0, 3), (7.8, 3)])
def test_magnitude_band_edges(mag, band):
    assert rules.magnitude_band(pd.Series([mag])).iloc[0] == band


def test_magnitude_below_four_has_no_band():
    assert rules.magnitude_band(pd.Series([3.99])).isna().iloc[0]


def test_country_rule_at_exactly_100_km():
    out = rules.assign_country(pd.Series(["TR", "TR", "SY"]), pd.Series([100.0, 100.01, 12.0]))
    assert out.tolist() == ["TR", rules.UNASSIGNED, "SY"]


def test_exposure_counts_and_population_by_radius():
    pairs = pd.DataFrame({"event_id": ["e1", "e1", "e1", "e2"], "place_id": [1, 2, 3, 1],
                          "distance_km": [5.0, 25.0, 40.0, 50.0]})
    pop = pd.Series({1: 1000, 2: 200, 3: 30})
    out = rules.exposure(pairs, pop).set_index("event_id")
    assert out.loc["e1", "places_within_25km"] == 2          # 25 km itself is inside
    assert out.loc["e1", "population_within_25km"] == 1200
    assert out.loc["e1", "population_within_50km"] == 1230
    assert out.loc["e2", "places_within_25km"] == 0 and out.loc["e2", "population_within_50km"] == 1000


def test_depth_classes():
    assert rules.depth_class(pd.Series([10.0, 69.9, 70.0, 300.0, 300.1])).tolist() == \
        ["shallow", "shallow", "intermediate", "intermediate", "deep"]


def test_wikipedia_match_window_and_tie_break():
    events = pd.DataFrame({"event_id": ["a", "b", "c"],
                           "event_time_utc": pd.to_datetime(["2023-02-06 01:17", "2023-02-06 10:24",
                                                             "2020-01-24 17:55"], utc=True),
                           "magnitude": [7.8, 7.5, 6.7]})
    wiki = pd.DataFrame({"wiki_id": [1, 2, 3], "quake_date": pd.to_datetime(["2023-02-06", "2023-02-06",
                                                                             "2020-01-26"]),
                         "magnitude": [7.8, 7.7, 6.7]})
    m = rules.match_wikipedia(events, wiki).set_index("event_id")["wiki_id"].to_dict()
    assert m["a"] == 1           # same day, exact magnitude beats the 7.7 row
    assert m["b"] == 2           # 7.5 vs 7.7 is within 0.3
    assert "c" not in m          # two days apart: outside the +/- 1 day window


def test_study_period_uses_the_date_passed_in():
    t = pd.Series(pd.to_datetime(["2016-11-13 23:59", "2016-11-14 00:00", "2026-11-13 23:59",
                                  "2026-11-14 00:00"], utc=True))
    assert rules.in_study_period(t, pd.Timestamp("2030-01-01")).tolist() == [False, True, True, False]
    # an earlier as-of date ends the period earlier: the rule never reads the clock
    assert not rules.in_study_period(t, pd.Timestamp("2020-01-01")).iloc[2]
