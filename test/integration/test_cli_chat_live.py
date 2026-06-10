"""Live 2-turn conversation smoke for the conversation seam (ADR-045). Run manually, once per
backend, when quota allows:
  AGENT_BACKEND=adk    pytest test/integration/test_cli_chat_live.py -m api_dependent -s
  AGENT_BACKEND=claude pytest test/integration/test_cli_chat_live.py -m api_dependent -s
(inside the app container — the claude backend additionally needs the authed `claude` CLI)."""

import pytest

from agentic_librarian.agents.backends import get_backend


@pytest.mark.api_dependent
def test_two_turn_conversation_live():
    events = []
    backend = get_backend()
    conv = backend.start_conversation(on_event=lambda kind, detail: events.append((kind, detail)))
    try:
        first = conv.send("Recommend one fantasy book from my catalog with found-family vibes.")
        second = conv.send("Why did you pick that one over other options?")
    finally:
        conv.close()
    print(f"\n[{backend.name}] events: {events}\nfirst: {first}\nsecond: {second}")
    assert first.strip() and first != "(no response)"
    assert second.strip() and second != "(no response)"
    assert events, "expected at least one tool/agent event in a real conversation"
