from agentic_librarian.agents import runtime


class _FakeSessionService:
    async def create_session(self, app_name, user_id, session_id):
        return None

    async def get_session(self, app_name, user_id, session_id):
        class _S:
            state = {"recommendation": "Recommended: The Long War."}

        return _S()


class _FakeRunner:
    def __init__(self):
        self.app_name = runtime.APP_NAME
        self.session_service = _FakeSessionService()

    async def run_async(self, user_id, session_id, new_message):
        if False:
            yield  # empty async generator (the pipeline "ran")


def test_run_recommendation_returns_state_recommendation(monkeypatch):
    # run_recommendation must build the pipeline runner, run it, and return state['recommendation'].
    monkeypatch.setattr(runtime, "build_pipeline_runner", lambda: _FakeRunner())
    assert runtime.run_recommendation("grim") == "Recommended: The Long War."
