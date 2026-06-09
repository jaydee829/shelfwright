import asyncio

from agentic_librarian.agents import runtime


class _FakeSessionService:
    def __init__(self):
        self.created = []
        self.appended = []

    async def create_session(self, app_name, user_id, session_id):
        self.created.append(session_id)
        return object()

    async def get_session(self, app_name, user_id, session_id):
        return object()

    async def append_event(self, session, event):
        self.appended.append(event)
        return event


class _FakeRunner:
    def __init__(self):
        self.session_service = _FakeSessionService()


def test_history_is_seeded_as_events_in_order():
    runner = _FakeRunner()
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    conv = asyncio.run(
        runtime.astart_conversation(user_id="u", runner=runner, session_id="abc123", history=history)
    )
    assert conv.session_id == "abc123"
    assert runner.session_service.created == ["abc123"]
    assert len(runner.session_service.appended) == 2
    roles = [e.content.role for e in runner.session_service.appended]
    assert roles == ["user", "model"]
    texts = [e.content.parts[0].text for e in runner.session_service.appended]
    assert texts == ["hello", "hi there"]


def test_no_history_seeds_no_events():
    runner = _FakeRunner()
    asyncio.run(runtime.astart_conversation(user_id="u", runner=runner, session_id="abc", history=None))
    assert runner.session_service.appended == []
