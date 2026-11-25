"""Settings shared by every step of the pipeline.

Values that change between machines (contact e-mail, data folder) come from the
.env file; everything that defines the question (study area, period, thresholds)
is written here once, so the extractors, the rules and the dashboard agree.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

# ---- folders ---------------------------------------------------------------
DATA_DIR = Path(os.getenv("SEISMICWATCH_DATA_DIR", ROOT / "data"))
if not DATA_DIR.is_absolute():
    DATA_DIR = ROOT / DATA_DIR
RAW_DIR = DATA_DIR / "raw"            # exactly what the sources sent, never edited
STAGING_DIR = DATA_DIR / "staging"    # one Parquet file per staging table, rebuilt every run
STATE_DIR = DATA_DIR / "state"        # watermark of the incremental extraction
WAREHOUSE_PATH = DATA_DIR / "warehouse" / "seismicwatch.db"
LOG_DIR = Path(os.getenv("SEISMICWATCH_LOG_DIR", ROOT / "logs"))
MANIFEST_PATH = RAW_DIR / "manifest.jsonl"

# ---- politeness ------------------------------------------------------------
CONTACT_EMAIL = os.getenv("SEISMICWATCH_CONTACT_EMAIL", "")
REPO_URL = "https://github.com/talamezher/seismicwatch"
USER_AGENT = (f"SeismicWatch/1.0 (DSC 620 student project, Lebanese American University; +{REPO_URL}"
              + (f"; {CONTACT_EMAIL})" if CONTACT_EMAIL else ")"))
REQUEST_TIMEOUT = 60          # seconds; requests has no default timeout
MIN_SECONDS_BETWEEN_REQUESTS = 1.0
MAX_RETRIES = 5

# ---- the question ------------------------------------------------------------
STUDY_START = "2016-11-14"    # ten years back from the proposal date
STUDY_END = "2026-11-14"      # end of the period the report answers (the pipeline keeps going)
MIN_MAGNITUDE = 4.0
MIN_VALID_EVENTS = int(os.getenv("SEISMICWATCH_MIN_EVENTS", "1000"))   # stop test S1 (the CI sample sets it lower)
STUDY_COUNTRIES = {"LB": "Lebanon", "TR": "Turkey", "CY": "Cyprus"}
# Box sent to the USGS API: Lebanon, Cyprus and all of Turkey with a margin.
BBOX = {"minlatitude": 32.5, "maxlatitude": 42.5, "minlongitude": 25.5, "maxlongitude": 45.0}

# ---- USGS extraction ---------------------------------------------------------
USGS_BASE = "https://earthquake.usgs.gov/fdsnws/event/1"
USGS_PAGE_SIZE = int(os.getenv("USGS_PAGE_SIZE", "1000"))   # the API allows up to 20,000
OVERLAP_DAYS = 1              # incremental runs re-read one day before the watermark
REVISION_MARGIN = 0.5         # incremental runs ask for M >= 3.5, to see events revised below 4.0

# ---- GeoNames ----------------------------------------------------------------
GEONAMES_BASE = "https://download.geonames.org/export/dump"
GEONAMES_COUNTRY_FILES = ["LB", "TR", "CY"]   # every populated place of the three countries
GEONAMES_NEIGHBOURS_FILE = "cities1000"        # towns >= 1,000 people worldwide, for the neighbours

# ---- Wikipedia ---------------------------------------------------------------
WIKI_PAGES = ["https://en.wikipedia.org/wiki/List_of_earthquakes_in_Turkey"]
WIKI_ROBOTS = "https://en.wikipedia.org/robots.txt"

# ---- business rules (documented in docs/business_rules.md) ---------------------
COUNTRY_MAX_DISTANCE_KM = 100     # BR-1
EXPOSURE_RADII_KM = (25, 50)      # BR-3
WIKI_MATCH_DAYS = 1               # BR-4
WIKI_MATCH_MAGNITUDE = 0.3        # BR-4
