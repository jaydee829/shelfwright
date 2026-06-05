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
