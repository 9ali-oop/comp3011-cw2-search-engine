"""Unit tests for the indexer."""

from __future__ import annotations

import json

import pytest

from src.indexer import (
    DEFAULT_STOPWORDS,
    DocumentRecord,
    INDEX_SCHEMA_VERSION,
    Indexer,
)


# ---------------------------------------------------------------------
# Tokenisation
# ---------------------------------------------------------------------

class TestTokenise:
    def test_lowercases(self):
        idx = Indexer(stopwords=frozenset())
        assert idx.tokenise("Hello WORLD") == ["hello", "world"]

    def test_splits_on_non_alphanumeric(self):
        idx = Indexer(stopwords=frozenset())
        assert idx.tokenise("foo, bar!baz?qux") == ["foo", "bar", "baz", "qux"]

    def test_keeps_digits(self):
        idx = Indexer(stopwords=frozenset())
        assert idx.tokenise("ver 3 has 42 things") == [
            "ver", "3", "has", "42", "things",
        ]

    def test_splits_apostrophes(self):
        idx = Indexer(stopwords=frozenset())
        # The brief says split on non-alphanumeric. Apostrophe counts.
        assert idx.tokenise("don't") == ["don", "t"]

    def test_drops_stopwords(self):
        idx = Indexer(stopwords=frozenset({"the", "a"}))
        assert idx.tokenise("The cat sat on a mat") == ["cat", "sat", "on", "mat"]

    def test_empty_string(self):
        assert Indexer().tokenise("") == []

    def test_only_stopwords(self):
        idx = Indexer(stopwords=frozenset({"the", "and"}))
        assert idx.tokenise("the and the") == []

    def test_unicode_outside_az09_dropped(self):
        # Brief specifies alphanumeric. Non-ASCII letters and emoji get split out.
        idx = Indexer(stopwords=frozenset())
        # café — the é is non-alphanumeric in our [a-z0-9] regex, so we get "caf"
        tokens = idx.tokenise("café 🚀 hello")
        assert tokens == ["caf", "hello"]


# ---------------------------------------------------------------------
# Boilerplate stripping
# ---------------------------------------------------------------------

class TestExtractText:
    def test_strips_script_and_style(self):
        html = """
        <html><head><style>.x{color:red}</style></head>
        <body>visible<script>alert(1)</script></body></html>
        """
        text = Indexer.extract_text(html)
        assert "alert" not in text
        assert ".x{color:red}" not in text
        assert "visible" in text

    def test_strips_nav_header_footer_aside(self):
        html = """
        <html><body>
          <header>SITE TITLE</header>
          <nav><a href='/login'>Login</a></nav>
          <main>real content here</main>
          <aside>sidebar</aside>
          <footer>copyright 2026</footer>
        </body></html>
        """
        text = Indexer.extract_text(html)
        assert "real content" in text
        assert "SITE TITLE" not in text
        assert "Login" not in text
        assert "sidebar" not in text
        assert "copyright" not in text

    def test_strips_quotes_toscrape_chrome(self):
        # The actual structure of a quotes.toscrape.com page.
        html = """
        <body>
          <div class='container'>
            <div class='row header-box'>
              <h1><a href='/'>Quotes to Scrape</a></h1>
              <a href='/login'>Login</a>
            </div>
            <div class='quote'>
              <span class='text'>“The world as we have created it”</span>
              <small class='author'>Albert Einstein</small>
            </div>
            <div class='tags-box'><h2>Top Ten tags</h2><span>love</span></div>
          </div>
        </body>
        """
        text = Indexer.extract_text(html)
        assert "Albert Einstein" in text
        assert "world" in text
        assert "Login" not in text
        assert "Quotes to Scrape" not in text
        assert "Top Ten tags" not in text

    def test_strips_html_comments(self):
        html = "<body>visible<!-- HIDDEN STUFF --></body>"
        assert "HIDDEN" not in Indexer.extract_text(html)

    def test_title_tag_not_in_extracted_text(self):
        # Every page on quotes.toscrape.com has the same <title>;
        # leaking it would skew every term frequency. The body-confined
        # extraction must keep it out of the term stream.
        html = (
            "<html><head><title>Quotes to Scrape</title></head>"
            "<body>real content</body></html>"
        )
        text = Indexer.extract_text(html)
        assert "real content" in text
        assert "Quotes to Scrape" not in text

    def test_handles_malformed_html(self):
        html = "<p>unclosed <b>bold <a href='x'>link"
        text = Indexer.extract_text(html)
        assert "unclosed" in text
        assert "link" in text

    def test_empty_html(self):
        assert Indexer.extract_text("") == ""

    def test_title_extraction(self):
        assert Indexer.extract_title("<html><head><title>Hi</title></head></html>") == "Hi"

    def test_title_missing(self):
        assert Indexer.extract_title("<p>no head</p>") == ""


# ---------------------------------------------------------------------
# Document indexing
# ---------------------------------------------------------------------

