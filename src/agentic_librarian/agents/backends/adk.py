"""ADK/Gemini recommendation backend — wraps the existing SequentialAgent pipeline."""

from __future__ import annotations

import asyncio
import uuid

from agentic_librarian.agents.pipeline import create_recommendation_pipeline
from agentic_librarian.agents.runtime import APP_NAME, _ensure_adk_credentials
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types


class ADKBackend:
    name = "adk"

    def build_runner(self) -> Runner:
        _ensure_adk_credentials()
        return Runner(
            agent=create_recommendation_pipeline(),
            app_name=APP_NAME,
            session_service=InMemorySessionService(),
        )

    async def arun(self, prompt: str, user_id: str = "local") -> str:
        runner = self.build_runner()
        session_id = uuid.uuid4().hex
        await runner.session_service.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
        content = types.Content(role="user", parts=[types.Part(text=prompt)])
        async for _ in runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
            pass
        session = await runner.session_service.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
        return session.state.get("recommendation") or "(no recommendation)"

    def run_recommendation(self, prompt: str, user_id: str = "local") -> str:
        return asyncio.run(self.arun(prompt, user_id))
