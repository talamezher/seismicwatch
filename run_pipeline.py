"""SeismicWatch: one command runs the whole pipeline.

    python run_pipeline.py                 extract (incremental if a watermark exists), transform, test, load
    python run_pipeline.py --full          force a full USGS extraction
    python run_pipeline.py --skip-extract  rebuild staging and the warehouse from the raw layer only

Every run writes logs/pipeline_<run_id>.log and one row in the run_log table, and
can be re-run safely: the raw layer only grows, the staging tables are rebuilt,
and the warehouse load is an idempotent upsert in one transaction.
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone

import pandas as pd

from src import config
from src.extract import geonames, usgs, wikipedia
from src.extract.http import PoliteSession
from src.load import warehouse as wh
from src.quality import checks
from src.quality.checks import QUARANTINE, PipelineStop, TestResult
from src.transform import enrich, staging

log = logging.getLogger("seismicwatch")


def setup_logging(run_id):
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = config.LOG_DIR / f"pipeline_{run_id}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-28s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in (logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt)
        root.addHandler(h)
    return path


def extract(run_id, run_date, force_full):
    session = PoliteSession()
    watermark = usgs.read_watermark()
    if force_full or watermark is None:
        res_usgs = usgs.extract_full(session, run_id, run_date)
    else:
        res_usgs = usgs.extract_incremental(session, run_id, watermark)
    res_geo = geonames.extract_geonames(session, run_id, run_date)
    res_wiki = wikipedia.extract_wikipedia(session, run_id, run_date)
    log.info("extract: %d HTTP requests in total", session.requests_sent)
    return res_usgs, res_geo, res_wiki


def to_quarantine(df, source, key_col, test_col, reason_col):
    if df.empty:
        return pd.DataFrame(columns=["source", "record_key", "test_id", "severity", "reason", "record_json"])
    return pd.DataFrame({
        "source": source, "record_key": df[key_col].astype(str), "test_id": df[test_col],
        "severity": QUARANTINE, "reason": df[reason_col].astype(str),
        "record_json": [json.dumps(r, default=str) for r in df.drop(columns=[test_col]).to_dict("records")]})


def transform_and_test(run_id, run_date, conn):
    # ---- staging: one table per source -------------------------------------------
    raw_current = staging.read_usgs_raw(staging.usgs_raw_files())
    events, acc_e = staging.stg_events(raw_current, run_date)
    versions = staging.event_versions(staging.read_usgs_raw(staging.all_usgs_files()))
    frames = []
    for name in [*config.GEONAMES_COUNTRY_FILES, config.GEONAMES_NEIGHBOURS_FILE]:
        path = geonames.latest_file(name)
        if path is None:
            raise FileNotFoundError(f"no {name}.zip in the raw layer")
        frames.append(staging.read_geonames_zip(path))
    places, acc_p = staging.stg_places(frames)
    pages = [(p.stem, p.read_text(encoding="utf-8")) for p in wikipedia.latest_pages()]
    wiki, acc_w = staging.stg_wiki_quakes(pages)
    config.STAGING_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in [("stg_events", events), ("stg_places", places), ("stg_wiki_quakes", wiki)]:
        df.to_parquet(config.STAGING_DIR / f"{name}.parquet", index=False)
    log.info("staging: events %s | places %s | wikipedia %s", acc_e, acc_p, acc_w)

    # ---- row-level tests: quarantine ----------------------------------------------------
    results = checks.event_row_results(events)
    good_events = events[events["is_valid"]].drop(columns=["invalid_reason", "failed_test", "is_valid"])
    bad_events = events[~events["is_valid"]]
    bad_coords = places[places["invalid_reason"].eq("invalid coordinates")].assign(test="P1")
    results.append(TestResult("P1", "place coordinates valid", QUARANTINE, "stg_places", len(bad_coords),
                              bad_coords.empty))
    bad_wiki = wiki[wiki["invalid_reason"].eq("magnitude not readable")].assign(test="K1") if len(wiki) \
        else wiki
    results.append(TestResult("K1", "historical earthquake magnitude readable", QUARANTINE, "stg_wiki_quakes",
                              len(bad_wiki), len(bad_wiki) == 0))
    parts = [to_quarantine(bad_events, "usgs", "event_id", "failed_test", "invalid_reason"),
             to_quarantine(bad_coords, "geonames", "place_id", "test", "invalid_reason"),
             to_quarantine(bad_wiki, "wikipedia", "wiki_id", "test", "invalid_reason")]
    quarantine = pd.concat([q for q in parts if len(q)], ignore_index=True) if any(len(q) for q in parts) \
        else parts[0]
    good_places = places[places["is_valid"]]
    good_wiki = wiki[wiki["is_valid"]] if len(wiki) else wiki

    # ---- joins and business rules -------------------------------------------------------
    enriched, pairs, joins = enrich.build(good_events, good_places, good_wiki, run_date)

    # ---- table-level tests ------------------------------------------------------------------
    results += checks.table_tests(good_events, places, wiki, wh.fact_count(conn), enriched["distance_km"])
    loaded = len(enriched)
    balance = acc_e["raw_records"] - acc_e["older_versions_removed"] - len(bad_events) - loaded
    results.append(TestResult("S5", "row accounting: raw = older versions + quarantined + loaded", checks.STOP,
                              "pipeline", abs(balance), balance == 0,
                              f"{acc_e['raw_records']:,} raw = {acc_e['older_versions_removed']:,} older versions"
                              f" + {len(bad_events):,} quarantined + {loaded:,} loaded"))
    counts = {"raw_records": acc_e["raw_records"], "older_versions": acc_e["older_versions_removed"],
              "staged": acc_e["staged_events"], "quarantined": len(bad_events), "loaded": loaded,
              "watermark": raw_current_max_updated(raw_current)}
    return enriched, pairs, good_places, versions, quarantine, results, joins, counts


def raw_current_max_updated(raw):
    return pd.to_datetime(raw["properties.updated"], unit="ms", utc=True).max().to_pydatetime()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help="force a full USGS extraction")
    ap.add_argument("--skip-extract", action="store_true", help="rebuild from the raw layer, no network")
    ap.add_argument("--run-date", help="as-of date for the rules (YYYY-MM-DD); default: today (UTC)")
    args = ap.parse_args(argv)

    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    run_date = pd.Timestamp(args.run_date).date() if args.run_date else started.date()
    log_path = setup_logging(run_id)
    mode = "replay" if args.skip_extract else ("full" if args.full or usgs.read_watermark() is None
                                               else "incremental")
    log.info("run %s | mode %s | as of %s | log %s", run_id, mode, run_date, log_path)
    conn = wh.connect()
    wh.start_run(conn, run_id, started.isoformat(timespec="seconds"), mode)
    t0 = time.monotonic()
    extracted = None
    results = []
    try:
        if not args.skip_extract:
            res_usgs, _, _ = extract(run_id, run_date, args.full)
            extracted = res_usgs["records"]
        (enriched, pairs, places, versions, quarantine, results, joins,
         counts) = transform_and_test(run_id, pd.Timestamp(run_date), conn)
        wh.write_join_log(conn, run_id, joins)
        wh.write_quarantine(conn, run_id, started.isoformat(timespec="seconds"), quarantine)
        stops = checks.summarize(results)
        if stops:
            raise PipelineStop("; ".join(f"{t.test_id} {t.name}" for t in stops))

        star = wh.build_star(enriched, pairs, places, versions, conn)
        rel, deleted = wh.load_star(conn, star)
        checks.summarize(rel)
        results += rel
        fp = wh.table_fingerprint(conn)
        for table, v in fp.items():
            log.info("warehouse %-20s %8s rows  sha256 %s", table, f"{v['rows']:,}", v["sha256"])
        if any(deleted.values()):
            log.info("rows deleted because they left the source: %s", {k: v for k, v in deleted.items() if v})
        if mode != "replay":
            usgs.write_watermark(counts["watermark"], run_id)      # only after a successful load
            log.info("watermark moved to %s", counts["watermark"].isoformat())
        wh.write_test_results(conn, run_id, results)
        log.info("ROWS  raw USGS records %s -> older versions %s -> quarantined %s -> loaded %s",
                 f"{counts['raw_records']:,}", f"{counts['older_versions']:,}", f"{counts['quarantined']:,}",
                 f"{counts['loaded']:,}")
        wh.finish_run(conn, run_id, finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      status="success", records_extracted=extracted, events_staged=counts["staged"],
                      events_loaded=counts["loaded"], events_quarantined=counts["quarantined"],
                      tests_failed=sum(not r.passed for r in results), warehouse_hash=wh.combined_hash(fp),
                      message=f"{time.monotonic() - t0:.0f} s")
        log.info("run %s finished: success in %.0f s, warehouse fingerprint %s", run_id, time.monotonic() - t0,
                 wh.combined_hash(fp))
        return 0
    except PipelineStop as exc:
        log.error("STOP: %s. Nothing was loaded; the warehouse keeps the last good data.", exc)
        wh.write_test_results(conn, run_id, results)
        wh.finish_run(conn, run_id, finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      status="stopped", records_extracted=extracted, tests_failed=sum(not r.passed for r in results),
                      message=str(exc)[:500])
        return 2
    except Exception as exc:
        log.exception("run %s failed", run_id)
        wh.finish_run(conn, run_id, finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      status="failed", records_extracted=extracted, message=f"{type(exc).__name__}: {exc}"[:500])
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
