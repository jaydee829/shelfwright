import builtins

import pytest
from agentic_librarian import cli


class _FakeConversation:
    def __init__(self, replies, on_event=None):
        self._replies = list(replies)
        self.on_event = on_event
        self.sent = []
        self.closed = False

    def send(self, message):
        self.sent.append(message)
        if self.on_event:
            self.on_event("tool", "search_internal_database")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def close(self):
        self.closed = True


class _FakeBackend:
    name = "fake"

    def __init__(self, replies=("a reply",)):
        self._replies = replies
        self.conversation = None

    def run_recommendation(self, prompt, user_id="local"):
        return f"one-shot: {prompt}"

    def start_conversation(self, user_id="local", on_event=None):
        self.conversation = _FakeConversation(self._replies, on_event)
        return self.conversation


@pytest.fixture
def no_mlflow_dir(tmp_path, monkeypatch):
    # Keep transcripts inside tmp and never touch MLflow in CLI tests.
    monkeypatch.setattr(cli, "_LOG_DIR", str(tmp_path))


def _feed_stdin(monkeypatch, lines):
    it = iter(lines)
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(it))


def test_once_prints_recommendation(monkeypatch, capsys, no_mlflow_dir):
    fake = _FakeBackend()
    monkeypatch.setattr(cli, "get_backend", lambda: fake)
    rc = cli.main(["--once", "heist novel", "--no-mlflow"])
    assert rc == 0
    assert "one-shot: heist novel" in capsys.readouterr().out


def test_repl_two_turns_then_quit(monkeypatch, capsys, no_mlflow_dir):
    fake = _FakeBackend(replies=("first reply", "second reply"))
    monkeypatch.setattr(cli, "get_backend", lambda: fake)
    _feed_stdin(monkeypatch, ["hello", "more", "/quit"])
    rc = cli.main(["--no-mlflow"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "librarian> first reply" in out
    assert "librarian> second reply" in out
    assert "tool: search_internal_database" in out  # event trace
    assert fake.conversation.closed


def test_repl_survives_a_failed_turn(monkeypatch, capsys, no_mlflow_dir):
    fake = _FakeBackend(replies=(RuntimeError("boom"), "recovered"))
    monkeypatch.setattr(cli, "get_backend", lambda: fake)
    _feed_stdin(monkeypatch, ["explode", "again", "/quit"])
    rc = cli.main(["--no-mlflow"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "error: RuntimeError: boom" in out
    assert "librarian> recovered" in out


def test_quiet_suppresses_event_trace_but_still_records(tmp_path, monkeypatch, capsys):
    import json

    monkeypatch.setattr(cli, "_LOG_DIR", str(tmp_path))
    fake = _FakeBackend(replies=("ok",))
    monkeypatch.setattr(cli, "get_backend", lambda: fake)
    _feed_stdin(monkeypatch, ["hi", "/quit"])
    cli.main(["--no-mlflow", "--quiet"])
    assert "· tool:" not in capsys.readouterr().out
    transcripts = list(tmp_path.glob("*.jsonl"))
    assert len(transcripts) == 1
    record = json.loads(transcripts[0].read_text(encoding="utf-8").splitlines()[0])
    assert len(record["events"]) == 1
    assert record["events"][0].endswith("tool: search_internal_database")  # carries elapsed prefix


def test_backend_flag_sets_env(monkeypatch, capsys, no_mlflow_dir):
    fake = _FakeBackend()
    monkeypatch.setattr(cli, "get_backend", lambda: fake)
    monkeypatch.delenv("AGENT_BACKEND", raising=False)
    cli.main(["--once", "x", "--no-mlflow", "--backend", "claude"])
    import os

    assert os.environ["AGENT_BACKEND"] == "claude"


def test_unknown_backend_fails_fast(monkeypatch, capsys, no_mlflow_dir):
    def _boom():
        raise ValueError("Unknown AGENT_BACKEND='nope'")

    monkeypatch.setattr(cli, "get_backend", _boom)
    rc = cli.main(["--once", "x", "--no-mlflow"])
    assert rc == 2
    assert "Unknown AGENT_BACKEND" in capsys.readouterr().err


def test_events_carry_elapsed_seconds_prefix(monkeypatch, capsys, no_mlflow_dir):
    import re

    fake = _FakeBackend(replies=("ok",))
    monkeypatch.setattr(cli, "get_backend", lambda: fake)
    _feed_stdin(monkeypatch, ["hi", "/quit"])
    cli.main(["--no-mlflow"])
    out = capsys.readouterr().out
    # "  · 0.0s tool: search_internal_database" — elapsed-seconds-into-turn prefix (tuning spec)
    assert re.search(r"· \d+\.\ds tool: search_internal_database", out)


def test_add_subcommand_success(monkeypatch, capsys):
    captured = {}

    def _fake_add(**kwargs):
        captured.update(kwargs)
        return "Added 'Project Hail Mary' to your reading history (work abc, read #1)."

    monkeypatch.setattr("agentic_librarian.mcp.server.add_book_to_history", _fake_add)
    rc = cli.main(["add", "Project Hail Mary", "--author", "Andy Weir", "--rating", "5", "--date", "2026-06-01"])
    assert rc == 0
    assert "Added 'Project Hail Mary'" in capsys.readouterr().out
    assert captured == {
        "title": "Project Hail Mary",
        "author": "Andy Weir",
        "date_completed": "2026-06-01",
        "rating": 5,
        "format": "ebook",
        "notes": None,
    }


def test_add_subcommand_error_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(
        "agentic_librarian.mcp.server.add_book_to_history",
        lambda **kw: "Error: rating must be an integer from 1 to 5; got 9.",
    )
    rc = cli.main(["add", "T", "--author", "A", "--rating", "9"])
    assert rc == 1
    assert "Error" in capsys.readouterr().out


def test_add_requires_author(capsys):
    with pytest.raises(SystemExit):
        cli.main(["add", "Some Title"])  # argparse exits on missing --author


def test_repl_default_unaffected_by_subparsers(monkeypatch, capsys, no_mlflow_dir):
    # Bare `librarian` (no subcommand) must still enter the REPL path.
    fake = _FakeBackend(replies=("ok",))
    monkeypatch.setattr(cli, "get_backend", lambda: fake)
    _feed_stdin(monkeypatch, ["hi", "/quit"])
    assert cli.main(["--no-mlflow"]) == 0
