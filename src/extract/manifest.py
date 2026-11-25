"""The raw layer: save what a source sent, untouched, and record where it came from.

Every raw file gets one line in data/raw/manifest.jsonl: source, URL, parameters,
time, HTTP status, number of records, size and SHA-256. The hash shows when a
source re-issued the same file name with different contents.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from src import config


def save_raw(path: Path, content: bytes, *, source: str, url: str, records: int, run_id: str,
             params: dict | None = None, status: int | None = None, manifest=None) -> dict:
    """Write the bytes exactly as received and append a manifest entry.

    Writing to the same path again (a re-run of the same extraction) replaces the
    file, so a re-run never stores a second copy.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(content)
    tmp.replace(path)                    # atomic: never a half-written raw file
    entry = {
        "run_id": run_id,
        "source": source,
        "url": url,
        "params": params or {},
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "http_status": status,
        "records": int(records),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "file": path.relative_to(config.DATA_DIR).as_posix() if path.is_relative_to(config.DATA_DIR)
        else path.as_posix(),
    }
    manifest = Path(manifest or config.MANIFEST_PATH)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def read_manifest(manifest=None):
    manifest = Path(manifest or config.MANIFEST_PATH)
    if not manifest.exists():
        return []
    return [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
