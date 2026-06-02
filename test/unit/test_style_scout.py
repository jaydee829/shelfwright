from unittest.mock import patch

from agentic_librarian.scouts.metadata_scout import StyleScout


class _FakeLLM:
    """A GroundedLLM stub returning a fixed JSON string for every call."""

    def __init__(self, text: str):
        self._text = text

    def generate(self, prompt: str, grounded: bool = True) -> str:
        return self._text


def test_scout_author_style():
    scout = StyleScout(api_key="fake-key", llm=_FakeLLM('{"pacing": "fast", "tone": "cynical", "style": "minimalist"}'))
    style = scout.scout_author_style("Ernest Hemingway")
    assert style["pacing"] == "fast"
    assert style["tone"] == "cynical"
    assert style["style"] == "minimalist"


def test_scout_narrator_style():
    scout = StyleScout(
        api_key="fake-key",
        llm=_FakeLLM('{"pacing": "steady", "voice_differentiation": "excellent", "emotional_range": "wide"}'),
    )
    style = scout.scout_narrator_style("Jefferson Mays")
    assert style["pacing"] == "steady"
    assert style["voice_differentiation"] == "excellent"
    assert style["emotional_range"] == "wide"


def test_style_scout_search_mode():
    scout = StyleScout(api_key="fake-key", llm=_FakeLLM('{"pacing": "fast"}'))
    res = scout.search("The Expanse", "James S.A. Corey", narrators=["Jefferson Mays"])
    assert "author_style" in res
    assert "narrator_styles" in res
    assert "Jefferson Mays" in res["narrator_styles"]
    assert res["author_style"]["pacing"] == "fast"


def test_work_style_baseline_falls_back_to_scouted_author_style():
    scout = StyleScout(api_key="fake-key", llm=_FakeLLM("{}"))
    with (
        patch.object(scout, "scout_author_style", return_value={"pacing": "fast"}),
        patch.object(scout, "scout_work_style", return_value={}) as m_work,
        patch.object(scout, "scout_narrator_style", return_value={}),
    ):
        scout.search("Book", "Author")
    assert m_work.call_args.kwargs["author_baseline"] == {"pacing": "fast"}


def test_work_style_baseline_prefers_db_baseline_when_provided():
    scout = StyleScout(api_key="fake-key", llm=_FakeLLM("{}"))
    with (
        patch.object(scout, "scout_author_style", return_value={"pacing": "fast"}),
        patch.object(scout, "scout_work_style", return_value={}) as m_work,
    ):
        scout.search("Book", "Author", author_styles={"tone": "dark"})
    assert m_work.call_args.kwargs["author_baseline"] == {"tone": "dark"}
