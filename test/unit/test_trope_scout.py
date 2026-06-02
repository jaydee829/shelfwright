from agentic_librarian.scouts.metadata_scout import LLMTropeScout


class _FakeLLM:
    def __init__(self, text: str):
        self._text = text

    def generate(self, prompt: str, grounded: bool = True) -> str:
        return self._text


def test_llm_trope_scout():
    payload = """
    {
        "tropes": [
            {
                "trope_name": "Found Family",
                "description": "A group of people who are not related by blood but form a deep familial bond.",
                "relevance_score": 0.9,
                "justification": "The crew of the Rocinante forms a tight-knit family unit throughout the series."
            }
        ]
    }
    """
    scout = LLMTropeScout(api_key="fake-key", llm=_FakeLLM(payload))
    res = scout.search("Leviathan Wakes", "James S.A. Corey")
    assert "tropes" in res
    assert len(res["tropes"]) == 1
    assert res["tropes"][0]["trope_name"] == "Found Family"
    assert res["tropes"][0]["relevance_score"] == 0.9
