"""Source 1: USGS earthquake catalogue (FDSN event web service, GeoJSON).

Full extraction (first run, or --full)
    The study period is cut into calendar-year windows. For each window we ask
    /count first, then page through /query with limit and offset. Each page is
    saved untouched before it is parsed, and a checkpoint file lists the windows
    already on disk, so a crashed run continues where it stopped. Small, closed
    windows also keep offset paging safe: their content no longer moves.

Incremental extraction (every later run)
    Only events updated after the saved watermark, minus a one-day overlap, are
    requested (updatedafter). USGS revises events after publishing them
    (magnitude, location, review status), so the staging step keeps the newest
    version of each event id: a revised event replaces the old row, it is never
    appended twice. The watermark itself is written by run_pipeline.py only
    after the load has succeeded.
"""
import json
import logging
from datetime import date, datetime, timedelta, timezone

from src import config
from src.extract.manifest import save_raw

log = logging.getLogger(__name__)
MAX_ALLOWED = 20_000   # USGS refuses a query that matches more events than this


def _base_params(min_magnitude):
    return {"format": "geojson", "minmagnitude": min_magnitude, **config.BBOX}


def year_windows(start: str, end: str):
    """[start, end) cut at every 1 January: ('2016-11-14','2017-01-01'), ('2017-01-01', ...), ..."""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    windows = []
    while s < e:
        nxt = min(date(s.year + 1, 1, 1), e)
        windows.append((s.isoformat(), nxt.isoformat()))
        s = nxt
    return windows


def count_events(session, params) -> int:
    resp = session.get(f"{config.USGS_BASE}/count", params=params)
    return int(resp.json()["count"])


def fetch_pages(session, params, total, out_dir, prefix, run_id, page_size=None):
    """Page through /query with limit and offset; save each page before parsing it."""
    page_size = page_size or config.USGS_PAGE_SIZE
    files, features, offset, page = [], 0, 1, 1
    while offset <= total:
        q = {**params, "orderby": "time-asc", "limit": page_size, "offset": offset}
        resp = session.get(f"{config.USGS_BASE}/query", params=q)
        n = 0 if resp.status_code == 204 else len(resp.json().get("features", []))
        path = out_dir / f"{prefix}_p{page:03d}.geojson"
        save_raw(path, resp.content, source="usgs", url=resp.url, records=n, run_id=run_id,
                 params=q, status=resp.status_code)
        files.append(path)
        if n == 0:          # an empty page ends the loop even if /count promised more
            break
        features += n
        offset += n
        page += 1
        if page > 200:      # safety stop: never loop forever on a misbehaving API
            raise RuntimeError("more than 200 pages for one window: check the parameters")
    return files, features


def extract_full(session, run_id: str, run_date: date):
    """Every M4+ event in the box since STUDY_START, one year window at a time."""
    out_dir = config.RAW_DIR / "usgs" / "full" / run_date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = out_dir / "_checkpoint.json"
    done = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    if done:
        log.info("usgs full: checkpoint found, %d window(s) already on disk", len(done))

    total_features = 0
    end = (run_date + timedelta(days=1)).isoformat()
    for start, stop in year_windows(config.STUDY_START, end):
        name = f"{start}_{stop}"
        if name in done:
            total_features += done[name]["features"]
            continue
        params = {**_base_params(config.MIN_MAGNITUDE), "starttime": start, "endtime": stop}
        expected = count_events(session, params)
        if expected > MAX_ALLOWED:
            raise RuntimeError(f"{expected} events in {name}: use smaller windows")
        _, got = fetch_pages(session, params, expected, out_dir, name, run_id)
        if got != expected:
            log.warning("usgs full %s: /count said %d, pages held %d", name, expected, got)
        log.info("usgs full %s: %d events", name, got)
        total_features += got
        done[name] = {"count": expected, "features": got}
        checkpoint.write_text(json.dumps(done, indent=1))     # the save point
    return {"mode": "full", "folder": out_dir, "records": total_features}


def extract_incremental(session, run_id: str, watermark: datetime):
    """Events updated since the watermark minus the overlap window."""
    since = watermark - timedelta(days=config.OVERLAP_DAYS)
    params = {**_base_params(config.MIN_MAGNITUDE - config.REVISION_MARGIN),
              "starttime": config.STUDY_START,
              "updatedafter": since.strftime("%Y-%m-%dT%H:%M:%S")}
    out_dir = config.RAW_DIR / "usgs" / "incremental" / run_id
    expected = count_events(session, params)
    log.info("usgs incremental: %d event(s) updated after %s (watermark %s minus %d day overlap)",
             expected, params["updatedafter"], watermark.isoformat(), config.OVERLAP_DAYS)
    if expected > MAX_ALLOWED:
        raise RuntimeError("too many changes for one incremental run: run with --full")
    if expected == 0:
        # still record the (empty) answer, so the manifest shows the run happened
        resp = session.get(f"{config.USGS_BASE}/query", params={**params, "limit": 1})
        save_raw(out_dir / "updates_p001.geojson", resp.content, source="usgs", url=resp.url,
                 records=0, run_id=run_id, params=params, status=resp.status_code)
        return {"mode": "incremental", "folder": out_dir, "records": 0}
    _, got = fetch_pages(session, params, expected, out_dir, "updates", run_id)
    return {"mode": "incremental", "folder": out_dir, "records": got}


# ---- watermark ----------------------------------------------------------------
def watermark_path():
    return config.STATE_DIR / "usgs_watermark.json"


def read_watermark():
    p = watermark_path()
    if not p.exists():
        return None
    return datetime.fromisoformat(json.loads(p.read_text())["updated_max"])


def write_watermark(updated_max: datetime, run_id: str):
    """Called by run_pipeline.py after the load succeeded, never before."""
    p = watermark_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"updated_max": updated_max.astimezone(timezone.utc).isoformat(),
                             "run_id": run_id}, indent=1))
