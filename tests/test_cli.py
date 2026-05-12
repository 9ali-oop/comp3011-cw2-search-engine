"""Integration tests for the CLI shell.

We avoid hitting the network by injecting a fake ``Crawler`` factory
into the shell.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from src.crawler import CrawlResult
from src.indexer import Indexer
from src.main import Shell, build_arg_parser, main


SAMPLE_PAGES = {
    "http://x/1": "<body>The cat sat on the mat.</body>",
    "http://x/2": "<body>Dog and cat played all day.</body>",
    "http://x/3": "<body>Indifference is a poison.</body>",
    "http://x/4": "<body>Good friends matter.</body>",
}


def _fake_crawler_factory(pages=SAMPLE_PAGES):
    """Return a callable that produces a stub crawler."""

    class _Fake:
        def crawl(self, *, on_page=None, **_kw):
            for url, html in pages.items():
                if on_page is not None:
                    on_page(url, html)
            return CrawlResult(pages=dict(pages))

    return _Fake


def _make_shell(tmp_path: Path, **overrides) -> tuple[Shell, io.StringIO]:
    out = io.StringIO()
    shell = Shell(
        seed_url="http://x/",
        index_path=tmp_path / "index.json",
        delay=0,
        out=out,
        crawler_factory=_fake_crawler_factory(overrides.pop("pages", SAMPLE_PAGES)),
    )
    return shell, out


# Smoke tests for individual commands

class TestBuildCommand:
    def test_build_writes_index_and_loads_engine(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("build")
        assert (tmp_path / "index.json").exists()
        assert shell.engine is not None
        assert "indexed 4 pages" in out.getvalue()

    def test_build_creates_data_dir(self, tmp_path):
        shell, _ = _make_shell(tmp_path)
        # data_path is tmp_path/index.json -> parent already exists.
        # Try a nested path that doesn't exist yet.
        shell.index_path = tmp_path / "sub" / "dir" / "i.json"
        shell.execute("build")
        assert shell.index_path.exists()


class TestLoadCommand:
    def test_load_after_build_succeeds(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("build")
        out.truncate(0)
        out.seek(0)
        shell.execute("load")
        assert "loaded index" in out.getvalue()

    def test_load_without_index_warns(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("load")
        assert "no index found" in out.getvalue()


class TestFindCommand:
    def test_find_returns_matches(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("build")
        out.truncate(0)
        out.seek(0)
        shell.execute("find cat")
        text = out.getvalue()
        assert "result(s) for 'cat'" in text
        assert "http://x/1" in text or "http://x/2" in text

    def test_find_no_matches_message(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("build")
        out.truncate(0)
        out.seek(0)
        shell.execute("find xyzzy")
        assert "no pages match" in out.getvalue()

    def test_find_multi_word_and(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("build")
        out.truncate(0)
        out.seek(0)
        shell.execute("find good friends")
        text = out.getvalue()
        assert "http://x/4" in text

    def test_find_requires_engine(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("find cat")
        assert "no index loaded" in out.getvalue()

    def test_find_requires_argument(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("build")
        out.truncate(0)
        out.seek(0)
        shell.execute("find")
        assert "usage: find" in out.getvalue()

    def test_find_handles_empty_string_query(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("build")
        out.truncate(0)
        out.seek(0)
        shell.execute("find    ")
        assert "usage: find" in out.getvalue()


class TestPrintCommand:
    def test_print_dumps_postings(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("build")
        out.truncate(0)
        out.seek(0)
        shell.execute("print cat")
        text = out.getvalue()
        assert "'cat' appears in" in text
        assert "tf=" in text and "positions=" in text

    def test_print_missing_word(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("build")
        out.truncate(0)
        out.seek(0)
        shell.execute("print xyzzy")
        assert "is not in the index" in out.getvalue()

    def test_print_requires_word(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("build")
        out.truncate(0)
        out.seek(0)
        shell.execute("print")
        assert "usage: print" in out.getvalue()

    def test_print_requires_engine(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("print cat")
        assert "no index loaded" in out.getvalue()


# REPL plumbing

class TestRepl:
    def test_quit_returns_false(self, tmp_path):
        shell, _ = _make_shell(tmp_path)
        assert shell.execute("quit") is False

    def test_exit_alias(self, tmp_path):
        shell, _ = _make_shell(tmp_path)
        assert shell.execute("exit") is False

    def test_help_prints_command_list(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("help")
        text = out.getvalue()
        for kw in ("build", "load", "print", "find", "quit"):
            assert kw in text

    def test_unknown_command_message(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("bogusssss")
        assert "unknown command" in out.getvalue()

    def test_blank_line_is_no_op(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        before = out.getvalue()
        shell.execute("")
        assert out.getvalue() == before

    def test_error_in_command_does_not_crash(self, tmp_path, monkeypatch):
        shell, out = _make_shell(tmp_path)
        shell.execute("build")

        # Force the engine to raise to confirm the REPL traps it.
        def boom(*a, **kw):
            raise RuntimeError("kapow")

        monkeypatch.setattr(shell.engine, "find", boom)
        out.truncate(0)
        out.seek(0)
        keep_going = shell.execute("find cat")
        assert keep_going is True
        assert "error: kapow" in out.getvalue()

    def test_run_consumes_scripted_stdin(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.run(["build", "find cat", "quit"])
        text = out.getvalue()
        assert "indexed" in text
        assert "result(s) for 'cat'" in text
        assert "bye." in text

    def test_command_is_case_insensitive(self, tmp_path):
        shell, out = _make_shell(tmp_path)
        shell.execute("BUILD")
        assert "indexed" in out.getvalue()


# argparse

class TestCli:
    def test_arg_parser_defaults(self):
        parser = build_arg_parser()
        args = parser.parse_args([])
        assert args.seed.startswith("http")
        assert args.delay >= 6

    def test_arg_parser_overrides(self):
        parser = build_arg_parser()
        args = parser.parse_args(
            ["--seed", "http://x/", "--delay", "0", "--index", "/tmp/i.json"]
        )
        assert args.seed == "http://x/"
        assert args.delay == 0
        assert args.index == "/tmp/i.json"

    def test_main_returns_zero(self, tmp_path, monkeypatch):
        # main() runs the REPL; provide an empty stdin to make it exit.
        import io as _io
        monkeypatch.setattr("sys.stdin", _io.StringIO(""))
        monkeypatch.setattr("sys.stdout", _io.StringIO())
        rc = main(
            [
                "--seed", "http://x/",
                "--delay", "0",
                "--index", str(tmp_path / "i.json"),
            ]
        )
        assert rc == 0
