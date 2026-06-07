"""`librarian` CLI — multi-turn chat REPL + one-shot recommendation test harness (ADR-045).
Run inside the app container: `docker exec -it agentic_librarian_app librarian`."""

from __future__ import annotations

import argparse
import os
import sys
import time

from agentic_librarian.agents.backends import get_backend
from agentic_librarian.chat_recorder import ConversationRecorder
from agentic_librarian.core.user_context import DEFAULT_USER_ID, as_user

_LOG_DIR = ".chat_logs"


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="librarian", description="Chat with the Librarian recommendation agent.")
    parser.add_argument("--once", metavar="PROMPT", help="one-shot recommendation (pipeline), then exit")
    parser.add_argument("--backend", choices=["adk", "claude"], help="override AGENT_BACKEND for this run")
    parser.add_argument("--user-id", default="local", help="user id for sessions and history (default: local)")
    parser.add_argument("--quiet", action="store_true", help="suppress the key-event trace")
    parser.add_argument("--no-mlflow", action="store_true", help="disable MLflow conversation capture")
    subparsers = parser.add_subparsers(dest="command")
    add_parser = subparsers.add_parser("add", help="add one book to your reading history (no LLM involved)")
    add_parser.add_argument("title", help="book title")
    add_parser.add_argument("--author", required=True, help="author name")
    add_parser.add_argument("--date", default=None, help="completion date YYYY-MM-DD (default: today)")
    add_parser.add_argument("--rating", type=int, default=None, help="rating 1-5")
    add_parser.add_argument("--format", default="ebook", help="edition format (default: ebook)")
    add_parser.add_argument("--notes", default=None, help="free-text notes")
    user_parser = subparsers.add_parser("user", help="account management (operator, Lift 1)")
    user_sub = user_parser.add_subparsers(dest="user_command")
    invite_parser = user_sub.add_parser("invite", help="invite an email — creates the user row; they sign in later")
    invite_parser.add_argument("email", help="the invitee's email (the invite key; lowercased)")
    invite_parser.add_argument("--name", default=None, help="display name (optional)")
    return parser.parse_args(argv)


def _model_label(backend_name: str) -> str:
    if backend_name == "claude":
        return os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
    return os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")


def main(argv=None) -> int:
    args = _parse_args(argv)
    # Data identity (Lift 1, ADR-048): the CLI is the operator's machine — everything
    # runs as the default user. NOTE: --user-id remains the ADK *session* label only;
    # it is NOT data identity and must never be parsed into the user context.
    with as_user(DEFAULT_USER_ID):
        return _dispatch(args)


def _dispatch(args) -> int:
    if getattr(args, "command", None) == "user":
        return _run_user(args)
    if getattr(args, "command", None) == "add":
        return _run_add(args)
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


def _run_add(args) -> int:
    """Deterministic single-title import — calls the validated MCP tool directly (no LLM,
    no recorder; the tool itself runs enrichment, which can take a minute or two)."""
    # Lazy import: the MCP server module pulls in the DB/scout stack, which the REPL
    # path loads via the backends instead.
    from agentic_librarian.mcp.server import add_book_to_history

    result = add_book_to_history(
        title=args.title,
        author=args.author,
        date_completed=args.date,
        rating=args.rating,
        format=args.format,
        notes=args.notes,
    )
    print(result)
    return 1 if result.startswith("Error") else 0


def _invite_db_manager():
    """Seam for tests; in production this points wherever DATABASE_URL points (the
    rollout runbook routes it through the Cloud SQL Auth Proxy for prod invites)."""
    from agentic_librarian.db.session import DatabaseManager

    return DatabaseManager()


def _run_user(args) -> int:
    if getattr(args, "user_command", None) != "invite":
        print("usage: librarian user invite <email> [--name NAME]", file=sys.stderr)
        return 2
    from agentic_librarian.db.models import User

    email = args.email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        print(f"error: {email!r} does not look like an email address", file=sys.stderr)
        return 2
    db = _invite_db_manager()
    with db.get_session() as session:
        existing = session.query(User).filter(User.email == email).first()
        if existing:
            status = "active" if existing.firebase_uid else "invited (never signed in)"
            print(f"{email} already exists — {status}.")
            return 0
        session.add(User(email=email, display_name=args.name))
        session.flush()
    print(f"Invited {email}. They can now sign in (claim-by-email links their account on first sign-in).")
    return 0


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
    turn_t0 = time.monotonic()  # on_event reads the enclosing scope's current value (closure cell)

    def on_event(kind: str, detail: str) -> None:
        entry = f"{time.monotonic() - turn_t0:.1f}s {kind}: {detail}"
        turn_events.append(entry)
        if not args.quiet:
            print(f"  · {entry}")

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
            turn_t0 = t0
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
