from agentic_librarian.agents.services import create_agent_mesh


def test_agent_mesh_delegation_structure():
    """Verify that the Librarian has the correct specialist tools assigned."""
    mesh = create_agent_mesh()
    librarian = mesh["librarian"]

    # Extract tool names from the librarian
    tool_names = [t.name for t in librarian.tools]

    assert "Analyst" in tool_names
    assert "Explorer" in tool_names
    assert "Critic" in tool_names
    assert "get_unacted_suggestions" in tool_names


def test_analyst_tool_assignment():
    """Verify the Analyst has its specific data tools."""
    mesh = create_agent_mesh()
    analyst = mesh["analyst"]

    tool_names = [t.name for t in analyst.tools]
    assert "get_user_trope_preferences" in tool_names


def test_critic_tool_assignment():
    """Verify the Critic has its specific evaluation tools."""
    mesh = create_agent_mesh()
    critic = mesh["critic"]

    tool_names = [t.name for t in critic.tools]
    assert "search_internal_database" in tool_names
    assert "check_reading_history" in tool_names


def test_librarian_has_the_enrich_tool():
    mesh = create_agent_mesh()
    tool_names = [t.name for t in mesh["librarian"].tools]
    assert "enrich_and_persist_work" in tool_names


def test_librarian_instruction_is_internal_first_and_series_aware():
    mesh = create_agent_mesh()
    text = mesh["librarian"].instruction
    assert "ONLY when" in text
    assert "enrich_and_persist_work" in text
    assert "SERIES" in text


def test_adk_librarian_carries_trust_boundary_and_confirm():
    mesh = create_agent_mesh()
    text = mesh["librarian"].instruction
    assert "TRUST BOUNDARY" in text
    assert "CONFIRM HISTORY WRITES" in text


def test_adk_librarian_has_the_import_tool_and_flow():
    mesh = create_agent_mesh()
    assert "add_book_to_history" in [t.name for t in mesh["librarian"].tools]
    text = mesh["librarian"].instruction
    assert "add_book_to_history" in text
    confirm = text[text.index("CONFIRM HISTORY WRITES") :]
    assert "add_book_to_history" in confirm


def test_librarian_can_check_reading_history():
    # D1a: the ADK Librarian orchestrator must be able to check history before importing —
    # without this tool its "add only if not already in history" instruction is unfollowable,
    # which is how it logged a duplicate read of an already-owned book.
    mesh = create_agent_mesh()
    assert "check_reading_history" in [t.name for t in mesh["librarian"].tools]


def test_adk_librarian_checks_history_before_import():
    mesh = create_agent_mesh()
    text = mesh["librarian"].instruction
    import_clause = text[text.index("IMPORT") :]
    assert "check_reading_history" in import_clause


def test_adk_librarian_defaults_to_three_recommendations():
    # A2: count pinned in the orchestrator instruction so Gemini stops returning a single pick.
    mesh = create_agent_mesh()
    assert "3 recommendations by default" in mesh["librarian"].instruction
