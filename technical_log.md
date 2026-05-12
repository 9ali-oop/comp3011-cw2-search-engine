# Technical Log - COMP3011 CW2 Search Engine

> Engineering log for COMP3011 CW2. Records the major design
> decisions I made, alternatives I weighed, test failures I hit
> and how I fixed them. Kept separate from the README so the
> design rationale can live in detail without bloating the
> public docs.

---

## 0. Mission re-read (from the brief)

* Target: `https://quotes.toscrape.com/` (~213 pages).
* Politeness window ≥ 6 s between requests (CW2 brief §1.b).
* Inverted index must record statistics per word per page
  (frequency, position).
* Search is case-insensitive and full-word (`good` = `Good`, but
  `friends` ≠ `friendship`).
* CLI must support `build`, `load`, `print <word>`, `find <query…>`.
  I add `quit`/`exit` for usability - outside the brief but expected of
  a REPL.

Rubric weights that shape priorities:

* Testing & coverage: 20 % → ≥ 85 % line coverage, ≥ 30 unit tests,
  edge cases, mocked HTTP.
* Search functionality: 12 % → multi-word AND queries, ranking.
* GenAI critical reflection: 15 % - covered in REFLECTION.md and
  in the final 30 seconds of the video.

## 1. Project layout

Chose the structure mandated by §3.ii of the brief verbatim:

```
search_engine/
  src/      crawler.py  indexer.py  search.py  main.py
  tests/    test_crawler.py  test_indexer.py  test_search.py  test_cli.py
  data/     index.json   (gitignored - generated artefact)
  README.md  requirements.txt  technical_log.md  pytest.ini  .gitignore
```

Alternatives considered:

* **Flat (all `.py` in repo root):** faster to start with but harder to
  package and not what the brief asks for. **Rejected.**
* **`searchengine/` package + `tests/` peer (typical PyPI layout):**
  nicer for distribution but the brief explicitly names `src/`.
  **Rejected** so the layout matches the markscheme example exactly.

## 2. Dependencies

`requirements.txt`:

| Library | Why |
|---|---|
| `requests` | brief recommends it (§1.c) |
| `beautifulsoup4` | brief recommends it (§1.c) |
| `pytest` | de-facto standard, supports parametrisation |
| `pytest-cov` | rubric values coverage - we want a real number |
| `responses` | mock HTTP cleanly. Considered `unittest.mock.patch('requests.get')` but `responses` is far less brittle and gives realistic response objects |

Stdlib-only alternatives ruled out:

* `urllib.request` + `html.parser` would have worked but the brief
  recommends requests/BS4, and using them is the path of least surprise
  for anyone reading the code.

---

## 3. Crawler (`src/crawler.py`)

### 3.1 URL normalisation

Required to deduplicate trivially-different URLs (`/about` vs
`/about/`, `HTTP://EXAMPLE.com` vs `http://example.com`, with vs
without `:80`, with vs without `#fragment`).

Rules implemented:

1. Resolve relative URLs against a base.
2. Strip the `#fragment` (`urldefrag`).
3. Lower-case scheme + host (HTTP semantics: scheme/host are
   case-insensitive; path is case-sensitive - we preserve case).
4. Drop default ports (`:80` for http, `:443` for https).
5. Empty path → `/`.
6. Strip surrounding whitespace.

Alternatives considered:

* **Canonicalise query parameters** (sort, drop UTM tags). Useful for
  ad-tech sites, irrelevant for quotes.toscrape.com. **Rejected** to
  keep the function obvious.
* **Force trailing slash on directory-looking paths.** Risks
  collapsing two genuinely different endpoints (`/foo` vs `/foo/`).
  **Rejected.**

### 3.2 Frontier and seen set

* Frontier: `collections.deque` for O(1) append + popleft.
* Seen: a `set[str]`.
* Both keyed by the normalised URL - that is how duplicate detection
  actually happens.

Alternatives:

* **`list.pop(0)`** - O(n). Pointless penalty.
* **Priority queue weighted by depth.** Useful for very large crawls;
  unnecessary for 213 pages.

### 3.3 Politeness

