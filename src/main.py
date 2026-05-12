"""Interactive shell - the user-facing entry point of the tool.

Run with ``python -m src.main`` from the repository root.  The shell
accepts the four commands mandated by the brief plus ``help`` and
``quit``::

    > build              # crawl + index + save
    > load               # read the saved index
    > print <word>       # dump postings for a word
    > find <words…>      # search (multi-word AND)
    > help
    > quit

All output goes to a configurable stream so the REPL can be tested
without touching ``sys.stdout``.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Iterable, TextIO

from .crawler import Crawler, DEFAULT_DELAY, DEFAULT_USER_AGENT
from .indexer import Indexer
from .search import SearchEngine

DEFAULT_SEED = "https://quotes.toscrape.com/"
DEFAULT_INDEX_PATH = Path("data") / "index.json"
PROMPT = "> "
WELCOME = (
    "COMP3011 search engine - type 'help' for commands, 'quit' to exit."
)
HELP_TEXT = (
    "Available commands:\n"
    "  build               crawl the site and build the index\n"
    "  load                load a previously saved index\n"
    "  print <word>        show the inverted-index entry for <word>\n"
    "  find <words…>       find pages containing every word in the query\n"
    "  help                show this message\n"
    "  quit / exit         leave the shell"
)


class Shell:
    """A tiny REPL.  Stateful: holds the current ``SearchEngine``.

    The class is exposed for the test suite - production code should
    use :func:`main` which wires it up against ``sys.stdin``/``stdout``.
    """

    def __init__(
        self,
        *,
        seed_url: str = DEFAULT_SEED,
        index_path: Path = DEFAULT_INDEX_PATH,
        delay: float = DEFAULT_DELAY,
        user_agent: str = DEFAULT_USER_AGENT,
        out: TextIO | None = None,
        crawler_factory=None,
        indexer_factory=None,
    ) -> None:
        self.seed_url = seed_url
        self.index_path = Path(index_path)
        self.delay = delay
        self.user_agent = user_agent
        self.out: TextIO = out if out is not None else sys.stdout
        self.engine: SearchEngine | None = None
        # Dependency injection seams - tests stub these to avoid HTTP.
        self._crawler_factory = crawler_factory or self._default_crawler
        self._indexer_factory = indexer_factory or Indexer

    # command dispatch

    def execute(self, line: str) -> bool:
        """Execute a single command line.

        Returns ``True`` if the shell should keep running, ``False``
        when the user has asked to quit.  All errors are caught and
        turned into a friendly one-line message so a misbehaving
        command never kills the REPL.
        """
        line = line.strip()
        if not line:
            return True
        head, *rest = line.split(maxsplit=1)
        arg = rest[0] if rest else ""
        cmd = head.lower()
        try:
            if cmd == "build":
                self.cmd_build()
            elif cmd == "load":
                self.cmd_load()
            elif cmd == "print":
                self.cmd_print(arg)
            elif cmd == "find":
                self.cmd_find(arg)
            elif cmd in ("help", "?"):
                self._echo(HELP_TEXT)
            elif cmd in ("quit", "exit"):
                self._echo("bye.")
                return False
            else:
                self._echo(f"unknown command: {cmd!r}  (type 'help')")
        except Exception as exc:  # noqa: BLE001 - last-ditch REPL guard
            self._echo(f"error: {exc}")
        return True

    def run(self, stdin: Iterable[str] | None = None) -> None:
        """Read-eval-print loop.

        If *stdin* is given (e.g. ``["build", "find cat", "quit"]``) the
        shell consumes that iterable; otherwise it falls back to the
        process's standard input.  The non-stdin mode is used both for
        scripting and by the test suite.
        """
        self._echo(WELCOME)
        if stdin is None:
            self._repl(sys.stdin)
        else:
            for raw in stdin:
                self._echo(PROMPT + raw, end="\n")
                if not self.execute(raw):
                    break

    # individual commands

    def cmd_build(self) -> None:
        """Crawl the seed site and persist a fresh index."""
        self._echo(f"crawling {self.seed_url} (politeness {self.delay}s)…")
        crawler = self._crawler_factory()
        start = time.monotonic()
        # Periodic progress callback so the user has something to look
        # at during the (multi-minute) full live crawl.
        counter = {"n": 0}

        def on_page(url: str, _html: str) -> None:
            counter["n"] += 1
            if counter["n"] % 10 == 0 or counter["n"] <= 3:
                self._echo(f"  [{counter['n']:>4}] {url}")

        result = crawler.crawl(on_page=on_page)
        crawl_secs = time.monotonic() - start

        self._echo(
            f"fetched {len(result.pages)} pages "
            f"({len(result.failed)} failed, "
            f"{len(result.disallowed)} disallowed) "
            f"in {crawl_secs:.1f}s"
        )

        indexer = self._indexer_factory()
        indexer.add_documents(result.pages)

        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        indexer.save(self.index_path)
        size_kb = self.index_path.stat().st_size / 1024
        self._echo(
            f"indexed {indexer.num_docs()} pages, "
            f"{len(indexer.terms)} unique terms; "
            f"saved to {self.index_path} ({size_kb:.1f} KB)"
        )
        self.engine = SearchEngine(indexer)

    def cmd_load(self) -> None:
        """Load a previously built index from :attr:`index_path`."""
        if not self.index_path.exists():
            self._echo(
                f"no index found at {self.index_path}; run 'build' first"
            )
            return
        indexer = Indexer.load(self.index_path)
        self.engine = SearchEngine(indexer)
        self._echo(
            f"loaded index: {indexer.num_docs()} pages, "
            f"{len(indexer.terms)} unique terms"
        )

    def cmd_print(self, term: str) -> None:
        """Dump the inverted-index entry for *term*."""
        if not self._require_engine():
            return
        if not term:
            self._echo("usage: print <word>")
            return
        postings = self.engine.print_term(term)
        if not postings:
            self._echo(f"'{term}' is not in the index")
            return
        self._echo(f"'{term}' appears in {len(postings)} page(s):")
        for p in postings:
            positions = ", ".join(str(x) for x in p.positions)
            self._echo(
                f"  doc{p.doc_id}  tf={p.tf}  positions=[{positions}]  {p.url}"
            )

    def cmd_find(self, query: str) -> None:
        """Look up pages matching *query* (multi-word AND, ranked).

        Quoted spans (``find "good friends"``) are treated as phrase
        queries that require adjacency.  Zero-result queries trigger a
        "did you mean?" hint computed by edit distance against the
        vocabulary.
        """
        if not self._require_engine():
            return
        if not query.strip():
            self._echo("usage: find <words…>")
            return
        hits = self.engine.find(query)
        if not hits:
            self._echo(f"no pages match {query!r}")
            self._maybe_suggest_alternatives(query)
            return
        self._echo(f"{len(hits)} result(s) for {query!r}:")
        for h in hits:
            label = f" - {h.title}" if h.title else ""
            self._echo(f"  [{h.score:.3f}] {h.url}{label}")

    def _maybe_suggest_alternatives(self, query: str) -> None:
        """Print a 'did you mean?' line for each unknown query token."""
        tokens = self.engine._tokenise_query(query)
        for token in tokens:
            if self.engine.index.term_postings(token):
                continue
            suggestions = self.engine.did_you_mean(token)
            if suggestions:
                joined = ", ".join(suggestions)
                self._echo(
                    f"  did you mean: {joined}?  (instead of {token!r})"
                )

    # helpers

    def _default_crawler(self) -> Crawler:
        return Crawler(
            self.seed_url,
            delay=self.delay,
            user_agent=self.user_agent,
            obey_robots=True,
        )

    def _require_engine(self) -> bool:
        if self.engine is None:
            self._echo("no index loaded - run 'build' or 'load' first")
            return False
        return True

    def _echo(self, msg: str, *, end: str = "\n") -> None:
        self.out.write(msg + end)
        self.out.flush()

    def _repl(self, stdin: TextIO) -> None:
        """Interactive loop reading from a TTY."""
        while True:
            self.out.write(PROMPT)
            self.out.flush()
            line = stdin.readline()
            if not line:  # EOF / Ctrl-D
                self._echo("")
                self._echo("bye.")
                return
            if not self.execute(line.rstrip("\n")):
                return


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_engine",
        description="COMP3011 CW2 search-engine tool.",
    )
    parser.add_argument(
        "--seed",
        default=DEFAULT_SEED,
        help="seed URL for the crawler (default: %(default)s)",
    )
    parser.add_argument(
        "--index",
        default=str(DEFAULT_INDEX_PATH),
        help="path to the JSON index file (default: %(default)s)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help="politeness window in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable INFO-level logging",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point used by ``python -m src.main`` and the console script."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    # Force UTF-8 on the stream - quotes.toscrape.com uses curly quotes
    # which the default Windows codepage cannot encode.  ``reconfigure``
    # exists only on TextIOWrapper, so guard it.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (AttributeError, ValueError):
                pass
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    shell = Shell(
        seed_url=args.seed,
        index_path=Path(args.index),
        delay=args.delay,
    )
    try:
        shell.run()
    except KeyboardInterrupt:
        shell._echo("")
        shell._echo("bye.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
