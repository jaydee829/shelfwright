import pytest

pytest.importorskip("claude_agent_sdk")  # the `claude` optional extra; skip if not installed


def test_build_librarian_mcp_server_exposes_tools():
    from agentic_librarian.agents.backends.claude_tools import LIBRARIAN_TOOL_NAMES, build_librarian_mcp_server

    server = build_librarian_mcp_server()
    assert server is not None
    for short in ("search_internal_database", "get_work_details", "get_user_trope_preferences", "log_suggestion"):
        assert f"mcp__librarian__{short}" in LIBRARIAN_TOOL_NAMES


def test_feedback_tools_are_exposed():
    # The conversational Librarian (ADR-045) needs the feedback tools the ADK Librarian has.
    from agentic_librarian.agents.backends.claude_tools import LIBRARIAN_TOOL_NAMES

    assert "mcp__librarian__update_reading_status" in LIBRARIAN_TOOL_NAMES
    assert "mcp__librarian__update_suggestion_status" in LIBRARIAN_TOOL_NAMES
