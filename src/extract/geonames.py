"""Source 2: GeoNames gazetteer dumps (zipped, tab-separated UTF-8 text, no header).

LB.zip, TR.zip and CY.zip hold every place of the three study countries.
cities1000.zip holds every town of 1,000 people or more worldwide: we need it
for the neighbours (Syria, Greece, Iraq...), otherwise an earthquake in Syria
near the border would be given to the closest Turkish or Lebanese town.

GeoNames regenerates the files every day. We send If-Modified-Since with the
date of our last copy: when the answer is 304 nothing is downloaded, and the
staging step keeps using the last saved copy.
"""
import io
import json
import logging
import zipfile
from datetime import date

from src import config
from src.extract.manifest import save_raw

log = logging.getLogger(__name__)


def _http_state_path():
    return config.STATE_DIR / "geonames_last_modified.json"


def count_lines(zip_bytes: bytes, member: str) -> int:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf, zf.open(member) as f:
        return sum(1 for _ in f)


def extract_geonames(session, run_id: str, run_date: date, names=None, force=False):
    names = names or [*config.GEONAMES_COUNTRY_FILES, config.GEONAMES_NEIGHBOURS_FILE]
    state_path = _http_state_path()
    last_modified = json.loads(state_path.read_text()) if state_path.exists() else {}
    out_dir = config.RAW_DIR / "geonames" / run_date.isoformat()
    results = {}
    for name in names:
        url = f"{config.GEONAMES_BASE}/{name}.zip"
        headers = {}
        if not force and name in last_modified and latest_file(name) is not None:
            headers["If-Modified-Since"] = last_modified[name]
        resp = session.get(url, headers=headers)
        if resp.status_code == 304:
            log.info("geonames %s: unchanged since %s, keeping %s", name, last_modified[name],
                     latest_file(name).parent.name)
            results[name] = {"downloaded": False, "records": None}
            continue
        if not zipfile.is_zipfile(io.BytesIO(resp.content)):
            raise RuntimeError(f"{url} did not return a zip file")
        n = count_lines(resp.content, f"{name}.txt")
        save_raw(out_dir / f"{name}.zip", resp.content, source="geonames", url=url, records=n,
                 run_id=run_id, status=resp.status_code)
        if resp.headers.get("Last-Modified"):
            last_modified[name] = resp.headers["Last-Modified"]
        log.info("geonames %s: %d lines, %.1f MB", name, n, len(resp.content) / 1e6)
        results[name] = {"downloaded": True, "records": n}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(last_modified, indent=1))
    return results


def latest_file(name: str):
    """Most recent saved copy of NAME.zip in the raw layer (folders are named by date)."""
    copies = sorted((config.RAW_DIR / "geonames").glob(f"*/{name}.zip"))
    return copies[-1] if copies else None
