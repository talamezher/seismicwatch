"""Extraction: retries, politeness, paging, the raw layer and the watermark, without the network."""
import json
from datetime import date, datetime, timezone

import pytest
import requests

from src.extract import manifest, usgs
from src.extract.http import PoliteSession, SourceError


class FakeResponse:
    def __init__(self, status, payload=None, headers=None, url="https://example.org/x"):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.url = url
        self.content = json.dumps(self._payload).encode()
        self.text = self.content.decode()

    def json(self):
        return self._payload


def session_with(responses):
    s = PoliteSession(sleep=lambda s: None)
    calls = iter(responses)
    s.session.get = lambda *a, **k: next(calls)
    return s


def test_429_and_500_are_retried_then_succeed():
    s = session_with([FakeResponse(429, headers={"Retry-After": "3"}), FakeResponse(503), FakeResponse(200, {"ok": 1})])
    assert s.get("https://example.org/x").json() == {"ok": 1}


def test_400_is_not_retried():
    s = session_with([FakeResponse(400), FakeResponse(200)])
    with pytest.raises(SourceError):
        s.get("https://example.org/x")


def test_gives_up_after_max_retries():
    s = session_with([FakeResponse(500)] * 10)
    with pytest.raises(SourceError):
        s.get("https://example.org/x")


def test_user_agent_has_contact_and_requests_are_spaced():
    waits = []
    s = PoliteSession(sleep=waits.append, min_interval=1.0)
    s.session.get = lambda *a, **k: FakeResponse(200)
    s.get("https://example.org/a")
    s.get("https://example.org/b")
    assert "SeismicWatch" in s.session.headers["User-Agent"]
    assert waits and 0 < waits[0] <= 1.0                 # the second call waited its turn


def test_year_windows_cover_the_period_without_gaps():
    w = usgs.year_windows("2016-11-14", "2019-03-02")
    assert w[0] == ("2016-11-14", "2017-01-01") and w[-1] == ("2019-01-01", "2019-03-02")
    assert all(a[1] == b[0] for a, b in zip(w, w[1:]))


def test_paging_saves_every_page_and_stops_on_empty(tmp_config):
    pages = [FakeResponse(200, {"features": [{"id": i} for i in range(2)]}),
             FakeResponse(200, {"features": [{"id": 9}]})]
    s = session_with(pages)
    files, n = usgs.fetch_pages(s, {"format": "geojson"}, total=3, out_dir=tmp_config.RAW_DIR / "usgs" / "t",
                                prefix="w", run_id="r1", page_size=2)
    assert n == 3 and len(files) == 2 and all(f.exists() for f in files)
    entries = manifest.read_manifest()
    assert [e["records"] for e in entries] == [2, 1] and all(len(e["sha256"]) == 64 for e in entries)


def test_rerun_of_the_same_raw_file_replaces_it(tmp_config):
    p = tmp_config.RAW_DIR / "geonames" / "2026-11-25" / "LB.zip"
    manifest.save_raw(p, b"first", source="geonames", url="u", records=1, run_id="a")
    manifest.save_raw(p, b"second", source="geonames", url="u", records=1, run_id="b")
    assert p.read_bytes() == b"second" and len(list(p.parent.iterdir())) == 1


def test_watermark_round_trip_and_overlap(tmp_config, monkeypatch):
    wm = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    usgs.write_watermark(wm, "run1")
    assert usgs.read_watermark() == wm
    seen = {}

    def fake_count(session, params):
        seen.update(params)
        return 0
    monkeypatch.setattr(usgs, "count_events", fake_count)
    s = session_with([FakeResponse(200, {"features": []})])
    usgs.extract_incremental(s, "run2", wm)
    assert seen["updatedafter"] == "2026-11-19T12:00:00"          # one day of overlap
    assert seen["minmagnitude"] == pytest.approx(3.5)              # sees events revised below 4.0


def test_full_extraction_resumes_from_its_checkpoint(tmp_config, monkeypatch):
    monkeypatch.setattr(usgs.config, "STUDY_START", "2025-11-01")
    out = tmp_config.RAW_DIR / "usgs" / "full" / "2026-01-10"
    out.mkdir(parents=True)
    (out / "_checkpoint.json").write_text(json.dumps({"2025-11-01_2026-01-01": {"count": 5, "features": 5}}))
    asked = []
    monkeypatch.setattr(usgs, "count_events", lambda s, p: asked.append(p["starttime"]) or 0)
    res = usgs.extract_full(session_with([]), "r", date(2026, 1, 10))
    assert asked == ["2026-01-01"]          # the finished window was not asked again
    assert res["records"] == 5


def test_requests_timeout_is_set():
    s = PoliteSession(sleep=lambda x: None)
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["timeout"] = timeout
        return FakeResponse(200)
    s.session.get = fake_get
    s.get("https://example.org")
    assert captured["timeout"] and captured["timeout"] > 0


def test_connection_errors_are_retried():
    s = PoliteSession(sleep=lambda x: None)
    outcomes = iter([requests.ConnectionError("down"), FakeResponse(200, {"ok": True})])

    def flaky(*a, **k):
        o = next(outcomes)
        if isinstance(o, Exception):
            raise o
        return o
    s.session.get = flaky
    assert s.get("https://example.org").json() == {"ok": True}
