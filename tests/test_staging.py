"""The staging steps: newest version kept, placeholders become missing, invalid rows flagged."""
import io
import zipfile

import pandas as pd

from src.transform import staging


def _raw(features):
    df = pd.json_normalize(features)
    coords = df.pop("geometry.coordinates")
    df["longitude"], df["latitude"], df["depth_km"] = coords.str[0], coords.str[1], coords.str[2]
    df["raw_file"] = "test/page.geojson"
    return df


def test_newest_version_of_a_revised_event_is_kept(make_feature, run_date):
    raw = _raw([make_feature("us1", mag=4.6, updated_ms=1_600_100_000_000),
                make_feature("us1", mag=4.9, updated_ms=1_700_000_000_000),   # revised later
                make_feature("us2", mag=5.1)])
    events, acc = staging.stg_events(raw, run_date)
    assert len(events) == 2 and acc["older_versions_removed"] == 1
    assert events.set_index("event_id").loc["us1", "magnitude"] == 4.9


def test_invalid_events_are_flagged_not_deleted(make_feature, run_date):
    raw = _raw([make_feature("ok"), make_feature("blast", etype="quarry blast"),
                make_feature("small", mag=3.8), make_feature("away", lat=10.0),
                make_feature("gone", status="deleted")])
    events, _ = staging.stg_events(raw, run_date)
    assert len(events) == 5                                   # nothing deleted in staging
    reasons = events.set_index("event_id")["failed_test"].to_dict()
    assert pd.isna(reasons["ok"])
    assert reasons["blast"] == "Q6" and reasons["small"] == "Q3"
    assert reasons["away"] == "Q4" and reasons["gone"] == "Q2"


def test_default_depth_is_marked(make_feature, run_date):
    events, _ = staging.stg_events(_raw([make_feature("a", depth=10.0), make_feature("b", depth=12.5)]), run_date)
    assert events.set_index("event_id")["depth_is_default"].to_dict() == {"a": True, "b": False}


def _geonames_zip(tmp_path, name, rows):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}.txt", "\n".join("\t".join(map(str, r)) for r in rows) + "\n")
    path = tmp_path / f"{name}.zip"
    path.write_bytes(buf.getvalue())
    return path


def _row(gid, name, code, cc, pop, dem="100", lat=35.0, lon=35.0):
    return [gid, name, name, "", lat, lon, "P", code, cc, "", "04", "", "", "", pop, "", dem, "Asia/Beirut",
            "2024-01-01"]


def test_geonames_placeholders_duplicates_and_sections(tmp_path):
    lb = _geonames_zip(tmp_path, "LB", [_row(1, "Beirut", "PPLC", "LB", 1916100),
                                        _row(2, "Achrafieh", "PPLX", "LB", 100000),   # a section of Beirut
                                        _row(3, "Village", "PPL", "LB", 0, dem="-9999")])
    world = _geonames_zip(tmp_path, "cities1000", [_row(1, "Beirut", "PPLC", "LB", 1916100),
                                                   _row(4, "Aleppo", "PPLA", "SY", 1602264, lat=36.2, lon=37.2)])
    frames = [staging.read_geonames_zip(lb), staging.read_geonames_zip(world)]
    places, acc = staging.stg_places(frames)
    p = places.set_index("place_id")
    assert acc["duplicates_removed"] == 1                     # Beirut is in both files
    assert p.loc[1, "source_file"] == "LB.zip"                # the country file is preferred
    assert p.loc[1, "admin1_code"] == "04"                    # codes keep their leading zero
    assert pd.isna(p.loc[3, "population"]) and pd.isna(p.loc[3, "dem_m"])   # 0 and -9999 are placeholders
    assert not p.loc[2, "is_valid"]                           # PPLX never counts as a settlement
    assert p.loc[4, "is_valid"]


def test_wikipedia_parsers():
    assert staging.parse_magnitude("7.8 Mw[3]") == 7.8
    assert staging.parse_magnitude("Mw 6.6–6.9") == 6.6
    assert pd.isna(staging.parse_magnitude("–"))
    assert staging.parse_count("50,783[12]") == 50783
    assert pd.isna(staging.parse_count("Unknown"))
    assert staging.parse_date("6 February 2023") == pd.Timestamp("2023-02-06")
    assert pd.isna(staging.parse_date("c. 1500"))
