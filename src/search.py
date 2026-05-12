"""Query the inverted index.

:class:`SearchEngine` is a thin facade over :class:`Indexer` that
implements the two query surfaces required by the brief:

* ``print(term)`` - dump the full posting list for *term*.
* ``find(query)`` - return the list of pages containing every (non
  stop-word) term in *query*, ranked by TF-IDF.

Both surfaces share a tokeniser with the indexer so the matches are
consistent and full-word: ``"friends"`` will never match a token of
``"friendship"`` because the tokens compared are exact strings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .indexer import DocumentRecord, Indexer


@dataclass(frozen=True)
class SearchHit:
    """A single page in a ranked result list."""

    doc_id: int
    url: str
    title: str
    score: float

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        title = f" - {self.title}" if self.title else ""
        return f"{self.url}{title}  (score={self.score:.4f})"


@dataclass(frozen=True)
class TermPosting:
    """One line of ``print <term>`` output."""

    doc_id: int
    url: str
    tf: int
    positions: tuple[int, ...]


class SearchEngine:
    """Read-only view over an :class:`Indexer`.

    The indexer is held by reference, never mutated: querying must
    never alter the index.
    """

    def __init__(self, index: Indexer) -> None:
        self.index = index

    # public API

    def find(self, query: str, limit: int | None = None) -> list[SearchHit]:
        """Return pages containing **every** term in *query*.

        Multi-word semantics match the brief's ``find good friends``
        example: a page is a hit only when it contains both ``good``
        and ``friends``.  Hits are sorted by TF-IDF score, highest
        first, with the URL as a deterministic tiebreaker.

        Empty queries - and queries that contain only stop-words -
        return an empty list rather than raising.
        """
        terms = self._tokenise_query(query)
        if not terms:
            return []

        # Look up postings once per term.
        per_term_postings: list[dict[int, dict]] = []
        for term in terms:
            postings = self.index.term_postings(term)
            if not postings:
                # One missing term -> AND semantics give zero hits.
                return []
            per_term_postings.append(postings)

        # Intersect on doc id.  Start with smallest set for speed.
        per_term_postings.sort(key=len)
        candidate_ids: set[int] = set(per_term_postings[0])
        for postings in per_term_postings[1:]:
            candidate_ids &= postings.keys()
            if not candidate_ids:
                return []

        # Score the intersection by sum of TF-IDF over the query terms.
        scored: list[SearchHit] = []
        for doc_id in candidate_ids:
            score = 0.0
            for term, postings in zip(terms, per_term_postings):
                tf = postings[doc_id]["tf"]
                # Sub-linear TF (1 + log tf) - standard text-IR trick to
                # damp the impact of very high-frequency repetitions.
                tf_w = 1.0 + math.log(tf) if tf > 0 else 0.0
                score += tf_w * self.index.idf(term)
            doc = self.index.document(doc_id)
            if doc is None:  # pragma: no cover - inconsistent index
                continue
            scored.append(
                SearchHit(
                    doc_id=doc_id,
                    url=doc.url,
                    title=doc.title,
                    score=score,
                )
            )

        scored.sort(key=lambda h: (-h.score, h.url))
        if limit is not None:
            scored = scored[:limit]
        return scored

    def print_term(self, term: str) -> list[TermPosting]:
        """Return the posting list for *term* (one entry per matching doc).

        ``print_term`` is named with an underscore so it doesn't clash
        with the ``print`` builtin in CLI code.  The CLI maps the user's
        literal ``print <word>`` command onto this method.

        For multi-word input (``print good friends``) the call is run
        per token and the results concatenated; that matches the spirit
        of the brief which only specifies a single-word ``print``.
        """
        results: list[TermPosting] = []
        for token in self._tokenise_query(term):
            for doc_id, posting in self.index.term_postings(token).items():
                doc = self.index.document(doc_id)
                if doc is None:  # pragma: no cover
                    continue
                results.append(
                    TermPosting(
                        doc_id=doc_id,
                        url=doc.url,
                        tf=posting["tf"],
                        positions=tuple(posting["positions"]),
                    )
                )
        # Stable secondary order: most-frequent first, then URL.
        results.sort(key=lambda p: (-p.tf, p.url))
        return results

    # internals

    def _tokenise_query(self, query: str) -> list[str]:
        """Apply the same tokenisation rules as the indexer.

        This is the single mechanism that makes the engine
        case-insensitive *and* full-word: both the index and the query
        are reduced to identical surface forms before comparison.  No
        prefix/suffix matching ever happens, so ``friends`` cannot hit
        ``friendship``.
        """
        return self.index.tokenise(query)


__all__ = ["SearchEngine", "SearchHit", "TermPosting"]
