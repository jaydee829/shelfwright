import pytest

from agentic_librarian.agents import candidates


def _patch(monkeypatch, internal, status, suggested=frozenset()):
    monkeypatch.setattr(candidates, "search_internal_database", lambda **kw: internal)
    monkeypatch.setattr(candidates, "get_read_status", lambda ids: status)
    # raising=False: the helper doesn't exist yet on the first RED run (TDD).
    monkeypatch.setattr(candidates, "get_active_suggestion_work_ids", lambda: set(suggested), raising=False)


def test_curate_orders_unread_first_and_drops_recent_reads(monkeypatch):
    internal = [
        {"id": "w-old", "title": "Old Read", "authors": ["A"], "genres": ["sf"], "description": "d1"},
        {"id": "w-recent", "title": "Recent Read", "authors": ["B"], "genres": [], "description": "d2"},
        {"id": "w-new", "title": "Fresh", "authors": ["C"], "genres": [], "description": "d3"},
    ]
    status = {
        "w-old": {"status": "Read", "last_read": "2019-01-01", "is_re_read_candidate": True, "rating": 5},
        "w-recent": {"status": "Read", "last_read": "2025-12-01", "is_re_read_candidate": False, "rating": None},
        "w-new": {"status": "Unread", "last_read": None, "is_re_read_candidate": True, "rating": None},
    }
    _patch(monkeypatch, internal, status)

    out = candidates.curate_candidates(["cozy"], ["lyrical"])

    ids = [c["id"] for c in out["candidates"]]
    assert ids == ["w-new", "w-old"]  # unread first; recent read dropped
    assert out["has_unread"] is True
    assert out["unread_count"] == 1 and out["reread_count"] == 1
    new_card = out["candidates"][0]
    assert new_card["read_status"] == "new" and new_card["last_read"] is None
    old_card = out["candidates"][1]
    assert old_card["read_status"] == "reread" and old_card["last_read"] == "2019-01-01" and old_card["rating"] == 5


def test_curate_reports_no_unread_when_all_reads_eligible(monkeypatch):
    internal = [{"id": "w1", "title": "T", "authors": [], "genres": [], "description": ""}]
    status = {"w1": {"status": "Read", "last_read": "2018-01-01", "is_re_read_candidate": True, "rating": None}}
    _patch(monkeypatch, internal, status)

    out = candidates.curate_candidates(["x"], None)
    assert out["has_unread"] is False
    assert [c["id"] for c in out["candidates"]] == ["w1"]


def test_curate_excludes_actively_suggested_works(monkeypatch):
    """Fresh candidate sets must not re-offer works the Librarian already suggested and the
    user never acted on (#125: Starsight was re-presented after the user deflected it)."""
    internal = [
        {"id": "w-pitched", "title": "Already Pitched", "authors": [], "genres": [], "description": ""},
        {"id": "w-new", "title": "Fresh", "authors": [], "genres": [], "description": ""},
    ]
    status = {
        "w-pitched": {"status": "Unread", "last_read": None, "is_re_read_candidate": True, "rating": None},
        "w-new": {"status": "Unread", "last_read": None, "is_re_read_candidate": True, "rating": None},
    }
    _patch(monkeypatch, internal, status, suggested={"w-pitched"})

    out = candidates.curate_candidates(["x"])
    assert [c["id"] for c in out["candidates"]] == ["w-new"]


def test_curate_does_not_reinject_prior_suggestions(monkeypatch):
    """curate no longer unions get_unacted_suggestions into fresh candidate sets (#125:
    the union floated deflected suggestions back to the top on every new request)."""
    called = []
    monkeypatch.setattr(
        candidates,
        "search_internal_database",
        lambda **kw: [{"id": "w1", "title": "T", "authors": [], "genres": [], "description": ""}],
    )
    monkeypatch.setattr(
        candidates,
        "get_unacted_suggestions",
        lambda **kw: called.append(kw) or [{"id": "s1", "title": "Prior Pick", "justification": "j"}],
        raising=False,
    )
    monkeypatch.setattr(
        candidates,
        "get_read_status",
        lambda ids: {
            i: {"status": "Unread", "last_read": None, "is_re_read_candidate": True, "rating": None} for i in ids
        },
    )
    monkeypatch.setattr(candidates, "get_active_suggestion_work_ids", lambda: set())

    out = candidates.curate_candidates(["x"])
    assert [c["id"] for c in out["candidates"]] == ["w1"]
    assert called == []  # never consulted for fresh candidates


