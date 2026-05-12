"""Sanity-check a built index against the brief's example queries.

Run after ``build`` to confirm the live crawl produced an index that
behaves as the brief expects::

    python verify_index.py

Prints a per-check ✓/✗ summary and exits non-zero on the first failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from src.indexer import Indexer
from src.search import SearchEngine

INDEX_PATH = Path("data") / "index.json"

CHECKS = [
    ("Index file exists", lambda: INDEX_PATH.exists()),
    ("Index loads cleanly", None),  # filled in below
]


def main() -> int:
    if not INDEX_PATH.exists():
        print(f"ERROR: {INDEX_PATH} not found - run 'build' first")
        return 1

    raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    size_kb = INDEX_PATH.stat().st_size / 1024
    print(f"file: {INDEX_PATH}  ({size_kb:.1f} KB)")
    print(f"version: {raw.get('version')}")
    print(f"documents: {len(raw.get('documents', {}))}")
    print(f"unique terms: {len(raw.get('terms', {}))}")
    print()

    idx = Indexer.load(INDEX_PATH)
    eng = SearchEngine(idx)
    failed = 0

    def report(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failed
        mark = "ok " if ok else "FAIL"
        print(f"  [{mark}] {label}  {detail}")
        if not ok:
            failed += 1

    # 1. Document count.  ~213 expected for quotes.toscrape.com.
    n_docs = idx.num_docs()
    report(
        "page count is in the expected ballpark (>=200)",
        n_docs >= 200,
        f"got {n_docs}",
    )

    # 2. Brief's example queries return at least one hit.
    for q in ("indifference", "good friends", "Einstein"):
        hits = eng.find(q)
        report(f"find {q!r} returns >=1 hit", len(hits) >= 1, f"got {len(hits)}")

    # 3. Full-word matching: 'friends' must not match 'friendship' tokens.
    friends_urls = {h.url for h in eng.find("friends")}
    friendship_urls = {h.url for h in eng.find("friendship")}
    # They can overlap (a page may mention both) but the sets should not
    # be equal - otherwise full-word matching is broken.
    report(
        "full-word: 'friends' result set != 'friendship' result set",
        friends_urls != friendship_urls or not friends_urls,
    )

    # 4. Stop-word queries return empty.
    report("find 'the' returns 0 hits (stop-word)", eng.find("the") == [])

    # 5. print_term works for a common word.
    print_einstein = eng.print_term("einstein")
    report(
        "print 'einstein' returns >=1 posting",
        len(print_einstein) >= 1,
        f"got {len(print_einstein)} postings",
    )

    # 6. No page contains the boilerplate 'login'.
    report(
        "boilerplate 'login' not indexed (df==0 expected)",
        idx.terms.get("login", {}).get("df", 0) == 0,
    )

    # 7. No page contains the boilerplate 'scrape' (from <title>).
    report(
        "boilerplate 'scrape' not indexed",
        idx.terms.get("scrape", {}).get("df", 0) == 0,
    )

    print()
    if failed:
        print(f"{failed} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
