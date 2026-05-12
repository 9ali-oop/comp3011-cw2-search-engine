# Technical Log - COMP3011 CW2 Search Engine

> Engineering log for COMP3011 CW2. Records the major design
> decisions I made, alternatives I weighed, test failures I hit
> and how I fixed them. Kept separate from the README so the
> design rationale can live in detail without bloating the
> public docs.

---

## 0. Mission re-read (from the brief)

- Target: `https://quotes.toscrape.com/` (~213 pages).
- Politeness window ≥ 6 s between requests (CW2 brief §1.b).
- Inverted index must record statistics per word per page
  (frequency, position).
- Search is case-insensitive and full-word (`good` = `Good`, but
  `friends` ≠ `friendship`).
- CLI must support `build`, `load`, `print <word>`, `find <query…>`.
- I add `quit`/`exit` for usability - outside the brief but expected of a
  REPL.

Rubric weighting that drives priorities:

- Testing & coverage: 20%  → ≥85% line coverage, ≥30 unit tests, edge
  cases, mocked HTTP.
- Search functionality: 12% → multi-word AND queries, ranking.
- GenAI critical reflection: 15% (owner: user, not me - I supply the
  raw engineering diary they will mine for reflection material).

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

- Flat (all `.py` in repo root): faster to start with but harder to
  package and not what the brief asks for. Rejected.
- `searchengine/` package + `tests/` peer (typical PyPI layout): nicer
  for distribution but the brief explicitly names `src/`. Rejected to
  match the markscheme example.

## 2. Dependencies

`requirements.txt`:

| Library | Why |
|---|---|
| `requests` | brief recommends it (§1.c) |
| `beautifulsoup4` | brief recommends it (§1.c) |
| `pytest` | de-facto standard, supports parametrisation |
| `pytest-cov` | rubric values coverage - we want a number |
| `responses` | mock HTTP cleanly. Considered `unittest.mock.patch('requests.get')` but `responses` is far less brittle and gives realistic response objects |

Stdlib-only alternatives ruled out:

- `urllib.request` + `html.parser` would have worked but the brief
  recommends requests/BS4, and using them is the path of least
  surprise for anyone reading the code.

## 3. Open decisions (to be resolved as code lands)

- [ ] Single-file JSON vs. sharded JSON vs. SQLite for the index store.
- [ ] Stopword list source (NLTK vs. Sklearn vs. inline).
- [ ] Ranking: TF-IDF vs. BM25 vs. unranked. (Hint: rubric explicitly
  mentions TF-IDF in 80-100 band.)
- [ ] Crawler frontier: list-based BFS vs. `collections.deque`.

These get a section each below as they are decided.
