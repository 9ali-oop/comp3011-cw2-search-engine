"""Unit tests for the search engine."""

from __future__ import annotations

import pytest

from src.indexer import Indexer
from src.search import SearchEngine, SearchHit, TermPosting, _edit_distance

# A tiny corpus used by most tests.  Tokens (after stop-word stripping):
#   doc1: cat sat mat
#   doc2: dog ran far
#   doc3: cat ran cat ran
#   doc4: friendship runs deep        (note: 'friendship' not 'friends')
#   doc5: good friends matter most    ('most' is a stopword)
CORPUS = {
    "http://x/1": "<body>The cat sat on the mat.</body>",
    "http://x/2": "<body>A dog ran far.</body>",
    "http://x/3": "<body>cat ran cat ran</body>",
    "http://x/4": "<body>Friendship runs deep.</body>",
    "http://x/5": "<body>Good friends matter most.</body>",
}


@pytest.fixture()
def engine() -> SearchEngine:
    idx = Indexer()
    idx.add_documents(CORPUS)
    return SearchEngine(idx)


# find - single-word

class TestFindSingleWord:
    def test_returns_matching_docs(self, engine):
        urls = [h.url for h in engine.find("cat")]
        assert set(urls) == {"http://x/1", "http://x/3"}

    def test_returns_searchhit_dataclass(self, engine):
        hits = engine.find("cat")
        assert all(isinstance(h, SearchHit) for h in hits)
        assert all(h.score > 0 for h in hits)

    def test_case_insensitive_query(self, engine):
        assert {h.url for h in engine.find("CAT")} == {
            "http://x/1", "http://x/3",
        }
        assert {h.url for h in engine.find("Cat")} == {
            "http://x/1", "http://x/3",
        }

    def test_full_word_match_not_substring(self, engine):
        # 'friends' must not match 'friendship'.
        urls = {h.url for h in engine.find("friends")}
        assert urls == {"http://x/5"}

    def test_full_word_match_not_substring_other_direction(self, engine):
        urls = {h.url for h in engine.find("friendship")}
        assert urls == {"http://x/4"}

    def test_non_existent_term_returns_empty(self, engine):
        assert engine.find("xyzzy") == []

    def test_empty_query_returns_empty(self, engine):
        assert engine.find("") == []

    def test_whitespace_only_query_returns_empty(self, engine):
        assert engine.find("   \t\n  ") == []

    def test_stopword_only_query_returns_empty(self, engine):
        # 'the' is in the default stop list.
        assert engine.find("the") == []
        assert engine.find("the and of") == []

    def test_punctuation_in_query_is_stripped(self, engine):
        # "cat!" should tokenise to ["cat"].
        assert {h.url for h in engine.find("cat!")} == {
            "http://x/1", "http://x/3",
        }


# find - multi-word (AND semantics)

class TestFindMultiWord:
    def test_and_intersection(self, engine):
        # 'good' is in doc5 only; 'friends' is in doc5 only.
        hits = engine.find("good friends")
        assert {h.url for h in hits} == {"http://x/5"}

    def test_and_with_one_missing_term_returns_empty(self, engine):
        # 'cat' is in docs 1 & 3; 'dog' is in doc 2. No intersection.
        assert engine.find("cat dog") == []

    def test_and_three_words(self):
        idx = Indexer()
        idx.add_document("http://x/1", "<body>red green blue</body>")
        idx.add_document("http://x/2", "<body>red green</body>")
        eng = SearchEngine(idx)
        # All three must appear.
        assert [h.url for h in eng.find("red green blue")] == ["http://x/1"]

    def test_query_with_mix_of_stopword_and_word(self, engine):
        # 'the cat' becomes ['cat'] (stopword removed); behaves like 'cat'.
        urls = {h.url for h in engine.find("the cat")}
        assert urls == {"http://x/1", "http://x/3"}

    def test_duplicate_query_terms_handled(self, engine):
        # Asking the same term twice should not change the result set,
        # though it may bump the score (we don't assert on the value).
        hits_single = engine.find("cat")
        hits_double = engine.find("cat cat")
        assert {h.url for h in hits_single} == {h.url for h in hits_double}


# Ranking

