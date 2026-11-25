"""Cut the small sample of REAL raw files used by the CI end-to-end run (tests/fixtures/raw).

    python scripts/make_fixtures.py <data/raw of a real run>

Records are copied as they are (no value is changed): about 200 earthquakes of 2023,
including the duplicate pair found by profiling, the populated places within 60 km of
them, the last incremental page, and the Wikipedia page.
"""
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "raw"
KEEP_IDS = {"us70009ad8", "us70009adb"}          # the same earthquake under two ids (test Q7)


def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * np.arcsin(np.sqrt(a))


def main(raw):
    raw = Path(raw)
    if OUT.exists():
        shutil.rmtree(OUT)
    full = sorted((raw / "usgs" / "full").iterdir())[-1]
    feats = []
    for f in sorted(full.glob("*.geojson")):
        for ft in json.loads(f.read_text(encoding="utf-8"))["features"]:
            if ft["id"] in KEEP_IDS:
                feats.append(ft)
    year = json.loads((full / "2023-01-01_2024-01-01_p001.geojson").read_text(encoding="utf-8"))
    rng = np.random.default_rng(620)
    pick = rng.choice(len(year["features"]), 200, replace=False)
    feats += [year["features"][i] for i in sorted(pick)]
    out_full = OUT / "usgs" / "full" / full.name
    out_full.mkdir(parents=True)
    (out_full / "sample_p001.geojson").write_text(json.dumps({**year, "features": feats}), encoding="utf-8")
    inc = sorted((raw / "usgs" / "incremental").iterdir())[-1]
    shutil.copytree(inc, OUT / "usgs" / "incremental" / inc.name)

    lat = np.array([f["geometry"]["coordinates"][1] for f in feats])
    lon = np.array([f["geometry"]["coordinates"][0] for f in feats])
    geo_dir = sorted((raw / "geonames").iterdir())[-1]
    (OUT / "geonames" / geo_dir.name).mkdir(parents=True)
    for z in sorted(geo_dir.glob("*.zip")):
        keep = []
        with zipfile.ZipFile(z) as zf, zf.open(z.stem + ".txt") as fh:
            for line in io.TextIOWrapper(fh, encoding="utf-8"):
                parts = line.split("\t")
                if parts[6] != "P":
                    continue
                if haversine(float(parts[4]), float(parts[5]), lat, lon).min() <= 60:
                    keep.append(line)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(z.stem + ".txt", "".join(keep))
        (OUT / "geonames" / geo_dir.name / z.name).write_bytes(buf.getvalue())
        print(z.name, len(keep), "places")
    wiki = sorted((raw / "wikipedia").iterdir())[-1]
    shutil.copytree(wiki, OUT / "wikipedia" / wiki.name)
    print(len(feats), "earthquakes ->", OUT)


if __name__ == "__main__":
    main(sys.argv[1])