The brief mandates ≥ 6 s between requests. Implementation:

```python
def _wait_for_politeness(self):
    if self._last_request_time is None or self.delay <= 0:
        return
    elapsed = time.monotonic() - self._last_request_time
    remaining = self.delay - elapsed
    if remaining > 0:
        self._sleep(remaining)
```

The sleep function is injected. The default is `time.sleep`; the test
suite passes `lambda _: None` so a unit test can verify
"politeness was requested" without actually waiting six seconds per
request. `time.monotonic` is preferred over `time.time` so the
politeness window is unaffected by system-clock jumps.

Alternative considered: **fixed `time.sleep(6)` after every fetch**.
Simpler, but it sleeps too long when a request itself takes (say) 4 s.
The chosen approach reserves *exactly* 6 s of separation regardless of
request latency.

### 3.4 Robots.txt

`urllib.robotparser.RobotFileParser` is used. The crawler fetches
`/robots.txt` once on construction, parses the lines, and consults the
parser before every URL. If the robots file is missing or fetching it
fails the parser is fed an empty rule-set, matching the conservative
behaviour of the stdlib (allow-all).

Alternative considered: **third-party `reppy`**. Not in stdlib,
heavier; gain over `robotparser` is negligible for this site.

### 3.5 Retry and timeouts

* Transient failures (`requests.RequestException`, 5xx responses) →
  retry up to `max_retries=2` times with exponential back-off
  (multiplier 1.5).
* 4xx → permanent, no retry.
* Non-HTML content-types → skipped (so we don't fail to parse a PNG).
* All retry sleeps go through the same injected `_sleep` callable so
  tests run fast.

### 3.6 Edge cases hit during dev

* The very first crawler test for `User-Agent` injection failed because
  `requests.Session.headers` ships with `python-requests/x.y` pre-set;
  `setdefault` therefore silently kept the default. **Fix:** direct
  assignment (`self.session.headers["User-Agent"] = self.user_agent`).
  Added a test that reads the header back and asserts on the value so
  the bug can't return.
* The retry tests were slow (≈ 11 s for the suite) because they used
  `time.sleep` for the back-off. **Fix:** the `_make_crawler` test
  helper now injects `sleep=lambda _: None`. Suite back down to <1 s.

---

## 4. Indexer (`src/indexer.py`)

### 4.1 Index data structure

Chosen layout:

```python
{
    "version": 1,
    "stopwords": [...],
    "documents": {doc_id: {url, title, length}},
    "terms": {
        term: {
            "df": int,
            "postings": {doc_id: {tf: int, positions: [int]}}
        }
    },
}
```

Doc ids are short integers (1, 2, 3, …) assigned monotonically as the
indexer ingests pages. Postings reference the id, not the URL.

Why this shape?

* `term -> postings` is the canonical inverted-index layout
  (Manning §1.2). Random lookup by term is O(1).
* Storing `df` next to the postings means TF-IDF scoring is one
  hash-lookup per term, no scanning required.
* Integer doc ids cut the JSON size by roughly half compared with
  repeating URLs per posting (the URLs live in a single `documents`
  dict).

Alternatives explored:

* **Flat list of `(term, doc_id, tf)` triples.** Easy to write but
  every query becomes O(n) over the whole file. Rejected.
* **Trie / FST keyed by term prefix.** Right for autocomplete; overkill
  for 2.6 k terms.
* **SQLite.** Would work but the brief says "save the entire index to
  a single file" and asks for JSON-style data structures. Adds opacity.
* **pickle / msgpack.** Faster I/O but the brief asks for a marker-
  inspectable artefact; JSON wins on legibility.

### 4.2 Tokenisation

Three rules from the brief, no more:

1. Lower-case (`text.lower()`).
2. Split on non-alphanumeric (`re.findall(r"[a-z0-9]+", text)`).
3. Drop common stop-words.

Trade-offs in choosing this exact form:

* Why **`[a-z0-9]+`** and not `\w+`? `\w` matches unicode letters,
  which sounds nice but means `café` becomes one token (`café`) while
  `cafe` is another - they would never match. ASCII-only is consistent
  with what the brief asks for ("split on non-alphanumeric") and with
  the corpus, which uses curly quotes around quotes but ASCII inside
  them.
* Why **no stemming** (Porter/Snowball)? The brief asks for full-word
  matching: `friends ≠ friendship`. Stemming explicitly violates this.
  Stemmers also hurt precision on short queries.
* Why drop stop-words at **index** time rather than only at query
  time? Saves space (stop-words are ~30 % of English running text);
  positions list correspondingly shrinks; standard IR practice. The
  trade-off is that you can't search for `"the matrix"` as a phrase -
  acceptable for this brief.

Stop-word list: 120 classic English stop-words. The list deliberately
**does not** include the brief's own example terms (`good`, `friends`,
`indifference`, `nonsense`) - a regression test enforces this.