def test_curate_returns_empty_when_search_finds_nothing(monkeypatch):
    """With the unacted union removed, an empty search means an empty candidate set —
    has_unread False signals the caller to delegate to the Explorer."""
    _patch(monkeypatch, [], {})
    out = candidates.curate_candidates(["x"], [])
    assert out == {"candidates": [], "has_unread": False, "unread_count": 0, "reread_count": 0}


@pytest.mark.parametrize(
    ("exclude_tropes", "exclude_styles"),
    [
        (["gore"], None),
        (None, ["grimdark prose"]),
        (["gore", "body horror"], ["grimdark prose"]),
    ],
)
def test_curate_forwards_exclusions_to_search(monkeypatch, exclude_tropes, exclude_styles):
    seen = {}

    def fake_search(**kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(candidates, "search_internal_database", fake_search)
    monkeypatch.setattr(candidates, "get_read_status", lambda ids: {})
    monkeypatch.setattr(candidates, "get_active_suggestion_work_ids", lambda: set())

    candidates.curate_candidates(["x"], None, exclude_tropes=exclude_tropes, exclude_styles=exclude_styles)
    assert seen.get("exclude_tropes") == exclude_tropes
    assert seen.get("exclude_styles") == exclude_styles


def test_extract_candidate_ids_passes_session_constraints_as_exclusions(monkeypatch):
    """The Analyst's session_constraints ('less fantasy') must structurally reach retrieval
    as negative targets, not just live in the prompt (#125)."""
    seen = {}

    def fake_search(**kw):
        seen.update(kw)
        return [{"id": "w1"}, {"id": "w2"}]

    monkeypatch.setattr(candidates, "search_internal_database", fake_search)
    monkeypatch.setattr(candidates, "get_active_suggestion_work_ids", lambda: set())

    state = {"targets": {"tropes": ["heist"], "styles": ["witty"], "session_constraints": ["high fantasy", "gore"]}}
    ids = candidates.extract_candidate_ids(state)

    assert ids == ["w1", "w2"]
    assert seen.get("target_tropes") == ["heist"]
    assert seen.get("target_styles") == ["witty"]
    assert seen.get("exclude_tropes") == ["high fantasy", "gore"]


def test_extract_candidate_ids_excludes_actively_suggested(monkeypatch):
    monkeypatch.setattr(candidates, "search_internal_database", lambda **kw: [{"id": "w-pitched"}, {"id": "w-new"}])
    monkeypatch.setattr(candidates, "get_active_suggestion_work_ids", lambda: {"w-pitched"})

    state = {"targets": {"tropes": ["heist"], "styles": [], "session_constraints": []}}
    assert candidates.extract_candidate_ids(state) == ["w-new"]


def test_extract_candidate_ids_does_not_reinject_prior_suggestions(monkeypatch):
    called = []
    monkeypatch.setattr(candidates, "search_internal_database", lambda **kw: [{"id": "w1"}])
    monkeypatch.setattr(
        candidates,
        "get_unacted_suggestions",
        lambda **kw: called.append(kw) or [{"id": "s1"}],
        raising=False,
    )
    monkeypatch.setattr(candidates, "get_active_suggestion_work_ids", lambda: set())

    state = {"targets": {"tropes": ["heist"], "styles": [], "session_constraints": []}}
    assert candidates.extract_candidate_ids(state) == ["w1"]
    assert called == []
