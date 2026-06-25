from agentic_librarian.availability import directory

_FIXTURE = [
    {"slug": "spl", "name": "Seattle Public Library"},
    {"slug": "nsc", "name": "North Seattle College"},
    {"slug": "kcls", "name": "King County Library System"},
]


def test_search_prefix_ranked_before_contains(monkeypatch):
    monkeypatch.setattr(directory, "_directory", lambda: _FIXTURE)
    names = [r["name"] for r in directory.search("seattle")]
    assert names == ["Seattle Public Library", "North Seattle College"]  # prefix match first


def test_search_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(directory, "_directory", lambda: _FIXTURE)
    assert directory.search("KING")[0]["slug"] == "kcls"


def test_search_blank_returns_empty():
    assert directory.search("   ") == []


def test_search_respects_limit(monkeypatch):
    big = [{"slug": f"s{i}", "name": f"Library {i}"} for i in range(50)]
    monkeypatch.setattr(directory, "_directory", lambda: big)
    assert len(directory.search("library", limit=10)) == 10


def test_real_snapshot_loads_and_finds_a_known_library():
    # Guards the committed library_directory.json: it must be valid JSON and contain KCLS.
    out = directory.search("King County Library System")
    assert any(r["slug"] == "kcls" for r in out)