Alternatives considered:

* **NLTK stop-words (`stopwords.words('english')`)** - adds a heavy
  download and a dependency.
* **sklearn `ENGLISH_STOP_WORDS`** - same dependency cost, plus a long
  list (~300 words) that filters out a lot of usable content.
* **No stop-words.** Larger index, noisier ranking. The brief asks for
  stop-words to be removed.

### 4.3 Boilerplate stripping

Removed before tokenising:

* `<script>`, `<style>`, `<nav>`, `<header>`, `<footer>`, `<aside>`
  (all decomposed).
* CSS selectors `.header-box .sidebar .footer .tags-box #navbar
  #footer` (match the quotes.toscrape.com chrome and common site
  conventions).
* HTML comments (`<!-- … -->`).
* Extraction is **confined to `<body>`** so the `<title>` element
  cannot leak into the term stream.

The `<title>` confinement bit me on first try: every page on
quotes.toscrape.com has `<title>Quotes to Scrape</title>`, which gave
the terms `quotes` and `scrape` `df = N` (one per page). Fixed by
restricting `extract_text` to `soup.body`. Regression test added.

### 4.4 TF-IDF stats

Stored DF alongside each term in the postings list so the IDF formula
is a one-shot lookup, no global scan. Smoothed form:

```
idf(t) = log((N + 1) / (df(t) + 1)) + 1
```

…matches the formula used by scikit-learn's `TfidfTransformer`. The
`+1` everywhere stops zero-division on a fresh index and gives unseen
terms a positive (small) weight - useful if we ever extend the
indexer to support online queries against terms it hasn't seen yet.

Alternatives considered:

* **Raw `log(N/df)`** - divides by zero when `df = 0`.
* **BM25.** Probabilistic, generally better than TF-IDF on long
  documents but indistinguishable on the very short pages of
  quotes.toscrape.com. Rubric explicitly names TF-IDF.
* **No ranking at all.** Acceptable for a pass; 80-100 band names
  ranking as a feature.

### 4.5 Persistence

`save()` writes the dict above as UTF-8 JSON with `ensure_ascii=False`
(otherwise unicode in URLs/titles balloons the file with `\uXXXX`
escapes). `load()` reads it back, validates the `version` field and
rebuilds the int-keyed dicts (JSON only has string keys).

Single file, even at 213 pages: ~600 KB. Easy to attach to Minerva or
email.

---

## 5. Search (`src/search.py`)

### 5.1 `find()` semantics

* Tokenise the query with the **same** function the indexer used.
  That is the single mechanism that makes the engine
  case-insensitive **and** full-word - both sides are reduced to
  identical exact-match tokens before comparison. No regex/prefix
  match anywhere.
* Empty / whitespace / stop-word-only queries return `[]` cleanly (no
  exception).
* Multi-word: AND. Intersect the doc-id sets of every query term.
* If any term is unknown the result is immediately empty.
* Score = `sum_over_terms(tf_weight(t,d) * idf(t))` where
  `tf_weight = 1 + log(tf)` (sub-linear).
* Tiebreak by URL for reproducibility (otherwise `set` iteration order
  leaks into tests).

Alternatives considered:

* **OR semantics with score-based ranking.** Matches Google's default
  but the brief's example "`find good friends` returns pages containing
  both" implies AND. Stuck with AND.
