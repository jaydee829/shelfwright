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
    assert "· tool: search_internal_database" in out  # event trace
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
    assert record["events"] == ["tool: search_internal_database"]  # recorded even when not printed


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
