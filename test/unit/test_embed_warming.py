"""#123: embedding texts are collected from the scout row and warmed BEFORE any session."""

from unittest.mock import MagicMock, patch

from agentic_librarian.etl.persist import collect_embedding_texts


def test_collects_trope_and_style_texts():
    row = {
        "enriched_tropes": [{"trope_name": "Found Family"}, {"trope_name": "Slow Burn"}],
        "author_style": {"pacing": "leisurely"},
        "work_style": {"tone": "wry"},
        "narrator_styles": {"Sam": {"accent": "Irish"}},
        "genres": ["Fantasy"],
        "moods": ["Cozy"],
    }
    texts = collect_embedding_texts(row)
    assert {"Found Family", "Slow Burn", "leisurely", "wry", "Irish"} <= set(texts)
    assert "Fantasy" not in texts  # real tropes present -> no fallback tags


def test_collects_fallback_tags_when_no_real_tropes():
    row = {
        "enriched_tropes": [],
        "genres": ["Fantasy"],
        "moods": ["Cozy"],
        "author_style": {},
        "work_style": {},
        "narrator_styles": {},
    }
    texts = collect_embedding_texts(row)
    assert set(texts) & {"Fantasy", "Cozy"}  # cleaned fallback tags included


def test_no_fallback_tags_when_write_fallback_tropes_false():
    """The two-phase fast pass opts out of fallback tropes (#65) — warming must not embed
    genre/mood tags it will never persist as tropes."""
    row = {
        "enriched_tropes": [],
        "genres": ["Fantasy"],
        "moods": ["Cozy"],
        "author_style": {},
        "work_style": {},
        "narrator_styles": {},
        "write_fallback_tropes": False,
    }
    texts = collect_embedding_texts(row)
    assert texts == []


def test_persist_row_warms_before_session(monkeypatch):
    from agentic_librarian.enrichment import two_phase

    session_state = {"open": 0}
    warmed_during = []

    class FakeSession:
        def __enter__(self):
            session_state["open"] += 1
            m = MagicMock()
            # dedup query chain returns None (no existing work) so enrich_fast proceeds to scouts
            m.query.return_value.join.return_value.join.return_value.filter.return_value.filter.return_value.first.return_value = None
            return m

        def __exit__(self, *a):
            session_state["open"] -= 1
            return False

    fake_manager = MagicMock()
    fake_manager.get_session = lambda: FakeSession()
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)

    row = {
        "Title": "New Book",
        "Author_1": "New Author",
        "format": "ebook",
        "enriched_tropes": [{"trope_name": "Found Family"}],
        "author_style": {},
        "work_style": {},
        "narrator_styles": {},
        "genres": [],
        "moods": [],
        "write_fallback_tropes": False,
    }

    def fake_run_scouts(manager, **kwargs):
        return row

    def fake_embed(model, text):
        warmed_during.append(session_state["open"])
        return [0.0]

    monkeypatch.setattr(two_phase, "_run_scouts", fake_run_scouts)
    monkeypatch.setattr(two_phase, "get_cached_embedding", fake_embed)
    monkeypatch.setattr(two_phase, "persist_enriched_work", lambda *a, **k: MagicMock())

    with patch.object(two_phase, "create_fast_scout_manager", return_value=MagicMock()):
        two_phase.enrich_fast("New Book", "New Author")

    assert warmed_during and all(n == 0 for n in warmed_during)
