def test_pipeline_has_six_steps_in_order(monkeypatch):
    # Set the key BEFORE importing — ADK reads GOOGLE_API_KEY when the agents are constructed.
    # Keep this import inside the test (do not hoist to module top, or it breaks).
    monkeypatch.setenv("GOOGLE_API_KEY", "test-adk-key")
    from agentic_librarian.agents.pipeline import create_recommendation_pipeline

    pipeline = create_recommendation_pipeline()
    names = [a.name for a in pipeline.sub_agents]
    assert names == ["Analyst", "InternalCandidates", "Explorer", "Enrichment", "Critic", "Logger"]