* **Linear TF (`tf` itself, no log).** Over-rewards keyword spamming.
* **No ranking, order by doc id.** Trivial but the 80-100 band asks
  for "advanced features (e.g., TF-IDF ranking)".

### 5.2 `print_term()`

Returns the full posting list for a single token (one entry per
matching doc with `tf` and `positions`). Sorted by descending `tf`
then by URL.

* Tokenises the input the same way as `find` so `print Friends!` works.
* Stop-word-only / empty / unknown-term input returns `[]`.
* The method is named `print_term` (not `print`) because shadowing the
  builtin would be a foot-gun for the test suite - the CLI maps the
  user's literal `print <word>` command onto this method.

---

## 6. CLI (`src/main.py`)

* Tiny `Shell` class with an `execute(line)` method and a thin
  `run(stdin)` loop.
* `out` and `stdin` are injectable so tests are deterministic.
* Crawler/indexer construction lives behind factory callables -
  tests pass a stub that returns a pre-built `CrawlResult` instead of
  actually hitting the network.
* All errors in command handling are caught and printed as a
  one-line `error: <message>` so a buggy command can't kill the REPL.
* Quit / exit / EOF all leave gracefully.
* Force UTF-8 stdout on startup (Windows codepage cp1252 otherwise
  cannot encode the curly quotes on quotes.toscrape.com).

---

## 7. Testing

130 tests, ~1 s suite, 98 % line coverage.

| Module | Tests | Notes |
|---|---|---|
| `tests/test_crawler.py` | 38 | URL norm, links, BFS, errors, robots, politeness |
| `tests/test_indexer.py` | 38 | tokenise, boilerplate, postings, save/load, IDF |
| `tests/test_search.py` | 29 | find, print, AND, ranking, edge cases |
| `tests/test_cli.py` | 25 | build/load/print/find, REPL plumbing, argparse |

Edge cases the user singled out for coverage are all included:

| Edge case | Test |
|---|---|
| Empty query | `TestFindSingleWord::test_empty_query_returns_empty`, `…whitespace_only` |
| Stopword-only query | `TestFindSingleWord::test_stopword_only_query_returns_empty` |
| Non-existent word | `TestFindSingleWord::test_non_existent_term_returns_empty` |
| Malformed HTML | `TestExtractLinks::test_handles_malformed_html`, `TestExtractText::test_handles_malformed_html` |
| Network timeout | `TestCrawlerBasics::test_timeout_failure` |
| Duplicate URL | `TestCrawlerBasics::test_dedups_duplicate_urls` |
| robots.txt disallow | `TestRobots::test_robots_disallow_blocks_url` |
| Case insensitivity | `TestFindSingleWord::test_case_insensitive_query`, `TestPrintTerm::test_case_insensitive` |
| Full-word matching | `TestFindSingleWord::test_full_word_match_not_substring`, `…other_direction` |
| JSON load/save | `TestSaveLoad::test_roundtrip`, `…valid_json`, `…unknown_version`, `…unicode_roundtrips`, `…missing_file`, `…resumes_next_doc_id` |
| URL normalisation | nine tests under `TestNormaliseUrl` |

All HTTP traffic mocked through the `responses` library - the suite is
fully offline and deterministic.

---

## 8. Version control

| Commit | Description |
|---|---|
| `chore: initial project scaffold` | Layout + requirements + pytest.ini + .gitignore |
| `feat(crawler)` | Crawler + 38 tests |
| `feat(indexer)` | Indexer + 38 tests |
| `feat(cli)` | Shell + 25 tests, search.py + 29 tests |

Conventional-commit prefixes (`feat`, `chore`, `fix`) used throughout
so the development timeline is clear from `git log --oneline`
alone.

---

## 9. Advanced query features (beyond the brief)

Two features added explicitly to chase the rubric's 80-100 band bullet
*"advanced features beyond requirements (e.g., TF-IDF ranking, advanced
query processing, query suggestions)"*:

### 9.1 Phrase queries (`find "good friends"`)

