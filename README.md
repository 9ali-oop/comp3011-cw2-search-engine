# COMP3011 CW2 - Search Engine Tool

A small but complete search engine: crawl
[`quotes.toscrape.com`](https://quotes.toscrape.com/), build an inverted
index over its pages, and run multi-word queries against the index from
a command-line shell.

The project is the deliverable for COMP3011 *Web Services and Web Data*
Coursework 2 (University of Leeds, 2025/26).

---

## Table of contents

1. [Features](#features)
2. [Quickstart](#quickstart)
3. [Installation](#installation)
4. [Usage](#usage)
5. [Architecture](#architecture)
6. [Inverted-index layout](#inverted-index-layout)
7. [Design decisions and trade-offs](#design-decisions-and-trade-offs)
8. [Testing](#testing)
9. [Project layout](#project-layout)
10. [Limitations](#limitations)
11. [References](#references)

---

## Features

| Feature | Notes |
|---|---|
| Polite BFS crawler | configurable politeness window, default **6 seconds** per request (brief §1.b) |
| robots.txt aware | parsed once via `urllib.robotparser`; allow-all fallback on fetch failure |
| URL normalisation | strips fragment, lower-cases scheme/host, drops default ports, collapses empty paths |
| Retry with back-off | transient errors (5xx, ConnectionError, Timeout) retried with exponential delay; 4xx treated as permanent |
| Same-host restriction | crawler never follows links off the seed host |
| Inverted index | `term -> {df, postings: {doc_id -> {tf, positions}}}` |
| TF-IDF ranking | smoothed IDF (`log((N+1)/(df+1)) + 1`); sub-linear TF (`1 + log tf`) |
| Boilerplate stripping | `script/style/nav/header/footer/aside` + CSS selectors for site chrome; HTML comments removed |
| Tokenisation | lower-case, split on `[a-z0-9]+`, drop curated 120-word stop-list |
| Full-word matching | by design: tokens are compared exactly, never as substrings - `friends` ≠ `friendship` |
| Case-insensitive search | both index and query tokens are folded to lower-case |
| Multi-word queries | AND semantics, ranked by sum of TF-IDF over query terms |
| JSON persistence | single `index.json` file with schema version |
| 130 unit tests | 98% line coverage, all HTTP mocked, full suite runs in ~1.1 s |

## Quickstart

```bash
# 1. Install dependencies
python -m pip install -r requirements.txt

# 2. Build the index (≈ 21 minutes on the live site - 213 pages × 6 s)
python -m src.main
> build

# 3. Re-open the shell later without re-crawling
python -m src.main
> load
> find good friends
> print indifference
> quit
```

## Installation

Requires **Python 3.10+** (uses `list[str]` PEP 604 union syntax in
type hints).

```bash
git clone <repo-url> search_engine
cd search_engine
python -m pip install -r requirements.txt
```

Dependencies:

| Package | Why |
|---|---|
| `requests` | HTTP client (recommended by brief §1.c) |
| `beautifulsoup4` | HTML parsing (recommended by brief §1.c) |
| `pytest` | test runner |
| `pytest-cov` | coverage report |
| `responses` | mocks HTTP for offline, deterministic tests |

## Usage

Run the REPL with sensible defaults:

```bash
python -m src.main
```

Or override the seed URL / politeness / index path:

```bash
python -m src.main --seed https://quotes.toscrape.com/ \
                   --delay 6 \
                   --index data/index.json
```

### Commands

```
build                 crawl the site, build the index, save data/index.json
load                  read a previously built index
print <word>          show the inverted-index entry for <word>
find <words…>         find pages containing every word in the query
help                  print this list
quit / exit           leave the shell
```

### Sample session

```
$ python -m src.main
COMP3011 search engine - type 'help' for commands, 'quit' to exit.
> build
crawling https://quotes.toscrape.com/ (politeness 6.0s)…
fetched 213 pages (0 failed, 0 disallowed) in 1278.4s
indexed 213 pages, 2683 unique terms; saved to data\index.json (612.7 KB)
> find indifference
1 result(s) for 'indifference':
  [4.812] https://quotes.toscrape.com/tag/love/page/1/
> find good friends
3 result(s) for 'good friends':
  [9.843] https://quotes.toscrape.com/page/5/
  [4.220] https://quotes.toscrape.com/tag/friendship/page/1/
  [2.107] https://quotes.toscrape.com/page/9/
> print nonsense
'nonsense' appears in 2 page(s):
  doc12  tf=1  positions=[145]  https://quotes.toscrape.com/page/1/
  doc54  tf=1  positions=[63]   https://quotes.toscrape.com/author/Lewis-Carroll
> quit
bye.
```

> Exact figures depend on the site at crawl time.

## Architecture

```
            ┌────────────┐
            │  main.py   │  REPL: build / load / print / find / quit
            └─────┬──────┘
                  │ orchestrates
   ┌──────────────┼──────────────┐
   ▼              ▼              ▼
┌────────┐  ┌─────────┐    ┌──────────┐
│Crawler │─►│ Indexer │◄───│SearchEngine│
└────────┘  └─────────┘    └──────────┘
   │            │
   │ fetches    │ JSON ─► data/index.json
   ▼            │ load ◄─
quotes.toscrape.com
```

* `crawler.py` produces a `CrawlResult` (`pages: {url -> html}`,
  `failed`, `disallowed`).  Nothing about indexing leaks into the
  crawler - it could be reused for any other site.
* `indexer.py` consumes `(url, html)` pairs, tokenises and merges them
  into the inverted index; it is the *only* module that knows how to
  serialise / deserialise the index.
* `search.py` is a read-only view over an `Indexer`.  The same
  tokeniser is reused so the index and the query are reduced to
  identical surface forms before comparison - that is what makes the
  search *case-insensitive* and *full-word*.
* `main.py` is a thin REPL.  Both the crawler and the indexer are
  injectable so the CLI tests can run without HTTP.

## Inverted-index layout

The index is a single UTF-8 JSON file with this shape:

```jsonc
{
  "version": 1,
  "stopwords": ["a", "about", "above", ...],
  "documents": {
    "1": {"url": "https://…/", "title": "", "length": 78},
    "2": {"url": "https://…/page/2/", "title": "", "length": 82}
  },
  "terms": {
    "indifference": {
      "df": 1,
      "postings": {
        "12": {"tf": 1, "positions": [145]}
      }
    },
    "friends": {
      "df": 3,
      "postings": {
        "5":  {"tf": 2, "positions": [11, 47]},
        "31": {"tf": 1, "positions": [88]},
        "78": {"tf": 1, "positions": [4]}
      }
    }
  }
}
```

* **`df`** - document frequency (number of pages containing the term).
* **`tf`** - term frequency in the page (raw count).
* **`positions`** - 0-based token offset inside the page, post stop-word
  removal.  Useful for snippet generation or phrase search in future
  versions.
* **`length`** - number of tokens kept in the page after tokenisation
  and stop-word removal.

Storing doc ids as integers (rather than repeating URLs in every
posting) keeps the file roughly half the size it would otherwise be.

## Design decisions and trade-offs

Full reasoning is captured in [`technical_log.md`](technical_log.md).
Summary:

| Decision | Chosen | Alternatives weighed | Why |
|---|---|---|---|
| Index storage | single JSON file | SQLite, pickle, Whoosh | Brief says "save the entire index to a single file"; human-readable; trivial portability |
| Frontier | `collections.deque` | `list.pop(0)`, set | O(1) both ends; preserves BFS order |
| Tokeniser | `re.findall("[a-z0-9]+")` | NLTK word_tokenize, str.split | Matches the brief verbatim; no extra dependency; fast |
| Stop-words | 120-word inline list | NLTK / sklearn / no list | Brief asks for stop-word removal; inline avoids extra dependency; query-example words deliberately kept |
| Ranking | TF-IDF (smoothed IDF, sub-linear TF) | unranked, BM25 | Rubric 80-100 band names TF-IDF explicitly; BM25 is overkill on 213 short pages |
| Politeness | injectable `sleep` + `time.monotonic` clock | hard-coded `time.sleep(6)` | Tests verify the politeness logic without actually waiting |
| HTTP mocking | `responses` library | `unittest.mock.patch('requests.get')` | Less brittle; richer response objects; matches the test pattern recommended in the Python Requests docs |
| Search semantics | AND across query terms | OR | Brief's example `find good friends` implies AND |
| Full-word match | by construction (tokenised both sides) | substring search | The brief says "good ≠ goodness" implicitly via the way it asks for full-word matching |

## Testing

```bash
# Full suite with coverage
python -m pytest --cov=src --cov-report=term-missing -q

# A single module
python -m pytest tests/test_crawler.py -q
```

**Suite stats**

| | |
|---|---|
| Tests | 130 |
| Coverage | 98% (crawler 99 / indexer 99 / search 100 / main 96) |
| Runtime | ~1 s (all HTTP mocked, all sleeps stubbed) |

Coverage spans every required edge case:

* **Crawler** - URL normalisation (port, case, fragment, base, query), link extraction (relative, absolute, mailto, javascript, fragments, malformed HTML), BFS deduplication, frontier traversal, external-host skip, `max_pages` cap, 404, 500-then-200 recovery, ConnectionError, Timeout, all-retries-fail, non-HTML content skip, politeness window (positive sleep, zero-delay no-sleep, default ≥ 6 s), robots.txt (disallow blocked, 404 allow-all, network failure allow-all), `User-Agent` injection, on-page callback.
* **Indexer** - lower-casing, non-alphanumeric splitting, digit retention, apostrophe handling, stop-word filtering, empty/whitespace docs, unicode dropping, boilerplate stripping for `<script>`/`<style>`/`<nav>`/`<header>`/`<footer>`/`<aside>` + the quotes.toscrape.com chrome, comment removal, `<title>`-leak regression, doc-id assignment, position tracking, term re-indexing, JSON round-trip, version rejection, unicode preservation, IDF smoothing.
* **Search** - single-word, multi-word AND, case-insensitive, full-word (`friends` ≠ `friendship` and vice versa), non-existent term, empty query, whitespace-only query, stop-word-only query, punctuation handling, three-word intersection, duplicate query terms, TF ranking, IDF ranking, `limit`, deterministic tiebreak, `print_term` postings, mutation-free queries, empty index.
* **CLI** - `build`, `load`, `print`, `find` happy paths, missing-index `load`, missing-engine `find` and `print`, missing-argument errors, multi-word `find`, no-matches messaging, REPL plumbing (quit, exit, help, blank line, unknown command, error trapping, scripted stdin, case-insensitive commands), argparse defaults and overrides, `main()` smoke test.

Tests use `responses` to mock the HTTP layer, so the suite is fully
offline and deterministic.

## Project layout

```
search_engine/
├─ src/
│   ├─ crawler.py     # polite BFS crawler
│   ├─ indexer.py     # HTML → tokens → inverted index
│   ├─ search.py      # query the index (find / print)
│   └─ main.py        # interactive shell
├─ tests/
│   ├─ test_crawler.py
│   ├─ test_indexer.py
│   ├─ test_search.py
│   └─ test_cli.py
├─ data/
│   └─ index.json     # generated by `build`; gitignored
├─ requirements.txt
├─ pytest.ini
├─ README.md
└─ technical_log.md   # design log
```

## Limitations

Known constraints (called out for anyone reading the repo
knows they were deliberate):

* The crawler does no `Last-Modified` / `If-Modified-Since`
  conditional requests, so a re-crawl is always a full re-fetch.  Out
  of scope for the brief.
* Phrase queries (`find "good friends"` as a single phrase) are not
  implemented; positions are stored ready for it but not yet exposed.
* The tokeniser is ASCII-only.  Curly quotes (`“…”`) on the source
  pages are stripped because they are non-alphanumeric - the quote
  *text inside* is indexed normally.
* Stop-word handling is binary: words are either kept or dropped.  No
  separate "soft" stop-list.

## References

* COMP3011 lecture slides 9-12 (crawling, link analysis, parsing &
  tokenisation, indexing).
* Christopher D. Manning, Prabhakar Raghavan, Hinrich Schütze - *An
  Introduction to Information Retrieval* (Cambridge University Press,
  2008), chapters 1-6.
* Python `requests` documentation - <https://docs.python-requests.org/>
* Beautiful Soup documentation - <https://www.crummy.com/software/BeautifulSoup/bs4/doc/>
* `responses` library - <https://github.com/getsentry/responses>
