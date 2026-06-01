from agentic_librarian.agents.schemas import Discoveries, Targets


def test_targets_schema_validates():
    t = Targets(tropes=["heist"], styles=["fast paced"], session_constraints=["no gore"])
    assert t.tropes == ["heist"]


def test_discoveries_schema_validates():
    d = Discoveries(books=[{"title": "X", "author": "Y", "why": "fits"}])
    assert d.books[0].title == "X"


def test_analyst_has_output_schema_explorer_has_output_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-adk-key")
    from agentic_librarian.agents.services import create_agent_mesh

    mesh = create_agent_mesh()
    # The Analyst uses a function tool, so output_schema (function-calling) is allowed.
    assert mesh["analyst"].output_schema is Targets
    assert mesh["analyst"].output_key == "targets"
    # The Explorer's google_search is a BUILT-IN tool, which Gemini forbids combining with
    # function-calling — so it has NO output_schema and emits JSON-as-text via output_key.
    assert mesh["explorer"].output_schema is None
    assert mesh["explorer"].output_key == "discoveries"
