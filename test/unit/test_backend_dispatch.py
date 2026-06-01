from agentic_librarian.agents import runtime


class _FakeBackend:
    name = "fake"

    def run_recommendation(self, prompt, user_id="local"):
        return f"FAKE[{prompt}]"


def test_run_recommendation_delegates_to_backend(monkeypatch):
    monkeypatch.setattr(runtime, "get_backend", lambda: _FakeBackend())
    assert runtime.run_recommendation("grim") == "FAKE[grim]"
