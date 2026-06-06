"""SEC-002 structural invariant: the write tools exist ONLY on the Librarian — the single
write-authorization point — on BOTH backends. If a future change hands a subagent a write
tool, this fails loudly."""

import pytest

WRITE_TOOLS = {
    "log_suggestion",
    "update_reading_status",
    "update_suggestion_status",
    "enrich_and_persist_work",
    "add_book_to_history",
}


def test_claude_subagents_have_no_write_tools():
    pytest.importorskip("claude_agent_sdk")
    from agentic_librarian.agents.backends.claude import _conversation_options

    options = _conversation_options()
    for name, agent in options.agents.items():
        granted = {t.split("__")[-1] for t in (agent.tools or [])}
        assert not (granted & WRITE_TOOLS), f"subagent {name!r} was granted write tools: {granted & WRITE_TOOLS}"
    # And the Librarian session DOES hold them (it is the authorization point):
    session_tools = {t.split("__")[-1] for t in options.allowed_tools}
    assert session_tools >= WRITE_TOOLS


def test_adk_specialists_have_no_write_tools():
    from agentic_librarian.agents.services import create_agent_mesh

    mesh = create_agent_mesh()
    for name in ("analyst", "explorer", "critic"):
        granted = {t.name for t in mesh[name].tools} & WRITE_TOOLS
        assert not granted, f"ADK {name} was granted write tools: {granted}"
    librarian_tools = {t.name for t in mesh["librarian"].tools}
    assert librarian_tools >= WRITE_TOOLS
