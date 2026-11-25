"""Source 3: Wikipedia, "List of earthquakes in Turkey" (web page, HTML tables).

Scraping is the last resort, so it follows the course rules: robots.txt is read
first (with our own User-Agent: Wikipedia refuses the default urllib one), the
User-Agent names the project and a contact address, there is at least one
second between requests, and the HTML is saved untouched before pandas reads it.
The page text is CC BY-SA 4.0; we credit Wikipedia in the README and the report.
"""
import logging
from datetime import date
from io import StringIO
from urllib.parse import unquote, urlparse
from urllib.robotparser import RobotFileParser

import pandas as pd

from src import config
from src.extract.manifest import save_raw

log = logging.getLogger(__name__)


class RobotsDisallowed(RuntimeError):
    pass


def robots_parser(session, robots_url=config.WIKI_ROBOTS) -> RobotFileParser:
    resp = session.get(robots_url)
    parser = RobotFileParser(robots_url)
    parser.parse(resp.text.splitlines())
    return parser


def earthquake_tables(html: str):
    """The tables of the page that have a magnitude column."""
    tables = pd.read_html(StringIO(html))
    return [t for t in tables if any("mag" in str(c).lower() for c in t.columns) and len(t) >= 3]


def extract_wikipedia(session, run_id: str, run_date: date, pages=None):
    pages = pages or config.WIKI_PAGES
    robots = robots_parser(session)
    out_dir = config.RAW_DIR / "wikipedia" / run_date.isoformat()
    results = {}
    for url in pages:
        if not robots.can_fetch(config.USER_AGENT, url):
            raise RobotsDisallowed(f"robots.txt does not allow {url}")
        resp = session.get(url)
        title = unquote(urlparse(url).path.rsplit("/", 1)[-1])
        rows = sum(len(t) for t in earthquake_tables(resp.text))
        save_raw(out_dir / f"{title}.html", resp.content, source="wikipedia", url=url, records=rows,
                 run_id=run_id, status=resp.status_code)
        log.info("wikipedia %s: robots.txt allows it, %d earthquake rows in its tables", title, rows)
        results[title] = rows
    return results


def latest_pages():
    """Most recent saved copy of each page."""
    latest = {}
    for p in sorted((config.RAW_DIR / "wikipedia").glob("*/*.html")):
        latest[p.name] = p
    return list(latest.values())
