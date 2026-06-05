from agentic_librarian.agents import prompts
from agentic_librarian.agents.prompts import CRITIC_INSTRUCTION


def test_critic_commits_to_a_one_shot_recommendation():
    text = CRITIC_INSTRUCTION.lower()
    assert "best-effort" in text
    assert "never" in text  # never ask a clarifying question / never return empty


def test_prompts_are_nonempty_strings():
    for name in ("ANALYST_INSTRUCTION", "EXPLORER_INSTRUCTION", "CRITIC_INSTRUCTION"):
        value = getattr(prompts, name)
        assert isinstance(value, str) and len(value.strip()) > 50


def test_services_use_shared_prompts(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    from agentic_librarian.agents.services import create_agent_mesh

    mesh = create_agent_mesh()
    assert mesh["analyst"].instruction == prompts.ANALYST_INSTRUCTION
    assert mesh["explorer"].instruction == prompts.EXPLORER_INSTRUCTION
    assert mesh["critic"].instruction == prompts.CRITIC_INSTRUCTION


def test_librarian_instruction_delegates_to_the_mesh():
    # The Claude conversational Librarian delegates to the SAME specialist mesh as ADK
    # (SDK subagents named analyst/explorer/critic) and keeps the feedback/logging tools direct.
    text = prompts.LIBRARIAN_INSTRUCTION
    assert "'analyst'" in text
    assert "'explorer'" in text
    assert "'critic'" in text
    assert "log_suggestion" in text
    assert "update_suggestion_status" in text
