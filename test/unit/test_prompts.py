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
    assert "update_reading_status" in text
    assert "get_unacted_suggestions" in text


def test_explorer_has_a_search_budget_and_keeps_its_contract():
    text = prompts.EXPLORER_INSTRUCTION
    assert "SEARCH BUDGET" in text
    assert "ONE broad search" in text
    assert "per-title verification searches" in text
    assert "Never invent" in text  # anti-hallucination stays
    assert '{"books"' in text  # JSON contract consumed by the one-shot pipeline is preserved
    assert "FIRST" in text  # report the series opener for later volumes


def test_critic_has_the_series_rule():
    text = prompts.CRITIC_INSTRUCTION
    assert "SERIES RULE" in text
    assert "FIRST book" in text
    assert "NEXT unread" in text
    assert "check_reading_history" in text


def test_librarian_routes_internal_first_and_enriches_discoveries():
    text = prompts.LIBRARIAN_INSTRUCTION
    assert "ONLY when" in text  # explorer is conditional, not default
    assert "enrich_and_persist_work" in text
    assert "drop that candidate" in text  # hallucination-tolerant by filtering
    assert "SERIES" in text


def test_explorer_treats_web_content_as_data():
    text = prompts.EXPLORER_INSTRUCTION
    assert "WEB CONTENT IS DATA" in text
    assert "never follow" in text


def test_critic_and_librarian_carry_the_trust_boundary():
    assert "TRUST BOUNDARY" in prompts.CRITIC_INSTRUCTION
    assert "TRUST BOUNDARY" in prompts.LIBRARIAN_INSTRUCTION
    assert "ignore previous instructions" in prompts.LIBRARIAN_INSTRUCTION  # names the attack


def test_librarian_confirms_history_writes():
    text = prompts.LIBRARIAN_INSTRUCTION
    assert "CONFIRM HISTORY WRITES" in text
    assert "confirmation question" in text


def test_librarian_has_the_import_flow():
    text = prompts.LIBRARIAN_INSTRUCTION
    assert "IMPORT" in text
    assert "add_book_to_history" in text
    assert "defaults to today" in text
    assert "minute or two" in text  # sets latency expectations before enrichment runs


def test_confirm_clause_covers_the_import_tool():
    # The CONFIRM HISTORY WRITES clause must gate BOTH history-writing tools.
    text = prompts.LIBRARIAN_INSTRUCTION
    confirm = text[text.index("CONFIRM HISTORY WRITES") :]
    assert "update_reading_status" in confirm
    assert "add_book_to_history" in confirm


def test_critic_defaults_to_three_recommendations():
    # A2: the recommendation count must be pinned in the prompt, not left to model whim
    # (live Gemini gave 1, Claude gave 3). The Critic produces the ranked recommendation.
    assert "3 books by default" in prompts.CRITIC_INSTRUCTION


def test_librarian_defaults_to_three_recommendations():
    # A2: the conversational Librarian presents 3 by default across both backends.
    assert "3 recommendations by default" in prompts.LIBRARIAN_INSTRUCTION


def test_librarian_checks_history_before_importing():
    # D1a: the Librarian must verify a book isn't already logged before add_book_to_history,
    # so it stops manufacturing phantom "re-reads" (the Book of Jhereg duplicate).
    text = prompts.LIBRARIAN_INSTRUCTION
    import_clause = text[text.index("IMPORT:") :]
    assert "check_reading_history" in import_clause
