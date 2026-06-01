from agentic_librarian.agents.schemas import Discoveries, Targets


def test_targets_schema_validates():
    t = Targets(tropes=["heist"], styles=["fast paced"], session_constraints=["no gore"])
    assert t.tropes == ["heist"]


def test_discoveries_schema_validates():
    d = Discoveries(books=[{"title": "X", "author": "Y", "why": "fits"}])
    assert d.books[0].title == "X"


def test_analyst_and_explorer_have_output_schema(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-adk-key")
    from agentic_librarian.agents.services import create_agent_mesh

    mesh = create_agent_mesh()
    assert mesh["analyst"].output_schema is Targets
    assert mesh["analyst"].output_key == "targets"
    assert mesh["explorer"].output_schema is Discoveries
    assert mesh["explorer"].output_key == "discoveries"
