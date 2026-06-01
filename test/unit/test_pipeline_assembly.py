def test_pipeline_has_six_steps_in_order(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-adk-key")
    from agentic_librarian.agents.pipeline import create_recommendation_pipeline

    pipeline = create_recommendation_pipeline()
    names = [a.name for a in pipeline.sub_agents]
    assert names == ["Analyst", "InternalCandidates", "Explorer", "Enrichment", "Critic", "Logger"]