class TestAddDocument:
    def test_assigns_sequential_doc_ids(self):
        idx = Indexer()
        a = idx.add_document("http://x/1", "<p>cat</p>")
        b = idx.add_document("http://x/2", "<p>dog</p>")
        assert (a, b) == (1, 2)

    def test_indexes_unique_terms(self):
        idx = Indexer(stopwords=frozenset())
        idx.add_document("http://x/1", "<p>cat dog fish</p>")
        assert set(idx.terms) == {"cat", "dog", "fish"}

    def test_records_positions(self):
        idx = Indexer(stopwords=frozenset())
        idx.add_document("http://x/1", "<p>cat dog cat</p>")
        cat_postings = idx.terms["cat"]["postings"][1]
        assert cat_postings["tf"] == 2
        assert cat_postings["positions"] == [0, 2]

    def test_df_counts_documents_not_occurrences(self):
        idx = Indexer(stopwords=frozenset())
        idx.add_document("http://x/1", "<p>cat cat cat</p>")
        idx.add_document("http://x/2", "<p>cat</p>")
        idx.add_document("http://x/3", "<p>dog</p>")
        assert idx.terms["cat"]["df"] == 2
        assert idx.terms["dog"]["df"] == 1

    def test_document_length_in_tokens_not_chars(self):
        idx = Indexer(stopwords=frozenset())
        idx.add_document("http://x/1", "<p>cat dog fish</p>")
        assert idx.documents[1].length == 3

    def test_document_length_excludes_stopwords(self):
        idx = Indexer(stopwords=frozenset({"the"}))
        idx.add_document("http://x/1", "<p>the cat the dog</p>")
        assert idx.documents[1].length == 2

    def test_re_adding_same_url_replaces_postings(self):
        idx = Indexer(stopwords=frozenset())
        idx.add_document("http://x/1", "<p>cat</p>")
        idx.add_document("http://x/1", "<p>dog</p>")
        # 'cat' should be gone, only 'dog' present.
        assert "cat" not in idx.terms
        assert "dog" in idx.terms
        assert len(idx.documents) == 1

    def test_add_documents_bulk(self):
        idx = Indexer(stopwords=frozenset())
        idx.add_documents({
            "http://x/1": "<p>cat</p>",
            "http://x/2": "<p>dog</p>",
        })
        assert idx.num_docs() == 2


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

class TestSaveLoad:
    def test_roundtrip(self, tmp_path):
        idx = Indexer(stopwords=frozenset({"the"}))
        idx.add_document("http://x/1", "<p>The cat sat</p>")
        idx.add_document("http://x/2", "<p>dog and cat</p>")

        path = tmp_path / "index.json"
        idx.save(path)
        loaded = Indexer.load(path)

        assert loaded.num_docs() == 2
        # Original and loaded indices must agree on every posting list.
        for term, entry in idx.terms.items():
            assert loaded.terms[term]["df"] == entry["df"]
            assert loaded.terms[term]["postings"] == entry["postings"]
        assert loaded.documents[1].url == "http://x/1"
        assert loaded.stopwords == idx.stopwords

    def test_saved_file_is_valid_json(self, tmp_path):
        idx = Indexer()
        idx.add_document("http://x/1", "<p>hello world</p>")
        path = tmp_path / "index.json"
        idx.save(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == INDEX_SCHEMA_VERSION
        assert "documents" in data
        assert "terms" in data

    def test_load_rejects_unknown_version(self, tmp_path):
        path = tmp_path / "weird.json"
        path.write_text(json.dumps({"version": 99}), encoding="utf-8")
        with pytest.raises(ValueError):
            Indexer.load(path)

    def test_unicode_in_url_or_title_roundtrips(self, tmp_path):
        idx = Indexer()
        idx.add_document(
            "http://x/1",
            "<html><head><title>café</title></head><body>hello</body></html>",
        )
        path = tmp_path / "index.json"
        idx.save(path)
        loaded = Indexer.load(path)
        assert loaded.documents[1].title == "café"

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            Indexer.load(tmp_path / "nope.json")

    def test_load_resumes_next_doc_id(self, tmp_path):
        idx = Indexer()
        idx.add_document("http://x/1", "<p>x</p>")
        idx.add_document("http://x/2", "<p>y</p>")
        path = tmp_path / "i.json"
        idx.save(path)
        loaded = Indexer.load(path)
        new_id = loaded.add_document("http://x/3", "<p>z</p>")
        assert new_id == 3


# ---------------------------------------------------------------------
# Query-side helpers
# ---------------------------------------------------------------------

class TestQueryHelpers:
    def test_term_postings_returns_doc_ids(self):
        idx = Indexer(stopwords=frozenset())
        idx.add_document("http://x/1", "<p>cat</p>")
        idx.add_document("http://x/2", "<p>cat dog</p>")
        postings = idx.term_postings("cat")
        assert set(postings) == {1, 2}

    def test_term_postings_missing_returns_empty(self):
        assert Indexer().term_postings("nonsense") == {}

    def test_term_postings_case_insensitive(self):
        idx = Indexer(stopwords=frozenset())
        idx.add_document("http://x/1", "<p>Cat</p>")
        assert set(idx.term_postings("CAT")) == {1}

    def test_idf_unseen_term_positive(self):
        idx = Indexer()
        idx.add_document("http://x/1", "<p>hello</p>")
        # Unknown term still gets a positive IDF — sklearn-style smoothing.
        assert idx.idf("nonsense") > 0

    def test_idf_rare_higher_than_common(self):
        idx = Indexer(stopwords=frozenset())
        # 'hello' appears in every doc (common), 'rare' only once.
        for i in range(5):
            idx.add_document(f"http://x/{i}", "<p>hello</p>")
        idx.add_document("http://x/r", "<p>hello rare</p>")
        assert idx.idf("rare") > idx.idf("hello")


# ---------------------------------------------------------------------
# Stopwords default
# ---------------------------------------------------------------------

class TestStopwords:
    def test_default_contains_classics(self):
        for w in ("the", "and", "of", "is", "a"):
            assert w in DEFAULT_STOPWORDS

    def test_default_does_not_contain_query_examples(self):
        # The brief's own queries: 'good', 'friends', 'indifference'.
        for w in ("good", "friends", "indifference", "nonsense"):
            assert w not in DEFAULT_STOPWORDS
