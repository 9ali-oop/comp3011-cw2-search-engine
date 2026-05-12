"""Polite, robots-aware web crawler for the search-engine tool.

The crawler walks every page on a single host that is reachable from a
seed URL, returning the raw HTML for each page so that the indexer can
process it.  It is deliberately decoupled from the indexer: ``crawl()``
hands back ``{url: html}`` and nothing more.

Design highlights
-----------------
* **Politeness window** — at least ``delay`` seconds between successive
  HTTP requests (default 6, per the COMP3011 brief).  Tests override this
  to 0.
* **robots.txt** — fetched once on construction and consulted before
  every request via :class:`urllib.robotparser.RobotFileParser`.
* **URL normalisation** — strip fragments, lower-case host, drop default
  ports, collapse a missing path to ``/``.  This avoids re-fetching the
  same page under a different spelling.
* **Same-host restriction** — never follow links off the seed's host.
* **Resilience** — transient network failures are retried with
  exponential back-off; permanent failures are logged and skipped so a
  single bad page does not abort the crawl.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "COMP3011-SearchEngineBot/1.0 "
    "(+https://github.com/ -- university coursework)"
)
DEFAULT_DELAY = 6.0          # seconds — required by the brief
DEFAULT_TIMEOUT = 10.0       # seconds per request
DEFAULT_MAX_RETRIES = 2      # attempts after the initial try
DEFAULT_BACKOFF = 1.5        # multiplier between retries


@dataclass
class CrawlResult:
    """Aggregate output of a single ``Crawler.crawl()`` invocation."""

    pages: dict[str, str] = field(default_factory=dict)
    """Mapping of normalised URL → HTML body."""

    failed: dict[str, str] = field(default_factory=dict)
    """Mapping of URL → human-readable error message."""

    disallowed: list[str] = field(default_factory=list)
    """URLs we refused to fetch because of robots.txt."""

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.pages)


def normalise_url(url: str, base: str | None = None) -> str:
    """Return a canonical form of *url*.

    * Resolved against *base* if it is relative.
    * Fragment (``#section``) discarded.
    * Scheme + host lower-cased.
    * Default ports (80 for http, 443 for https) dropped.
    * Empty path replaced with ``/``.
    * Trailing whitespace stripped.

    >>> normalise_url("HTTP://Example.com:80/A/?b=1#x")
    'http://example.com/A/?b=1'
    """
    if base is not None:
        url = urljoin(base, url)
    url, _ = urldefrag(url.strip())
    parts = urlparse(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    # Drop default ports.
    if (scheme == "http" and netloc.endswith(":80")) or (
        scheme == "https" and netloc.endswith(":443")
    ):
        netloc = netloc.rsplit(":", 1)[0]
    path = parts.path or "/"
    return urlunparse((scheme, netloc, path, parts.params, parts.query, ""))


def same_host(url: str, host: str) -> bool:
    """``True`` iff *url* is on the same host as *host* (case-insensitive)."""
    parsed = urlparse(url)
    return parsed.netloc.lower() == host.lower()


def extract_links(html: str, base_url: str) -> list[str]:
    """Return every absolute, normalised ``<a href>`` link in *html*."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not href or href.startswith(("mailto:", "javascript:", "tel:")):
            continue
        try:
            out.append(normalise_url(href, base=base_url))
        except ValueError:
            # urlparse can raise on truly malformed input on some
            # Python versions.  Skip silently — bad links are noise.
            continue
    return out


class Crawler:
    """Breadth-first crawler restricted to a single host.

    Parameters
    ----------
    seed_url
        Where to start.  The host of this URL bounds the crawl.
    delay
        Seconds to wait between successive HTTP requests.  Defaults to
        :data:`DEFAULT_DELAY` (6) so production behaviour respects the
        brief; tests pass ``0`` to keep the suite fast.
    user_agent
        ``User-Agent`` header sent on every request.
    timeout, max_retries, backoff
        Networking knobs.  See module-level ``DEFAULT_*`` constants.
    session
        Optional pre-built :class:`requests.Session`.  Tests pass a
        custom session; production code lets the crawler build its own.
    sleep
        Injected sleep function so tests can verify the politeness logic
        without actually waiting.
    obey_robots
        If ``False`` skip the robots.txt check entirely (useful for
        unit-testing the rest of the crawler).
    """

    def __init__(
        self,
        seed_url: str,
        *,
        delay: float = DEFAULT_DELAY,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff: float = DEFAULT_BACKOFF,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        obey_robots: bool = True,
    ) -> None:
        self.seed_url = normalise_url(seed_url)
        self.host = urlparse(self.seed_url).netloc
        self.delay = max(0.0, float(delay))
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = self.user_agent
        self._sleep = sleep
        self._last_request_time: float | None = None
        self._robots: RobotFileParser | None = None
        if obey_robots:
            self._load_robots()

    # ----- robots.txt --------------------------------------------------

    def _load_robots(self) -> None:
        """Fetch /robots.txt once and stash the parser.

        Failures are non-fatal — if the file cannot be retrieved we
        default to allowing everything, matching the conservative
        behaviour of the stdlib parser.
        """
        parsed = urlparse(self.seed_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = RobotFileParser()
        rp.set_url(robots_url)
        try:
            resp = self.session.get(robots_url, timeout=self.timeout)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                rp.parse([])  # empty rules -> allow all
        except requests.RequestException as exc:
            logger.warning("robots.txt fetch failed (%s); allowing all", exc)
            rp.parse([])
        self._robots = rp

    def allowed(self, url: str) -> bool:
        """Whether the crawler's user-agent may fetch *url*."""
        if self._robots is None:
            return True
        return self._robots.can_fetch(self.user_agent, url)

    # ----- politeness --------------------------------------------------

    def _wait_for_politeness(self) -> None:
        """Sleep just enough to keep ``self.delay`` between requests."""
        if self._last_request_time is None or self.delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request_time
        remaining = self.delay - elapsed
        if remaining > 0:
            self._sleep(remaining)

    # ----- networking --------------------------------------------------

    def fetch(self, url: str) -> str | None:
        """Return HTML for *url* or ``None`` on failure.

        Retries transient errors (connection / timeout / 5xx) up to
        ``self.max_retries`` times with exponential back-off.  4xx
        responses are treated as permanent.
        """
        self._wait_for_politeness()
        attempt = 0
        wait = self.backoff
        while True:
            try:
                resp = self.session.get(url, timeout=self.timeout)
                self._last_request_time = time.monotonic()
                if resp.status_code == 200:
                    # Only index text/html; binary content gives garbage.
                    ctype = resp.headers.get("Content-Type", "")
                    if "html" not in ctype and ctype != "":
                        logger.info("skip non-HTML %s (%s)", url, ctype)
                        return None
                    return resp.text
                if 400 <= resp.status_code < 500:
                    logger.info("HTTP %s for %s — giving up", resp.status_code, url)
                    return None
                # 5xx — retry.
                logger.info("HTTP %s for %s — retry %s", resp.status_code, url, attempt)
            except requests.RequestException as exc:
                self._last_request_time = time.monotonic()
                logger.info("network error for %s: %s", url, exc)
            attempt += 1
            if attempt > self.max_retries:
                return None
            self._sleep(wait)
            wait *= self.backoff

    # ----- crawl driver ------------------------------------------------

    def crawl(
        self,
        max_pages: int | None = None,
        on_page: Callable[[str, str], None] | None = None,
    ) -> CrawlResult:
        """Breadth-first walk starting at the seed.

        Parameters
        ----------
        max_pages
            If given, stop after *max_pages* successful fetches.  Used
            by demos and the test-suite to keep crawls short.
        on_page
            Optional callback invoked as ``on_page(url, html)`` for each
            successful fetch — handy for live progress reporting.
        """
        result = CrawlResult()
        frontier: deque[str] = deque([self.seed_url])
        seen: set[str] = {self.seed_url}

        while frontier:
            if max_pages is not None and len(result.pages) >= max_pages:
                break
            url = frontier.popleft()

            if not self.allowed(url):
                result.disallowed.append(url)
                logger.info("robots.txt disallows %s", url)
                continue

            html = self.fetch(url)
            if html is None:
                result.failed[url] = "fetch failed"
                continue

            result.pages[url] = html
            if on_page is not None:
                on_page(url, html)

            for link in extract_links(html, url):
                if link in seen:
                    continue
                if not same_host(link, self.host):
                    continue
                seen.add(link)
                frontier.append(link)

        return result


__all__ = [
    "Crawler",
    "CrawlResult",
    "DEFAULT_DELAY",
    "DEFAULT_USER_AGENT",
    "extract_links",
    "normalise_url",
    "same_host",
]
