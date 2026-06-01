from unittest.mock import patch

from agentic_librarian.agents.backends.claude import ClaudeBackend


class _Result:
    def __init__(self, result=None, structured_output=None):
        self.result = result
        self.structured_output = structured_output


def _fake_query_factory(scripted):
    calls = {"i": 0}

    async def fake_query(prompt=None, options=None):
        msgs = scripted[calls["i"]]
        calls["i"] += 1
        for m in msgs:
            yield m

    return fake_query, calls


def test_claude_backend_sequences_pipeline_and_returns_recommendation():
    # Analyst -> targets ; Explorer -> discoveries (empty) ; Critic -> recommendation text.
    scripted = [
        [
            _Result(
                result='{"tropes": ["heist"], "styles": [], "session_constraints": []}',
                structured_output={"tropes": ["heist"], "styles": [], "session_constraints": []},
            )
        ],  # Analyst
        [_Result(result='{"books": []}', structured_output={"books": []})],  # Explorer (no discoveries)
        [_Result(result="I recommend The Long War because it features grimdark war.")],  # Critic
    ]
    fake_query, calls = _fake_query_factory(scripted)

    with (
        patch("agentic_librarian.agents.backends.claude.query", fake_query),
        patch("agentic_librarian.agents.backends.claude.extract_candidate_ids", return_value=["w1"]),
        patch("agentic_librarian.agents.backends.claude.log_suggestion") as mock_log,
    ):
        out = ClaudeBackend().run_recommendation("a heist book")

    assert "recommend" in out.lower()
    assert calls["i"] == 3  # Analyst, Explorer, Critic each queried once
    mock_log.assert_called_once()  # the top candidate was logged
