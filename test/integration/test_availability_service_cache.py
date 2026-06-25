import pytest

from agentic_librarian.availability import overdrive, service
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration

_ITEMS = [
    {
        "title": "Dune",
        "type": {"id": "ebook", "name": "eBook"},
        "isAvailable": True,
        "firstCreatorName": "Frank Herbert",
    }
]


def test_cache_miss_then_hit(db_url, monkeypatch):
    calls = {"n": 0}

    def fake_fetch(slug, title):
        calls["n"] += 1
        return _ITEMS

    monkeypatch.setattr(overdrive, "fetch_media", fake_fetch)
    lib = {"slug": "kcls", "name": "KCLS"}

    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        first = service.availability_for(session, lib, "Dune", "Frank Herbert")
        assert first[0]["format"] == "eBook"
        second = service.availability_for(session, lib, "Dune", "Frank Herbert")
        assert second[0]["available"] is True
        assert calls["n"] == 1  # second call served from cache


def test_thunder_error_degrades_to_none(db_url, monkeypatch):
    def boom(slug, title):
        raise overdrive.ThunderError("down")

    monkeypatch.setattr(overdrive, "fetch_media", boom)

    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        out = service.availability_for(session, {"slug": "kcls", "name": "KCLS"}, "X", "Y")
        assert out is None
