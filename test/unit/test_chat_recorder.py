import json

from agentic_librarian.chat_recorder import ConversationRecorder


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_transcript_written_without_mlflow(tmp_path):
    rec = ConversationRecorder("adk", "m", "u", "chat", use_mlflow=False, log_dir=str(tmp_path))
    rec.record_turn("hi", "hello", ["tool: x"], 1.25)
    rec.record_turn("bye", "", [], 0.5, error="boom")
    rec.close()
    records = _read_jsonl(rec.transcript_path)
    assert len(records) == 2
    assert records[0] == {"turn": 0, "user": "hi", "reply": "hello", "events": ["tool: x"], "latency_s": 1.25, "error": None}
    assert records[1]["error"] == "boom"
    assert rec.run_id is None


def test_mlflow_run_captured_with_file_store(tmp_path, monkeypatch):
    import mlflow

    uri = (tmp_path / "mlruns").as_uri()
    monkeypatch.setenv("MLFLOW_TRACKING_URI", uri)
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")  # MLflow 3.x requires opt-in
    mlflow.set_tracking_uri(uri)
    rec = ConversationRecorder("claude", "claude-sonnet-4-6", "u", "chat", log_dir=str(tmp_path / "logs"))
    assert rec.run_id
    rec.record_turn("hi", "hello", [], 2.0)
    rec.close()
    run = mlflow.get_run(rec.run_id)
    assert run.data.params["backend"] == "claude"
    assert run.data.metrics["turns"] == 1.0
    assert run.info.status == "FINISHED"


def test_unreachable_mlflow_degrades_to_local_jsonl(tmp_path, monkeypatch, capsys):
    import mlflow

    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:9")
    mlflow.set_tracking_uri("http://127.0.0.1:9")
    rec = ConversationRecorder("adk", "m", "u", "chat", log_dir=str(tmp_path))
    rec.record_turn("hi", "hello", [], 1.0)
    rec.close()  # must not raise
    assert rec.run_id is None
    assert len(_read_jsonl(rec.transcript_path)) == 1
    assert "mlflow capture disabled" in capsys.readouterr().out
