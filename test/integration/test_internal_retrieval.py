import json
from pathlib import Path
from unittest.mock import patch

import pytest
from agentic_librarian.db.models import Author, Trope, Work, WorkContributor, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp.server import search_internal_database, set_db_manager

FIXTURE = json.loads(Path("test/data/trope_embeddings.json").read_text())
ROMANCE = ["enemies to lovers", "slow burn romance"]
GRIMDARK = ["grimdark war", "brutal military strategy"]


def _seed_work(session, title, author_name, trope_names):
    author = Author(name=author_name)
    session.add(author)
    session.flush()
    work = Work(title=title)
    session.add(work)
    session.flush()
    session.add(WorkContributor(work=work, author=author, role="Author"))
    for name in trope_names:
        trope = Trope(name=name, embedding=FIXTURE[name])
        session.add(trope)
        session.flush()
        session.add(WorkTrope(work=work, trope=trope))
    return work


@pytest.mark.db_integration
def test_search_ranks_semantically_near_work_first(db_url, monkeypatch):
    # Managers construct a genai.Client in __init__ (needs a key; no network call).
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)

    with test_db_manager.get_session() as session:
        _seed_work(session, "A Courtship", "Romance Author", ROMANCE)
        _seed_work(session, "The Long War", "Grimdark Author", GRIMDARK)
        session.commit()

    # The query-side embedding resolves a known string to the same cached real vector,
    # so cosine distances are deterministic.
    def fake_embedding(self, text):
        return FIXTURE[text]

    with patch("agentic_librarian.mcp.server.TropeManager._get_embedding", fake_embedding):
        results = search_internal_database(target_tropes=["enemies to lovers"])

    titles = [r["title"] for r in results]
    assert titles[:2] == ["A Courtship", "The Long War"], titles
