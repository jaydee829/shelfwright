import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import auth
from agentic_librarian.api import main as api_main
from agentic_librarian.chat import transcript
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    monkeypatch.setattr(transcript, "db_manager", manager)  # chat endpoints use the store's manager
    # Endpoints wrap store calls in as_user(user.id), so a plain user object suffices.
    api_main.app.dependency_overrides[auth.get_current_user] = lambda: auth.AuthenticatedUser(
        id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL
    )

    class _FakeConv:
        async def asend(self, message):
            return f"echo:{message}"

        def close(self):
            ...

    async def _fake_open(**kwargs):
        return _FakeConv()

    monkeypatch.setattr(api_main, "_open_conversation", _fake_open)
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


def test_current_conversation_then_chat_then_resume(client):
    current = client.get("/conversations/current").json()
    assert current["messages"] == []
    cid = current["id"]

    with client.stream("POST", "/chat", json={"message": "hi"}) as r:
        body = "".join(r.iter_text())
    assert "echo:hi" in body
    assert body.rstrip().endswith("event: done\ndata: {}")

    resumed = client.get("/conversations/current").json()
    assert resumed["id"] == cid
    assert resumed["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "echo:hi"},
    ]


def test_new_conversation_starts_empty(client):
    client.get("/conversations/current")
    fresh = client.post("/conversations").json()
    assert fresh["messages"] == []
