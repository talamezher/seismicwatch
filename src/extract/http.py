"""One polite HTTP client for every source.

- an honest User-Agent with a contact address on every request;
- a timeout on every request (requests has none by default);
- at least one second between two requests to the same host;
- 429 and 5xx are retried with exponential backoff (Retry-After is honoured);
- any other 4xx is our bug: it is raised at once and never retried.
"""
import logging
import time
from urllib.parse import urlparse

import requests

from src import config

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class SourceError(RuntimeError):
    """A source answered with an error we must not retry, or kept failing."""


class PoliteSession:
    def __init__(self, user_agent=config.USER_AGENT, min_interval=config.MIN_SECONDS_BETWEEN_REQUESTS,
                 max_retries=config.MAX_RETRIES, timeout=config.REQUEST_TIMEOUT, sleep=time.sleep):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self.sleep = sleep            # replaced by a no-op in the unit tests
        self._last_call = {}          # host -> time of the last request
        self.requests_sent = 0

    def _wait_turn(self, host):
        last = self._last_call.get(host)
        if last is not None:
            gap = time.monotonic() - last
            if gap < self.min_interval:
                self.sleep(self.min_interval - gap)
        self._last_call[host] = time.monotonic()

    def get(self, url, params=None, headers=None):
        host = urlparse(url).netloc
        for attempt in range(1, self.max_retries + 1):
            self._wait_turn(host)
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
                self.requests_sent += 1
            except (requests.ConnectionError, requests.Timeout) as exc:
                wait = 2 ** attempt
                log.warning("%s: %s (attempt %d/%d), retrying in %ds", host, type(exc).__name__,
                            attempt, self.max_retries, wait)
                self.sleep(wait)
                continue
            if resp.status_code in RETRY_STATUS:
                wait = _retry_after(resp) or 2 ** attempt
                log.warning("%s answered %d (attempt %d/%d), retrying in %ds", host, resp.status_code,
                            attempt, self.max_retries, wait)
                self.sleep(wait)
                continue
            if resp.status_code >= 400:
                # 400, 401, 403, 404...: retrying the same request cannot help
                raise SourceError(f"{resp.status_code} from {resp.url}: {resp.text[:200]}")
            return resp
        raise SourceError(f"{url} still failing after {self.max_retries} attempts")


def _retry_after(resp):
    value = resp.headers.get("Retry-After")
    if value and value.isdigit():
        return min(int(value), 120)
    return None
