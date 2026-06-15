from agentic_librarian.agents import candidates


def _patch(monkeypatch, internal, unacted, status):
    monkeypatch.setattr(candidates, "search_internal_database", lambda **kw: internal)
    monkeypatch.setattr(candidates, "get_unacted_suggestions", lambda **kw: unacted)
    monkeypatch.setattr(candidates, "get_read_status", lambda ids: status)


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
    _patch(monkeypatch, internal, [], status)

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
    _patch(monkeypatch, internal, [], status)

    out = candidates.curate_candidates(["x"], None)
    assert out["has_unread"] is False
    assert [c["id"] for c in out["candidates"]] == ["w1"]


def test_curate_falls_back_to_unacted_when_search_empty(monkeypatch):
    unacted = [{"id": "s1", "title": "Prior Pick", "justification": "you might like this"}]
    status = {"s1": {"status": "Unread", "last_read": None, "is_re_read_candidate": True, "rating": None}}
    _patch(monkeypatch, [], unacted, status)

    out = candidates.curate_candidates([], [])
    assert out["has_unread"] is True
    assert out["candidates"][0]["title"] == "Prior Pick"
    assert out["candidates"][0]["description"] == "you might like this"  # justification -> description
