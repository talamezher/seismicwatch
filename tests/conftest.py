import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Point every folder of the pipeline at a temporary directory."""
    from src import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(config, "STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(config, "MANIFEST_PATH", tmp_path / "raw" / "manifest.jsonl")
    monkeypatch.setattr(config, "WAREHOUSE_PATH", tmp_path / "warehouse" / "test.db")
    return config


def usgs_feature(event_id, mag=4.5, lat=38.0, lon=38.0, depth=10.0, time_ms=1_600_000_000_000,
                 updated_ms=1_600_100_000_000, status="reviewed", etype="earthquake", mag_type="mww"):
    return {"type": "Feature", "id": event_id,
            "properties": {"mag": mag, "place": "test", "time": time_ms, "updated": updated_ms,
                           "status": status, "type": etype, "magType": mag_type, "net": "us", "sig": 300,
                           "felt": None, "tsunami": 0, "alert": None, "nst": None, "gap": 20.0, "rms": 0.8},
            "geometry": {"type": "Point", "coordinates": [lon, lat, depth]}}


@pytest.fixture
def make_feature():
    return usgs_feature


@pytest.fixture
def run_date():
    return pd.Timestamp("2026-11-25")
