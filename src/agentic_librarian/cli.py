"""`librarian` CLI — multi-turn chat REPL + one-shot recommendation test harness (ADR-045).
Run inside the app container: `docker exec -it agentic_librarian_app librarian`."""

from __future__ import annotations

import argparse
import os
import sys
import time

from agentic_librarian.agents.backends import get_backend
from agentic_librarian.chat_recorder import ConversationRecorder

_LOG_DIR = ".chat_logs"


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="librarian", description="Chat with the Librarian recommendation agent.")
    parser.add_argument("--once", metavar="PROMPT", help="one-shot recommendation (pipeline), then exit")
    parser.add_argument("--backend", choices=["adk", "claude"], help="override AGENT_BACKEND for this run")
    parser.add_argument("--user-id", default="local", help="user id for sessions and history (default: local)")
    parser.add_argument("--quiet", action="store_true", help="suppress the key-event trace")
    parser.add_argument("--no-mlflow", action="store_true", help="disable MLflow conversation capture")
    return parser.parse_args(argv)


def _model_label(backend_name: str) -> str:
    if backend_name == "claude":
        return os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
    return os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")


def main(argv=None) -> int:
    args = _parse_args(argv)
    if args.backend:
        os.environ["AGENT_BACKEND"] = args.backend
    try:
        backend = get_backend()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    recorder = ConversationRecorder(
        backend.name,
        _model_label(backend.name),
        args.user_id,
        "one-shot" if args.once else "chat",
        use_mlflow=not args.no_mlflow,
        log_dir=_LOG_DIR,
    )
    if args.once:
        return _run_once(backend, args, recorder)
    return _run_repl(backend, args, recorder)


def _run_once(backend, args, recorder) -> int:
    t0 = time.monotonic()
    try:
        reply = backend.run_recommendation(args.once, user_id=args.user_id)
    except Exception as e:
        recorder.record_turn(args.once, "", [], time.monotonic() - t0, error=f"{type(e).__name__}: {e}")
        recorder.close(status="FAILED")
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(reply)
    recorder.record_turn(args.once, reply, [], time.monotonic() - t0)
    recorder.close()
    return 0


def _run_repl(backend, args, recorder) -> int:
    turn_events: list[str] = []

    def on_event(kind: str, detail: str) -> None:
        turn_events.append(f"{kind}: {detail}")
        if not args.quiet:
            print(f"  · {kind}: {detail}")

    try:
        conversation = backend.start_conversation(user_id=args.user_id, on_event=on_event)
    except Exception as e:
        recorder.close(status="FAILED")
        print(f"error: could not start conversation: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(
        f"librarian chat — backend: {backend.name} ({_model_label(backend.name)})"
        f" | mlflow: {recorder.run_id or 'off'} | /quit to exit"
    )
    try:
        while True:
            try:
                line = input("you> ")
            except (EOFError, KeyboardInterrupt, StopIteration):
                print()
                break
            line = line.strip()
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            turn_events.clear()
            t0 = time.monotonic()
            try:
                reply = conversation.send(line)
            except KeyboardInterrupt:
                print("(turn aborted)")
                recorder.record_turn(line, "", list(turn_events), time.monotonic() - t0, error="aborted")
                continue
            except Exception as e:
                print(f"error: {type(e).__name__}: {e}")
                recorder.record_turn(
                    line, "", list(turn_events), time.monotonic() - t0, error=f"{type(e).__name__}: {e}"
                )
                continue
            print(f"librarian> {reply}")
            recorder.record_turn(line, reply, list(turn_events), time.monotonic() - t0)
    finally:
        try:
            conversation.close()
        except Exception as e:
            print(f"warning: close failed ({type(e).__name__}: {e})")
        recorder.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
