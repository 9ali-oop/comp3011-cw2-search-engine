"""Unit tests for the crawler.

All HTTP traffic is mocked with the ``responses`` library so the suite
runs offline and finishes in milliseconds.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests
import responses

from src.crawler import (
    Crawler,
    CrawlResult,
    DEFAULT_DELAY,
    extract_links,
    normalise_url,
    same_host,
)


# normalise_url

class TestNormaliseUrl:
    def test_lowercases_scheme_and_host(self):
        assert (
            normalise_url("HTTP://Example.COM/Foo")
            == "http://example.com/Foo"
        )

    def test_strips_fragment(self):
        assert (
            normalise_url("http://example.com/page#section")
            == "http://example.com/page"
        )

    def test_keeps_query_string(self):
        assert (
            normalise_url("http://example.com/p?x=1&y=2")
            == "http://example.com/p?x=1&y=2"
        )

    def test_resolves_relative_against_base(self):
        assert (
            normalise_url("/about", base="http://example.com/blog/post")
            == "http://example.com/about"
        )

    def test_collapses_default_http_port(self):
        assert (
            normalise_url("http://example.com:80/path")
            == "http://example.com/path"
        )

    def test_collapses_default_https_port(self):
        assert (
            normalise_url("https://example.com:443/x")
            == "https://example.com/x"
        )

    def test_keeps_non_default_port(self):
        assert (
            normalise_url("http://example.com:8080/x")
            == "http://example.com:8080/x"
        )

    def test_empty_path_becomes_slash(self):
        assert (
            normalise_url("http://example.com")
            == "http://example.com/"
        )

    def test_trims_whitespace(self):
        assert (
            normalise_url("  http://example.com/x  ")
            == "http://example.com/x"
        )


# same_host

class TestSameHost:
    def test_same(self):
        assert same_host("http://example.com/a", "example.com")

    def test_different(self):
        assert not same_host("http://other.com/a", "example.com")

    def test_case_insensitive(self):
        assert same_host("http://EXAMPLE.com/a", "example.com")


# extract_links

class TestExtractLinks:
    def test_picks_relative_and_absolute(self):
        html = """
        <a href='/about'>x</a>
        <a href='http://example.com/external'>y</a>
        <a href='page/2'>z</a>
        """
        links = extract_links(html, "http://example.com/")
        assert "http://example.com/about" in links
        assert "http://example.com/external" in links
        assert "http://example.com/page/2" in links

    def test_skips_mailto_and_javascript(self):
        html = """
        <a href='mailto:x@y.com'>m</a>
        <a href='javascript:void(0)'>j</a>
        <a href='/real'>r</a>
        """
        links = extract_links(html, "http://example.com/")
        assert links == ["http://example.com/real"]

    def test_handles_no_links(self):
        assert extract_links("<p>no anchors</p>", "http://example.com/") == []

    def test_strips_fragment_in_extracted_link(self):
        html = "<a href='/foo#top'>x</a>"
        assert extract_links(html, "http://example.com/") == [
            "http://example.com/foo"
        ]

    def test_handles_malformed_html(self):
        # BS4 is tolerant - make sure we don't blow up.
        html = "<a href='/ok'>x<a href='/two'><p>ohno"
        links = extract_links(html, "http://example.com/")
        assert "http://example.com/ok" in links
        assert "http://example.com/two" in links


# Crawler integration (mocked HTTP)

BASE = "http://example.com"


def _make_crawler(**overrides) -> Crawler:
    """Helper: build a crawler with politeness disabled and retry-sleep stubbed."""
    overrides.setdefault("sleep", lambda _: None)
    return Crawler(
        f"{BASE}/",
        delay=0,
        obey_robots=False,
        **overrides,
    )


class TestCrawlerBasics:
    @responses.activate
    def test_visits_seed(self):
        responses.add(
            responses.GET,
            f"{BASE}/",
            body="<html><body>hi</body></html>",
            content_type="text/html",
            status=200,
        )
        result = _make_crawler().crawl()
        assert f"{BASE}/" in result.pages
        assert result.failed == {}

    @responses.activate
    def test_follows_internal_links(self):
        responses.add(
            responses.GET,
            f"{BASE}/",
            body="<a href='/a'>a</a>",
            content_type="text/html",
        )
        responses.add(
            responses.GET,
            f"{BASE}/a",
            body="<a href='/b'>b</a>",
            content_type="text/html",
        )
        responses.add(
            responses.GET,
            f"{BASE}/b",
            body="<p>leaf</p>",
            content_type="text/html",
        )
        result = _make_crawler().crawl()
        assert set(result.pages) == {f"{BASE}/", f"{BASE}/a", f"{BASE}/b"}

    @responses.activate
    def test_does_not_follow_external_links(self):
        responses.add(
            responses.GET,
            f"{BASE}/",
            body="<a href='http://other.com/x'>x</a>",
            content_type="text/html",
        )
        # No mock for other.com - would raise ConnectionError if visited.
        result = _make_crawler().crawl()
        assert set(result.pages) == {f"{BASE}/"}

    @responses.activate
    def test_dedups_duplicate_urls(self):
        # Two anchors with the same href + a fragment-only variant.
        responses.add(
            responses.GET,
            f"{BASE}/",
            body=(
                "<a href='/a'>1</a>"
                "<a href='/a'>2</a>"
                "<a href='/a#section'>3</a>"
            ),
            content_type="text/html",
        )
        responses.add(
            responses.GET,
            f"{BASE}/a",
            body="ok",
            content_type="text/html",
        )
        result = _make_crawler().crawl()
        # /a fetched exactly once.
        assert sum(1 for c in responses.calls if c.request.url.endswith("/a")) == 1
        assert set(result.pages) == {f"{BASE}/", f"{BASE}/a"}

    @responses.activate
    def test_max_pages_stops_crawl(self):
        responses.add(
            responses.GET,
            f"{BASE}/",
            body="<a href='/a'>a</a><a href='/b'>b</a>",
            content_type="text/html",
        )
        responses.add(responses.GET, f"{BASE}/a", body="x", content_type="text/html")
        responses.add(responses.GET, f"{BASE}/b", body="x", content_type="text/html")
        result = _make_crawler().crawl(max_pages=2)
        assert len(result.pages) == 2

    @responses.activate
    def test_404_marked_as_failed(self):
        responses.add(responses.GET, f"{BASE}/", status=404)
        result = _make_crawler().crawl()
        assert result.pages == {}
        assert f"{BASE}/" in result.failed

    @responses.activate
    def test_500_retried_then_fails(self):
        # 3 attempts (initial + 2 retries) -> all 500.
        responses.add(responses.GET, f"{BASE}/", status=500)
        responses.add(responses.GET, f"{BASE}/", status=500)
        responses.add(responses.GET, f"{BASE}/", status=500)
        c = _make_crawler()
        result = c.crawl()
        assert result.failed
        # Used all three attempts.
        assert len(responses.calls) == 3

    @responses.activate
    def test_500_then_200_succeeds(self):
        responses.add(responses.GET, f"{BASE}/", status=500)
        responses.add(
            responses.GET,
            f"{BASE}/",
            body="ok",
            content_type="text/html",
            status=200,
        )
        result = _make_crawler().crawl()
        assert f"{BASE}/" in result.pages

    @responses.activate
    def test_connection_error_retried(self):
        responses.add(responses.GET, f"{BASE}/", body=requests.ConnectionError())
        responses.add(
            responses.GET,
            f"{BASE}/",
            body="ok",
            content_type="text/html",
            status=200,
        )
        result = _make_crawler().crawl()
        assert f"{BASE}/" in result.pages

    @responses.activate
    def test_timeout_failure(self):
        # All attempts raise -> URL gives up.
        for _ in range(5):
            responses.add(responses.GET, f"{BASE}/", body=requests.Timeout())
        result = _make_crawler().crawl()
        assert result.pages == {}
        assert f"{BASE}/" in result.failed

    @responses.activate
    def test_non_html_content_skipped(self):
        responses.add(
            responses.GET,
            f"{BASE}/",
            body="<a href='/img.png'>x</a>",
            content_type="text/html",
        )
        responses.add(
            responses.GET,
            f"{BASE}/img.png",
            body=b"\x89PNG\r\n",
            content_type="image/png",
        )
        result = _make_crawler().crawl()
        # PNG enqueued, fetched, but skipped because content-type is not HTML.
        assert f"{BASE}/img.png" not in result.pages

    @responses.activate
    def test_returns_crawlresult_dataclass(self):
        responses.add(responses.GET, f"{BASE}/", body="x", content_type="text/html")
        result = _make_crawler().crawl()
        assert isinstance(result, CrawlResult)


# Politeness

class TestPoliteness:
    @responses.activate
    def test_sleeps_between_requests(self):
        responses.add(
            responses.GET,
            f"{BASE}/",
            body="<a href='/a'>a</a>",
            content_type="text/html",
        )
        responses.add(responses.GET, f"{BASE}/a", body="ok", content_type="text/html")
        slept: list[float] = []
        crawler = Crawler(
            f"{BASE}/",
            delay=6,
            obey_robots=False,
            sleep=lambda s: slept.append(s),
        )
        crawler.crawl()
        # First fetch no wait, second fetch should sleep a positive amount.
        assert slept, "expected at least one politeness sleep"
        assert max(slept) > 0

    @responses.activate
    def test_zero_delay_does_not_sleep(self):
        responses.add(
            responses.GET,
            f"{BASE}/",
            body="<a href='/a'>a</a>",
            content_type="text/html",
        )
        responses.add(responses.GET, f"{BASE}/a", body="ok", content_type="text/html")
        slept: list[float] = []
        crawler = Crawler(
            f"{BASE}/",
            delay=0,
            obey_robots=False,
            sleep=lambda s: slept.append(s),
        )
        crawler.crawl()
        assert slept == []

    def test_default_delay_matches_brief(self):
        # The brief mandates ≥ 6 seconds. The default must respect this.
        assert DEFAULT_DELAY >= 6.0


# robots.txt

class TestRobots:
    @responses.activate
    def test_robots_disallow_blocks_url(self):
        responses.add(
            responses.GET,
            f"{BASE}/robots.txt",
            body="User-agent: *\nDisallow: /private/",
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE}/",
            body="<a href='/private/x'>p</a><a href='/public'>q</a>",
            content_type="text/html",
        )
        responses.add(responses.GET, f"{BASE}/public", body="ok", content_type="text/html")
        crawler = Crawler(f"{BASE}/", delay=0, obey_robots=True)
        result = crawler.crawl()
        assert f"{BASE}/public" in result.pages
        assert f"{BASE}/private/x" in result.disallowed
        # No actual fetch was made to /private/x.
        assert all("private" not in c.request.url for c in responses.calls)

    @responses.activate
    def test_robots_missing_allows_all(self):
        responses.add(responses.GET, f"{BASE}/robots.txt", status=404)
        responses.add(
            responses.GET,
            f"{BASE}/",
            body="<a href='/a'>a</a>",
            content_type="text/html",
        )
        responses.add(responses.GET, f"{BASE}/a", body="ok", content_type="text/html")
        crawler = Crawler(f"{BASE}/", delay=0, obey_robots=True)
        result = crawler.crawl()
        assert f"{BASE}/a" in result.pages

    @responses.activate
    def test_robots_network_failure_allows_all(self):
        responses.add(
            responses.GET,
            f"{BASE}/robots.txt",
            body=requests.ConnectionError(),
        )
        responses.add(responses.GET, f"{BASE}/", body="ok", content_type="text/html")
        # Should not raise even though robots.txt blew up.
        crawler = Crawler(f"{BASE}/", delay=0, obey_robots=True)
        result = crawler.crawl()
        assert f"{BASE}/" in result.pages


# Misc safety

class TestCrawlerMisc:
    def test_negative_delay_clamped_to_zero(self):
        c = Crawler(f"{BASE}/", delay=-5, obey_robots=False)
        assert c.delay == 0.0

    def test_user_agent_in_session(self):
        c = Crawler(f"{BASE}/", delay=0, obey_robots=False, user_agent="MyBot/1.0")
        assert c.session.headers["User-Agent"] == "MyBot/1.0"

    @responses.activate
    def test_on_page_callback_invoked(self):
        responses.add(
            responses.GET,
            f"{BASE}/",
            body="hi",
            content_type="text/html",
        )
        captured: list[tuple[str, str]] = []
        c = _make_crawler()
        c.crawl(on_page=lambda u, h: captured.append((u, h)))
        assert captured == [(f"{BASE}/", "hi")]
