"""End-to-end integration tests.

The HTTP layer is still mocked, but unlike the unit tests these ones
exercise the crawler + indexer + search engine together against a
miniature fake site so we catch wiring bugs that single-module tests
cannot.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import responses

from src.crawler import Crawler
from src.indexer import Indexer
from src.search import SearchEngine


SITE = "http://fake.test"

# Tiny site that exercises every interesting case:
#   /        — seed, links to /a, /b, /missing, /tag/love/
#   /a       — links to /b (already seen)
#   /b       — leaf
#   /missing — 404
#   /tag/love/ — disallowed by robots.txt
ROBOTS = "User-agent: *\nDisallow: /tag/"
HTML_HOME = """
<html><body>
  <a href='/a'>a</a>
  <a href='/b'>b</a>
  <a href='/missing'>m</a>
  <a href='/tag/love/'>love</a>
  <p>Indifference is a poison.</p>
</body></html>
"""
HTML_A = "<html><body><a href='/b'>b</a><p>Good friends matter.</p></body></html>"
HTML_B = "<html><body><p>The cat sat on the mat.</p></body></html>"


def _wire_mock_site():
    responses.add(responses.GET, f"{SITE}/robots.txt", body=ROBOTS, status=200)
    responses.add(responses.GET, f"{SITE}/", body=HTML_HOME, content_type="text/html")
    responses.add(responses.GET, f"{SITE}/a", body=HTML_A, content_type="text/html")
    responses.add(responses.GET, f"{SITE}/b", body=HTML_B, content_type="text/html")
    responses.add(responses.GET, f"{SITE}/missing", status=404)


class TestEndToEnd:
    @responses.activate
    def test_full_pipeline(self, tmp_path):
        _wire_mock_site()
        crawler = Crawler(f"{SITE}/", delay=0, obey_robots=True, sleep=lambda _: None)
        result = crawler.crawl()

        # Three good pages, one 404, one robots-disallowed.
        assert set(result.pages) == {f"{SITE}/", f"{SITE}/a", f"{SITE}/b"}
        assert f"{SITE}/missing" in result.failed
        assert f"{SITE}/tag/love/" in result.disallowed

        indexer = Indexer()
        indexer.add_documents(result.pages)
        engine = SearchEngine(indexer)

        # Sanity: every brief example query returns something sensible.
        assert {h.url for h in engine.find("indifference")} == {f"{SITE}/"}
        assert {h.url for h in engine.find("good friends")} == {f"{SITE}/a"}
        # The classic full-word vs. substring check on this corpus.
        assert engine.find("friend") == []   # 'friends' is not 'friend'

        # Round-trip the index through disk and re-query.
        path = tmp_path / "index.json"
        indexer.save(path)
        reloaded = Indexer.load(path)
        engine2 = SearchEngine(reloaded)
        assert (
            {h.url for h in engine.find("good friends")}
            == {h.url for h in engine2.find("good friends")}
        )

    @responses.activate
    def test_print_then_find_consistency(self, tmp_path):
        # Build a tiny index and verify that every URL `print` reports
        # for a term is also returned by `find` on the same term.
        _wire_mock_site()
        crawler = Crawler(f"{SITE}/", delay=0, obey_robots=False, sleep=lambda _: None)
        indexer = Indexer()
        indexer.add_documents(crawler.crawl().pages)
        engine = SearchEngine(indexer)

        for term in ("cat", "good", "friends", "indifference"):
            print_urls = {p.url for p in engine.print_term(term)}
            find_urls = {h.url for h in engine.find(term)}
            assert print_urls == find_urls, term

    @responses.activate
    def test_indexing_is_idempotent_across_save_load(self, tmp_path):
        _wire_mock_site()
        crawler = Crawler(f"{SITE}/", delay=0, obey_robots=False, sleep=lambda _: None)
        pages = crawler.crawl().pages

        a = Indexer()
        a.add_documents(pages)
        a.save(tmp_path / "i.json")

        b = Indexer.load(tmp_path / "i.json")

        # Every term and every posting must agree.
        assert a.num_docs() == b.num_docs()
        assert set(a.terms) == set(b.terms)
        for t in a.terms:
            assert a.terms[t]["df"] == b.terms[t]["df"]
            assert a.terms[t]["postings"] == b.terms[t]["postings"]


class TestPerformance:
    """Lightweight performance smoke tests.

    Not strict benchmarks — they exist so a regression that turns the
    indexer or query path quadratic gets caught in CI rather than at
    submission time.
    """

    def test_index_100_docs_fast(self):
        idx = Indexer()
        # Each doc has ~50 distinct tokens; 100 docs -> ~5k token operations.
        for i in range(100):
            html = "<body>" + " ".join(f"word{j}" for j in range(50)) + f" doc{i}</body>"
            idx.add_document(f"http://x/{i}", html)
        assert idx.num_docs() == 100

    def test_find_on_large_index_fast(self):
        idx = Indexer()
        for i in range(200):
            html = f"<body>common term doc{i}</body>"
            idx.add_document(f"http://x/{i}", html)
        eng = SearchEngine(idx)
        # AND query that intersects across every doc.
        start = time.monotonic()
        hits = eng.find("common term")
        elapsed = time.monotonic() - start
        assert len(hits) == 200
        assert elapsed < 0.5  # Generous; tightens regressions

    def test_save_load_large_index(self, tmp_path):
        idx = Indexer()
        for i in range(200):
            html = f"<body>doc{i} body content here common word</body>"
            idx.add_document(f"http://x/{i}", html)
        path = tmp_path / "i.json"
        idx.save(path)
        loaded = Indexer.load(path)
        assert loaded.num_docs() == 200