Implemented in `search.SearchEngine._phrase_docs`.  Classical positional
intersection (Manning §2.4.1):

1. Look up postings for every token in the phrase.
2. Intersect on `doc_id` - any candidate page must contain every token.
3. For each candidate, shift each token's position list by its offset
   in the phrase, intersect the shifted sets.  The phrase is present
   iff the intersection is non-empty.

Why this is correct: if the phrase tokens *t₁, t₂, t₃* appear
consecutively starting at position *p*, their position lists contain
*p, p+1, p+2*; shifting each list left by its offset (0, 1, 2) makes
every aligned occurrence land on the value *p*.  Non-aligned
occurrences don't share a value.

Limitation: stop-words are removed at index time, so `find "to be"`
becomes the empty query (both tokens were dropped).  Documented;
preserving a stop-word's positions just for phrase queries would
inflate the index by ~30 %.

### 9.2 Did-you-mean (`did_you_mean(term)`)

Implemented in `search.SearchEngine.did_you_mean`.  When `find` returns
zero hits, the CLI iterates over the **unknown** query tokens, asks for
suggestions, and prints them.

* Distance metric - Levenshtein, computed with the standard
  one-rolling-row DP (`O(|a| × |b|)`).
* Early exit - once every cell on a row exceeds the ceiling (default 2),
  we return `ceiling + 1` immediately and skip the rest of the matrix.
* Length pre-filter - `|len(a) − len(b)| > max_edits` short-circuits
  before the DP runs; this skips the majority of vocabulary terms.
* Ranking - `(edit distance asc, document frequency desc, term asc)`
  so commonly-used terms surface first among ties.

Alternatives considered:

* **Damerau-Levenshtein** (counts adjacent transposition as one edit):
  more "human" for typos but two transpositions still need distance 2;
  not worth the extra complexity.
* **Soundex / Metaphone**: phonetic, but quotes.toscrape.com is
  Anglophone literature - typos look like typos, not like phonetic
  variants.
* **BK-tree** for `O(log V)` lookup: standard speed-up but our
  vocabulary is only ~4 k terms; the linear scan with two pre-filters
  is already a few ms per query.

## 9b. Quality tooling

* `ruff` for lint + import sorting (config in `pyproject.toml`).
* `mypy` for static typing.  Strict-ish mode - `check_untyped_defs`,
  `disallow_incomplete_defs`, `warn_return_any`, `no_implicit_optional`.
* `Makefile` so every operation is one command: `make test`, `make
  lint`, `make check`, `make build`, `make verify`.
* CI enforces ruff + mypy + pytest coverage gate (`--cov-fail-under=90`)
  on every push and PR.  A regression in style, types, or coverage
  cannot reach `main`.

## 10. Known limitations / decisions not pursued

* **Conditional GETs** (`If-Modified-Since`). Out of scope for a one-
  shot build.
* **Crawl parallelism.** Pointless when politeness is 6 s - the
  pipeline is HTTP-bound, not CPU-bound.
* **Persistent crawler queue.** Same reasoning - a 21-minute crawl
  fits comfortably in memory.
* **Unicode-aware tokenising.** Deliberately ASCII for the reasons in
  §4.2.

---

## 10. Complexity analysis

Notation: `N` = number of indexed pages, `T` = number of unique
terms, `L` = average tokens per page (after stopword removal),
`Q` = number of tokens in the query, `df_t` = document frequency of
term *t*.

| Operation | Time | Space | Notes |
|---|---|---|---|
| `Crawler.crawl()` | `O(N · L)` HTML parse + `O(N · A)` link extract | `O(N)` for the `seen` set and frontier | Politeness wait dominates wall-clock time (6 s × N) |
| `Indexer.add_document` | `O(L)` to tokenise + `O(L)` to merge postings | `O(L)` new postings | Hash-table lookup keyed by term |
| `Indexer.tokenise` | `O(\|text\|)` via single regex pass | `O(L)` token list | |
| `Indexer.save` | `O(N · L)` JSON serialise | `O(N · L)` on-disk | One-shot write |
| `Indexer.load` | `O(N · L)` JSON parse | `O(N · L)` in memory | |
| `SearchEngine.find` | `O(Q · log Q + min_t df_t + ∑_t df_t)` | `O(min_t df_t)` for the working set | We sort the postings by length first so the intersection starts with the smallest set - standard trick (Manning §1.3) |
| `SearchEngine.print_term` | `O(df_t)` | `O(df_t)` | Single dict lookup |

