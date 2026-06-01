from agentic_librarian.agents import runtime


class _FakeBackend:
    name = "fake"

    def run_recommendation(self, prompt, user_id="local"):
        return "Recommended: The Long War."


def test_run_recommendation_returns_state_recommendation(monkeypatch):
    # run_recommendation must delegate to the configured backend and return its result.
    monkeypatch.setattr(runtime, "get_backend", lambda: _FakeBackend())
    assert runtime.run_recommendation("grim") == "Recommended: The Long War."