class TestRanking:
    def test_higher_tf_outranks_lower_tf(self):
        # doc3 has cat twice, doc1 has cat once -> doc3 should rank higher.
        idx = Indexer()
        idx.add_document("http://x/1", "<body>cat sat mat</body>")
        idx.add_document("http://x/3", "<body>cat cat ran</body>")
        eng = SearchEngine(idx)
        hits = eng.find("cat")
        assert hits[0].url == "http://x/3"
        assert hits[0].score > hits[1].score

    def test_rare_term_outranks_common_when_intersected(self):
        # Add many docs with 'cat'; one doc with 'cat' AND a rare term.
        idx = Indexer()
        for i in range(10):
            idx.add_document(f"http://x/c{i}", "<body>cat</body>")
        idx.add_document("http://x/rare", "<body>cat unicorn</body>")
        eng = SearchEngine(idx)
        # Single-term 'unicorn' must surface the right doc.
        hits = eng.find("unicorn")
        assert [h.url for h in hits] == ["http://x/rare"]

    def test_limit_truncates_results(self):
        idx = Indexer()
        for i in range(5):
            idx.add_document(f"http://x/{i}", "<body>cat</body>")
        eng = SearchEngine(idx)
        hits = eng.find("cat", limit=2)
        assert len(hits) == 2

    def test_deterministic_tiebreak_on_equal_scores(self):
        # Two docs with the same exact tokens -> identical scores;
        # tiebreak by URL keeps output reproducible.
        idx = Indexer()
        idx.add_document("http://x/z", "<body>cat</body>")
        idx.add_document("http://x/a", "<body>cat</body>")
        eng = SearchEngine(idx)
        urls = [h.url for h in eng.find("cat")]
        assert urls == ["http://x/a", "http://x/z"]


# print

class TestPrintTerm:
    def test_returns_posting_for_each_doc(self, engine):
        postings = engine.print_term("cat")
        assert {p.url for p in postings} == {"http://x/1", "http://x/3"}

    def test_includes_tf_and_positions(self, engine):
        postings = engine.print_term("cat")
        cat3 = next(p for p in postings if p.url == "http://x/3")
        # doc3: 'cat ran cat ran' -> positions 0 and 2 after stopword removal.
        assert cat3.tf == 2
        assert cat3.positions == (0, 2)

    def test_case_insensitive(self, engine):
        a = engine.print_term("CAT")
        b = engine.print_term("cat")
        assert {(p.url, p.tf) for p in a} == {(p.url, p.tf) for p in b}

    def test_missing_term_returns_empty(self, engine):
        assert engine.print_term("xyzzy") == []

    def test_stopword_only_returns_empty(self, engine):
        assert engine.print_term("the") == []

    def test_empty_string_returns_empty(self, engine):
        assert engine.print_term("") == []

    def test_results_sorted_by_tf_desc(self, engine):
        # doc3 has cat twice, doc1 once -> doc3 first.
        postings = engine.print_term("cat")
        assert postings[0].tf >= postings[1].tf

    def test_print_term_returns_termposting_dataclass(self, engine):
        ps = engine.print_term("cat")
        assert all(isinstance(p, TermPosting) for p in ps)


# Engine wiring

class TestEngineWiring:
    def test_engine_does_not_mutate_index(self, engine):
        before_terms = len(engine.index.terms)
        before_docs = engine.index.num_docs()
        engine.find("cat dog")
        engine.print_term("cat")
        engine.find("nonsense")
        assert len(engine.index.terms) == before_terms
        assert engine.index.num_docs() == before_docs

    def test_works_on_empty_index(self):
        eng = SearchEngine(Indexer())
        assert eng.find("anything") == []
        assert eng.print_term("anything") == []


# Phrase queries