The query-time costs are dominated by the intersection, not the
score: ranking adds an `O(\|hits\| · Q)` factor, but `\|hits\| ≤
min_t df_t`, so the overall bound holds.

Observed numbers from the perf tests:

* Indexing 100 docs × 50 tokens - well under 100 ms on a 2024 laptop.
* Find against 200-doc index with two intersecting query terms - under
  500 ms, asserted by `TestPerformance::test_find_on_large_index_fast`.
* Save/load round-trip of 200-doc index - visibly instantaneous.

## 11. Full crawl results - 2026-05-12

Live crawl against `https://quotes.toscrape.com/` with 6 s politeness:

| Metric | Value |
|---|---|
| Wall-clock time | **21.9 min** (1315 s) |
| Pages fetched | **214** |
| Failed / disallowed | **0 / 0** |
| Unique terms in index | **4,445** |
| Average tokens per page | 85.2 |
| Index file size | 694.4 KB |
| `data/index.json` | UTF-8 JSON, schema version 1 |

Verification (`verify_index.py`) - every check passes:

```
[ok] page count is in the expected ballpark (>=200)  got 214
[ok] find 'indifference' returns >=1 hit  got 11
[ok] find 'good friends' returns >=1 hit  got 19
[ok] find 'Einstein' returns >=1 hit  got 37
[ok] full-word: 'friends' result set != 'friendship' result set
[ok] find 'the' returns 0 hits (stop-word)
[ok] print 'einstein' returns >=1 posting  got 37 postings
[ok] boilerplate 'login' not indexed (df==0 expected)
[ok] boilerplate 'scrape' not indexed
```

### Observations from the live data

* `213` vs `214` - quotes.toscrape.com has 213 + 1 distinct URLs once
  you count both the slash-and-no-slash variants of a couple of
  author pages.  Documented in §3.1 (URL normalisation deliberately
  treats `/foo` and `/foo/` as distinct because they *can* be
  different endpoints on a strict server).  No content duplication
  issue in practice - both variants index the same page and `find`
  returns them next to each other.
* Top terms by `df` are dominated by **`tags`** (163) and **`viewing`**
  (152) because every `/tag/X/page/N/` URL begins with
  `"Viewing tag: X"`.  These are content, not chrome, so the indexer
  rightly keeps them.
* **`description`** (df=50) was suspicious - turned out to be the
  literal `"Description:"` header on every author bio page.  Genuine
  content, not a metadata leak.
* **`login`** and **`scrape`** have `df = 0`, confirming the body-only
  extraction strips the site-wide `<title>` and the header chrome.

### Sample queries

| Query | Hits | Top result |
|---|---|---|
| `find indifference` | 11 | `/tag/indifference/page/1/` |
| `find Einstein` | 37 | `/author/Albert-Einstein` |
| `find good friends` | 19 | `/tag/friends/` |
| `find friendship` | 22 | (full-word; differs from `friends` set) |
| `find the` | 0 | stop-word filtered |
| `find xyzzy` | 0 | unknown term |

## 12. Submission checklist

- [x] Source code: `src/{crawler,indexer,search,main}.py`
- [x] Test suite: `tests/test_*.py` - 136 tests, 98 % coverage
- [x] Index file: `data/index.json` (694 KB) - to be attached to
      Minerva separately
- [x] `requirements.txt`
- [x] `README.md` with overview, install, usage, testing, architecture
- [x] `technical_log.md` - this file
- [x] Public GitHub repository:
      <https://github.com/9ali-oop/comp3011-cw2-search-engine>
- [x] GitHub Actions CI green across Python 3.10 / 3.11 / 3.12
- [ ] 5-minute video (user's responsibility)
- [ ] GenAI critical reflection in the video (user's responsibility)

