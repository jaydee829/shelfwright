from unittest.mock import patch

from agentic_librarian.agents.pipeline import (
    coerce_schema_value,
    extract_candidate_ids,
    extract_discovery_pairs,
)


def test_coerce_schema_value_handles_dict_and_json_and_model():
    assert coerce_schema_value({"tropes": ["a"]})["tropes"] == ["a"]
    assert coerce_schema_value('{"tropes": ["a"]}')["tropes"] == ["a"]
    # LLMs often wrap JSON in a markdown code fence — it must still parse.
    assert coerce_schema_value('```json\n{"tropes": ["a"]}\n```')["tropes"] == ["a"]
    assert coerce_schema_value(None) == {}
    # A valid JSON string that decodes to a non-dict must coerce to {} (callers do .get()).
    assert coerce_schema_value("[1, 2]") == {}
    assert coerce_schema_value("not json") == {}


def test_extract_discovery_pairs_skips_books_missing_title_or_author():
    state = {"discoveries": {"books": [{"title": "X"}, {"title": "Y", "author": "Z"}]}}
    assert extract_discovery_pairs(state) == [("Y", "Z")]


def test_extract_discovery_pairs_reads_books_from_dict_and_json():
    state_dict = {"discoveries": {"books": [{"title": "X", "author": "Y", "why": "z"}]}}
    assert extract_discovery_pairs(state_dict) == [("X", "Y")]
    # The Explorer emits JSON-as-text (no output_schema), so the value may be a JSON string:
    state_json = {"discoveries": '{"books": [{"title": "X", "author": "Y", "why": "z"}]}'}
    assert extract_discovery_pairs(state_json) == [("X", "Y")]


def test_extract_candidate_ids_calls_search(monkeypatch):
    state = {"targets": {"tropes": ["heist"], "styles": []}}
    with (
        patch("agentic_librarian.agents.pipeline.search_internal_database", return_value=[{"id": "w1"}, {"id": "w2"}]),
        patch("agentic_librarian.agents.pipeline.get_unacted_suggestions", return_value=[{"id": "w2"}]),
    ):
        ids = extract_candidate_ids(state)
    assert ids == ["w1", "w2"]  # de-duplicated, order preserved