class TestPhraseQueries:
    def _engine(self):
        idx = Indexer()
        idx.add_document("http://x/1", "<body>good friends matter</body>")
        idx.add_document("http://x/2", "<body>friends are good</body>")
        idx.add_document("http://x/3", "<body>good</body>")
        idx.add_document("http://x/4", "<body>just friends</body>")
        return SearchEngine(idx)

    def test_phrase_requires_adjacency(self):
        eng = self._engine()
        hits = {h.url for h in eng.find('"good friends"')}
        # doc1: positions good=0, friends=1 -> phrase matches
        # doc2: positions friends=0, good=1 -> reversed, NO match
        # doc3: missing friends
        # doc4: missing good
        assert hits == {"http://x/1"}

    def test_phrase_with_reversed_words_does_not_match(self):
        eng = self._engine()
        hits = {h.url for h in eng.find('"friends good"')}
        # Only doc2 has friends THEN good consecutively.
        assert hits == {"http://x/2"}

    def test_single_word_phrase_acts_like_term(self):
        eng = self._engine()
        # 'good' alone (quoted) -> same as plain 'good'.
        assert (
            {h.url for h in eng.find('"good"')}
            == {h.url for h in eng.find('good')}
        )

    def test_phrase_plus_free_term(self):
        idx = Indexer()
        idx.add_document(
            "http://x/1", "<body>good friends matter most</body>"
        )
        idx.add_document(
            "http://x/2", "<body>good friends</body>"  # missing 'matter'
        )
        idx.add_document(
            "http://x/3", "<body>friends good matter</body>"  # not adjacent
        )
        eng = SearchEngine(idx)
        # Phrase 'good friends' + free term 'matter'.
        hits = {h.url for h in eng.find('"good friends" matter')}
        assert hits == {"http://x/1"}

    def test_phrase_with_unknown_term_no_match(self):
        eng = self._engine()
        assert eng.find('"good xyzzy"') == []

    def test_three_word_phrase(self):
        idx = Indexer()
        idx.add_document(
            "http://x/1", "<body>good friends matter most</body>"
        )
        idx.add_document(
            "http://x/2", "<body>good kind friends matter</body>"
        )
        eng = SearchEngine(idx)
        # 'good friends matter' as a phrase: must be three consecutive.
        hits = {h.url for h in eng.find('"good friends matter"')}
        assert hits == {"http://x/1"}


# Did-you-mean (edit distance)

class TestDidYouMean:
    def test_suggests_close_term(self):
        idx = Indexer()
        idx.add_document("http://x/1", "<body>einstein wisdom</body>")
        eng = SearchEngine(idx)
        # 'einstien' is one transposition away from 'einstein'.
        suggestions = eng.did_you_mean("einstien")
        assert "einstein" in suggestions

    def test_no_suggestions_for_obviously_different_term(self):
        idx = Indexer()
        idx.add_document("http://x/1", "<body>cat dog fish</body>")
        eng = SearchEngine(idx)
        assert eng.did_you_mean("xyzzy") == []

    def test_too_short_query_returns_empty(self):
        idx = Indexer()
        idx.add_document("http://x/1", "<body>cat</body>")
        eng = SearchEngine(idx)
        assert eng.did_you_mean("a") == []
        assert eng.did_you_mean("") == []

    def test_ranks_by_distance_then_df(self):
        idx = Indexer()
        # 'cat' appears in many docs; 'bat' in only one.  Both are 1
        # edit away from 'cot' -> 'cat' should rank first (higher df).
        for i in range(5):
            idx.add_document(f"http://x/c{i}", "<body>cat</body>")
        idx.add_document("http://x/b", "<body>bat</body>")
        eng = SearchEngine(idx)
        suggestions = eng.did_you_mean("cot")
        assert suggestions[0] == "cat"
        assert "bat" in suggestions

    def test_self_match_excluded(self):
        # If the term IS in the vocab, it should not be suggested for itself.
        idx = Indexer()
        idx.add_document("http://x/1", "<body>cat</body>")
        eng = SearchEngine(idx)
        assert "cat" not in eng.did_you_mean("cat")

    def test_limit_enforced(self):
        idx = Indexer()
        # Build a few near-neighbours of 'cat'.
        for w in ("bat", "cot", "car", "rat", "mat", "hat", "sat"):
            idx.add_document(f"http://x/{w}", f"<body>{w}</body>")
        eng = SearchEngine(idx)
        suggestions = eng.did_you_mean("cat", limit=3)
        assert len(suggestions) == 3


class TestEditDistance:
    def test_identical_strings(self):
        assert _edit_distance("cat", "cat") == 0

    def test_single_substitution(self):
        assert _edit_distance("cat", "bat") == 1

    def test_insertion(self):
        assert _edit_distance("cat", "cart") == 1

    def test_deletion(self):
        assert _edit_distance("cart", "cat") == 1

    def test_empty_strings(self):
        assert _edit_distance("", "") == 0
        assert _edit_distance("abc", "") == 3
        assert _edit_distance("", "abc") == 3

    def test_ceiling_early_exit(self):
        # 'abcdef' vs 'uvwxyz' has distance 6 - with a ceiling of 2 we
        # should return 3 (ceiling + 1), confirming the early-exit path.
        assert _edit_distance("abcdef", "uvwxyz", ceiling=2) == 3
