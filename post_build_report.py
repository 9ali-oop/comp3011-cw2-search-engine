"""Generate the post-build report.

Reads ``data/index.json`` (built by the live crawl) and prints a
human-friendly summary plus the brief's example queries.  Used by the
final checkpoint to confirm all 213 pages are indexed correctly.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.indexer import Indexer
from src.search import SearchEngine

INDEX = Path("data") / "index.json"


def main() -> int:
    if not INDEX.exists():
        print(f"missing: {INDEX}")
        return 1

    raw = json.loads(INDEX.read_text(encoding="utf-8"))
    size_kb = INDEX.stat().st_size / 1024
    idx = Indexer.load(INDEX)
    eng = SearchEngine(idx)

    print("=" * 60)
    print(f"INDEX REPORT - {INDEX}")
    print("=" * 60)
    print(f"file size       {size_kb:>8.1f} KB")
    print(f"schema version  {raw.get('version'):>8}")
    print(f"documents       {idx.num_docs():>8}")
    print(f"unique terms    {len(idx.terms):>8}")
    avg_len = (
        sum(d.length for d in idx.documents.values()) / idx.num_docs()
        if idx.num_docs() else 0
    )
    print(f"avg tokens/doc  {avg_len:>8.1f}")
    print()

    print("top 10 terms by document frequency:")
    top = sorted(idx.terms.items(), key=lambda kv: -kv[1]["df"])[:10]
    for term, entry in top:
        total_tf = sum(p["tf"] for p in entry["postings"].values())
        print(f"  {term:<20} df={entry['df']:>4}  total tf={total_tf:>4}")
    print()

    print("brief's example queries:")
    for q in ("indifference", "Einstein", "good friends", "the", "xyzzy"):
        hits = eng.find(q)
        if hits:
            line = f"  find {q!r:<22} -> {len(hits)} hit(s)"
            print(line)
            for h in hits[:3]:
                print(f"      [{h.score:.3f}] {h.url}")
            if len(hits) > 3:
                print(f"      … + {len(hits) - 3} more")
        else:
            print(f"  find {q!r:<22} -> no matches")
    print()

    print("full-word match regression check:")
    f = {h.url for h in eng.find("friends")}
    fs = {h.url for h in eng.find("friendship")}
    print(f"  pages containing 'friends'     : {len(f)}")
    print(f"  pages containing 'friendship'  : {len(fs)}")
    print(f"  identical sets?                : {f == fs}")
    print()

    print("print nonsense (first 5 postings):")
    for p in eng.print_term("nonsense")[:5]:
        print(f"  doc{p.doc_id}  tf={p.tf}  positions={list(p.positions)[:5]}  {p.url}")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
