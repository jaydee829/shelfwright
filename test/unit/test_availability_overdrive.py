import pytest

from agentic_librarian.availability import overdrive
from agentic_librarian.availability.overdrive import ThunderError


def test_search_libraries_maps_items(monkeypatch):
    monkeypatch.setattr(
        overdrive,
        "_http_get_json",
        lambda url: {
            "items": [
                {"preferredKey": "kcls", "name": "King County Library System"},
                {"preferredKey": "spl", "name": "Seattle Public Library"},
            ],
        },
    )
    out = overdrive.search_libraries("seattle")
    assert out == [
        {"slug": "kcls", "name": "King County Library System"},
        {"slug": "spl", "name": "Seattle Public Library"},
    ]


def test_fetch_media_returns_items(monkeypatch):
    monkeypatch.setattr(overdrive, "_http_get_json", lambda url: {"items": [{"title": "Dune"}]})
    assert overdrive.fetch_media("kcls", "Dune") == [{"title": "Dune"}]


def test_http_failure_raises_thundererror(monkeypatch):
    def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(overdrive, "_http_get_json", boom)
    with pytest.raises(ThunderError):
        overdrive.search_libraries("x")
    with pytest.raises(ThunderError):
        overdrive.fetch_media("kcls", "Dune")


def test_unexpected_body_shape_raises_thundererror(monkeypatch):
    # Thunder returns a 200 with a non-dict body (list/None) → must still be ThunderError,
    # not a leaked AttributeError.
    monkeypatch.setattr(overdrive, "_http_get_json", lambda url: [])
    with pytest.raises(ThunderError):
        overdrive.search_libraries("x")
    monkeypatch.setattr(overdrive, "_http_get_json", lambda url: None)
    with pytest.raises(ThunderError):
        overdrive.fetch_media("kcls", "Dune")
