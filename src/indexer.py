"""HTML → tokens → inverted index.

The :class:`Indexer` owns the in-memory inverted index and is the only
component that knows how to serialise it to / from disk.

Index layout
------------
Stored as a single JSON document::

    {
        "version": 1,
        "stopwords": [...],
        "documents": {
            "1": {"url": "...", "title": "...", "length": 42}
        },
        "terms": {
            "good": {
                "df": 17,
                "postings": {
                    "1": {"tf": 3, "positions": [4, 87, 122]},
                    ...
                }
            }
        }
    }

Doc IDs are short integers (assigned monotonically as documents are
added) which keeps the JSON compact compared to repeating long URLs in
every posting.

Tokenisation rules (mirrored at query time so the matches are sound):

* lower-case
* split on any non-alphanumeric character (matches ``[a-z0-9]+``)
* drop stop-words from the curated list in
  :data:`DEFAULT_STOPWORDS`

Whole tokens are stored — that is the mechanism by which ``find friends``
will never hit ``friendship``: the tokens compared are exact strings.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Comment

# A modest, classical English stop-word list.  Sourced from the long-
# established "Snowball" / "MySQL" inventories with a few additions for
# this corpus (we deliberately keep "good", "friends" et al. because the
# brief uses them as query examples).
DEFAULT_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "about", "above", "after", "again", "against", "all", "am",
        "an", "and", "any", "are", "as", "at",
        "be", "because", "been", "before", "being", "below", "between",
        "both", "but", "by",
        "can", "cannot", "could",
        "did", "do", "does", "doing", "down", "during",
        "each",
        "few", "for", "from", "further",
        "had", "has", "have", "having", "he", "her", "here", "hers",
        "herself", "him", "himself", "his", "how",
        "i", "if", "in", "into", "is", "it", "its", "itself",
        "just",
        "me", "more", "most", "my", "myself",
        "no", "nor", "not", "now",
        "of", "off", "on", "once", "only", "or", "other", "our", "ours",
        "ourselves", "out", "over", "own",
        "s", "same", "she", "should", "so", "some", "such",
        "t", "than", "that", "the", "their", "theirs", "them",
        "themselves", "then", "there", "these", "they", "this", "those",
        "through", "to", "too",
        "under", "until", "up",
        "very",
        "was", "we", "were", "what", "when", "where", "which", "while",
        "who", "whom", "why", "will", "with", "would",
        "you", "your", "yours", "yourself", "yourselves",
    }
)

# Compile once at import.  Anchors are unnecessary because ``findall``
# returns every non-overlapping run.
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# CSS selectors for boilerplate to strip from each page before tokenising.
# Matches the chrome on quotes.toscrape.com but is generic enough to be
# useful on any other site that uses similar conventions.
_BOILERPLATE_TAGS = ("script", "style", "nav", "header", "footer", "aside")
_BOILERPLATE_SELECTORS = (
    ".header-box",
    ".sidebar",
    ".footer",
    ".tags-box",
    "#navbar",
    "#footer",
)

INDEX_SCHEMA_VERSION = 1


@dataclass
class DocumentRecord:
    """Lightweight per-document metadata kept inside the index."""

    doc_id: int
    url: str
    title: str
    length: int

    def to_json(self) -> dict:
        return {"url": self.url, "title": self.title, "length": self.length}


@dataclass
class Indexer:
    """Builds and queries an inverted index.

    The class is intentionally agnostic of how documents arrive — feed
    it ``(url, html)`` pairs via :meth:`add_document` (or
    :meth:`add_documents` for a dict).  Persist with :meth:`save`,
    reload with the :meth:`load` classmethod.
    """

    stopwords: frozenset[str] = field(default_factory=lambda: DEFAULT_STOPWORDS)
    documents: dict[int, DocumentRecord] = field(default_factory=dict)
    terms: dict[str, dict] = field(default_factory=dict)
    _next_doc_id: int = field(default=1, init=False, repr=False)
    _url_to_doc_id: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    # -------------------- public ingestion API --------------------

    def add_document(self, url: str, html: str) -> int:
        """Tokenise *html* and merge into the index.

        Returns the integer doc id assigned to the page.  If the same
        ``url`` is added twice the second call overwrites the first.
        """
        if url in self._url_to_doc_id:
            # Re-indexing the same URL: drop the stale postings first.
            self._purge_doc(self._url_to_doc_id[url])
        doc_id = self._next_doc_id
        self._next_doc_id += 1
        self._url_to_doc_id[url] = doc_id

        text = self.extract_text(html)
        title = self.extract_title(html)
        tokens = self.tokenise(text)

        self.documents[doc_id] = DocumentRecord(
            doc_id=doc_id, url=url, title=title, length=len(tokens)
        )

        for position, term in enumerate(tokens):
            entry = self.terms.get(term)
            if entry is None:
                entry = {"df": 0, "postings": {}}
                self.terms[term] = entry
            posting = entry["postings"].get(doc_id)
            if posting is None:
                posting = {"tf": 0, "positions": []}
                entry["postings"][doc_id] = posting
                entry["df"] += 1
            posting["tf"] += 1
            posting["positions"].append(position)

        return doc_id

    def add_documents(self, pages: dict[str, str]) -> None:
        """Convenience: index every ``(url, html)`` pair in *pages*."""
        for url, html in pages.items():
            self.add_document(url, html)

    # -------------------- helpers --------------------------------

    @classmethod
    def extract_text(cls, html: str) -> str:
        """Return visible body text with site chrome stripped.

        * Confines extraction to ``<body>`` so ``<title>`` (which on
          quotes.toscrape.com is the same boilerplate string on every
          page) cannot leak into the term stream.
        * Removes ``<script>``, ``<style>``, ``<nav>``, ``<header>``,
          ``<footer>``, ``<aside>`` and a handful of class/id selectors
          covering common navigation/sidebar/footer chrome.
        * Strips HTML comments — they sometimes hide indexable strings.
        """
        soup = BeautifulSoup(html, "html.parser")
        # Confine to body (falls back to whole doc when body is absent).
        root = soup.body or soup
        for tag in root(_BOILERPLATE_TAGS):
            tag.decompose()
        for selector in _BOILERPLATE_SELECTORS:
            for el in root.select(selector):
                el.decompose()
        for comment in root.find_all(string=lambda s: isinstance(s, Comment)):
            comment.extract()
        return root.get_text(" ", strip=True)

    @staticmethod
    def extract_title(html: str) -> str:
        """``<title>`` text, or empty string when absent."""
        soup = BeautifulSoup(html, "html.parser")
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        return ""

    def tokenise(self, text: str) -> list[str]:
        """Lower-case, split on non-alphanumeric, drop stop-words."""
        lowered = text.lower()
        tokens = _TOKEN_RE.findall(lowered)
        if not self.stopwords:
            return tokens
        return [t for t in tokens if t not in self.stopwords]

    # -------------------- query-side helpers ---------------------

    def num_docs(self) -> int:
        return len(self.documents)

    def term_postings(self, term: str) -> dict[int, dict]:
        """Return the ``{doc_id: posting}`` map for *term* (possibly empty)."""
        term = term.lower()
        entry = self.terms.get(term)
        return dict(entry["postings"]) if entry else {}

    def document(self, doc_id: int) -> DocumentRecord | None:
        return self.documents.get(doc_id)

    def idf(self, term: str) -> float:
        """Inverse document frequency for *term*.

        Uses the smoothed form ``log((N + 1) / (df + 1)) + 1`` so single
        unique terms still receive a positive weight (a property the
        scikit-learn implementation also relies on).
        """
        entry = self.terms.get(term.lower())
        df = entry["df"] if entry else 0
        n = max(self.num_docs(), 1)
        return math.log((n + 1) / (df + 1)) + 1.0

    # -------------------- persistence ---------------------------

    def save(self, path: str | Path) -> None:
        """Serialise the index to *path* as UTF-8 JSON."""
        payload = {
            "version": INDEX_SCHEMA_VERSION,
            "stopwords": sorted(self.stopwords),
            "documents": {
                str(doc_id): rec.to_json()
                for doc_id, rec in self.documents.items()
            },
            "terms": {
                term: {
                    "df": entry["df"],
                    "postings": {
                        str(doc_id): posting
                        for doc_id, posting in entry["postings"].items()
                    },
                }
                for term, entry in self.terms.items()
            },
        }
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> Indexer:
        """Inverse of :meth:`save` — produce a ready-to-query Indexer."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("version") != INDEX_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported index version {raw.get('version')!r}; "
                f"expected {INDEX_SCHEMA_VERSION}"
            )
        idx = cls(stopwords=frozenset(raw.get("stopwords", DEFAULT_STOPWORDS)))
        idx.documents = {
            int(doc_id): DocumentRecord(
                doc_id=int(doc_id),
                url=info["url"],
                title=info["title"],
                length=info["length"],
            )
            for doc_id, info in raw.get("documents", {}).items()
        }
        idx.terms = {
            term: {
                "df": entry["df"],
                "postings": {
                    int(doc_id): posting
                    for doc_id, posting in entry["postings"].items()
                },
            }
            for term, entry in raw.get("terms", {}).items()
        }
        if idx.documents:
            idx._next_doc_id = max(idx.documents) + 1
        idx._url_to_doc_id = {rec.url: did for did, rec in idx.documents.items()}
        return idx

    # -------------------- internals -----------------------------

    def _purge_doc(self, doc_id: int) -> None:
        """Remove every trace of *doc_id* from the index (for re-indexing)."""
        rec = self.documents.pop(doc_id, None)
        if rec is None:
            return
        self._url_to_doc_id.pop(rec.url, None)
        empties = []
        for term, entry in self.terms.items():
            if doc_id in entry["postings"]:
                del entry["postings"][doc_id]
                entry["df"] -= 1
                if entry["df"] == 0:
                    empties.append(term)
        for term in empties:
            del self.terms[term]


__all__ = [
    "Indexer",
    "DocumentRecord",
    "DEFAULT_STOPWORDS",
    "INDEX_SCHEMA_VERSION",
]
