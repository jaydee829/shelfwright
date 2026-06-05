"""MLflow conversation capture for the CLI chat harness (ADR-045). One conversation = one
MLflow run. Degradation posture (cf. the 2026-05-31 MLflow 403 bug): MLflow must NEVER block
or kill a chat — every MLflow call is guarded with warn-and-continue, and the transcript is
always written to a local jsonl regardless."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

_EXPERIMENT = "librarian_conversations"


class ConversationRecorder:
    def __init__(
        self,
        backend: str,
        model: str,
        user_id: str,
        mode: str,
        use_mlflow: bool = True,
        log_dir: str = ".chat_logs",
    ):
        self._t0 = time.monotonic()
        self._turns = 0
        self._mlflow = None
        self.run_id: str | None = None
        path_dir = Path(log_dir)
        path_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_path = path_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{backend}.jsonl"
        if use_mlflow:
            try:
                import mlflow

                mlflow.set_experiment(_EXPERIMENT)
                run = mlflow.start_run(run_name=f"{backend}-{mode}")
                mlflow.log_params({"backend": backend, "model": model, "user_id": user_id, "mode": mode})
                self._mlflow = mlflow
                self.run_id = run.info.run_id
            except Exception as e:  # degradation posture: warn once, never block the chat
                print(f"warning: mlflow capture disabled ({type(e).__name__}: {e})")

    def record_turn(
        self,
        user_text: str,
        reply: str,
        events: list[str],
        latency_s: float,
        error: str | None = None,
    ) -> None:
        record = {
            "turn": self._turns,
            "user": user_text,
            "reply": reply,
            "events": events,
            "latency_s": round(latency_s, 3),
            "error": error,
        }
        with self.transcript_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if self._mlflow:
            try:
                self._mlflow.log_metric("latency_s", latency_s, step=self._turns)
            except Exception as e:
                print(f"warning: mlflow log_metric failed ({type(e).__name__}: {e})")
        self._turns += 1

    def close(self, status: str = "FINISHED") -> None:
        if not self._mlflow:
            return
        try:
            self._mlflow.log_metric("turns", self._turns)
            self._mlflow.log_metric("duration_s", time.monotonic() - self._t0)
            if self.transcript_path.exists():
                self._mlflow.log_artifact(str(self.transcript_path))
        except Exception as e:
            print(f"warning: mlflow close failed ({type(e).__name__}: {e})")
        # end_run gets its own guard so a failed metric/artifact upload can't leave the
        # run stuck in RUNNING (mlflow's active run is process-global).
        try:
            self._mlflow.end_run(status=status)
        except Exception as e:
            print(f"warning: mlflow end_run failed ({type(e).__name__}: {e})")
