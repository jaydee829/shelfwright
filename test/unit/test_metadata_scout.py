from unittest.mock import MagicMock, patch

import agentic_librarian.scouts.metadata_scout as md_scout
import pytest


@pytest.fixture(autouse=True)
def mock_search_api_key(request):
    """Mock the search API key ONLY for non-live tests."""
    if "api_dependent" in request.keywords:
        yield
    else:
        with patch.dict("os.environ", {"GOOGLE_SEARCH_API_KEY": "mock_key"}):
            yield


@pytest.mark.parametrize(
    "mock_json,expected_title,expected_authors",
    [
        (
            {
                "items": [
                    {
                        "id": "test_id",
                        "volumeInfo": {
                            "title": "Test Book",
                            "authors": ["Test Author"],
                            "publishedDate": "2020-01-01",
                            "categories": ["Fiction"],
                        },
                    }
                ]
            },
            "Test Book",
            ["Test Author"],
        ),
    ],
)
def test_google_books_scout_search(monkeypatch, mock_json, expected_title, expected_authors):
    scout = md_scout.GoogleBooksScout(api_key="key")
    monkeypatch.setattr(scout, "_make_request", lambda *a, **k: mock_json)

    metadata = scout.search("Title", "Author")
    assert metadata["title"] == expected_title

    # Extract names from contributors list
    author_names = [c["name"] for c in metadata["contributors"] if c["role"] == "Author"]
    assert author_names == expected_authors


def test_api_scout_session_retries_transient_http_errors():
    # The Google Books enrichment burst 429s (REC-016/020); APIScout must retry transient 429/5xx
    # with backoff instead of dropping a book's metadata.
    from urllib3.util.retry import Retry

    scout = md_scout.GoogleBooksScout(api_key="key")
    retry = scout._session.get_adapter("https://www.googleapis.com").max_retries
    assert isinstance(retry, Retry)
    for code in (429, 500, 502, 503, 504):
        assert code in retry.status_forcelist
    assert retry.total >= 3


def test_hardcover_scout_shares_the_retrying_session():
    # Hardcover is an APIScout too, so it inherits the same transient-error retry.
    scout = md_scout.HardcoverScout(api_key="key")
    retry = scout._session.get_adapter("https://api.hardcover.app").max_retries
    assert 429 in retry.status_forcelist


def test_scout_manager_merging():
    """Verify that ScoutManager correctly merges and prioritizes data from multiple scouts."""
    manager = md_scout.ScoutManager()

    class FakeScout(md_scout.BaseScout):
        def __init__(self, data):
            super().__init__()
            self.data = data

        def search(self, t, a, **k):
            return self.data

    scout1 = FakeScout({"title": "Priority 1", "page_count": 100})
    scout2 = FakeScout({"title": "Priority 2", "description": "Desc 2"})

    manager.register_scout(scout1, priority=1)
    manager.register_scout(scout2, priority=2)

    result = manager.enrich("Original", "Author")

    # 1. Priority 1 wins for title
    assert result["title"] == "Priority 1"
    # 2. Both fields captured
    assert result["page_count"] == 100
    assert result["description"] == "Desc 2"
    # 3. Source tracking
    assert "FakeScout" in result["source_priority"]


def test_scout_manager_merges_styles_and_tropes():
    """enrich() must surface StyleScout/LLMTropeScout outputs into the merged record."""
    manager = md_scout.ScoutManager()

    class FakeScout(md_scout.BaseScout):
        def __init__(self, data):
            super().__init__()
            self.data = data

        def search(self, t, a, **k):
            return self.data

    style_scout = FakeScout({"author_style": {"pacing": "fast"}, "work_style": {"perspective": "1st person"}})
    trope_scout = FakeScout({"tropes": [{"trope_name": "The Chosen One", "relevance_score": 0.9}]})
    manager.register_scout(style_scout, priority=1)
    manager.register_scout(trope_scout, priority=2)

    result = manager.enrich("Title", "Author")

    assert result["author_style"] == {"pacing": "fast"}
    assert result["work_style"] == {"perspective": "1st person"}
    assert result["enriched_tropes"] == [{"trope_name": "The Chosen One", "relevance_score": 0.9}]


def test_create_scout_manager_registers_style_and_trope_scouts():
    """ENV-015: the StyleScout and LLMTropeScout must be wired into the live ScoutManager."""
    from agentic_librarian.orchestration.definitions import create_scout_manager

    manager = create_scout_manager()
    registered = {type(scout).__name__ for scout, _ in manager.scouts}

    assert "StyleScout" in registered
    assert "LLMTropeScout" in registered
    # The previously-registered scouts must remain.
    assert {"HardcoverScout", "GoogleBooksScout", "AudiobookScout", "DirectKnowledgeScout"} <= registered


def test_safe_extract_json_handles_fences_prose_and_none():
    """Grounded LLM output may be fenced, prose-wrapped, or empty."""
    scout = md_scout.LLMTropeScout(api_key="fake-key")

    assert scout._safe_extract_json('```json\n{"a": 1}\n```', "t", "a") == {"a": 1}
    assert scout._safe_extract_json('Here you go:\n{"a": 1}\nThanks.', "t", "a") == {"a": 1}
    assert scout._safe_extract_json(None, "t", "a") is None


def test_extract_text_falls_back_to_candidate_parts():
    """When response.text is empty (grounded responses), text comes from the candidate parts."""

    from agentic_librarian.scouts.grounded_llm import _extract_text

    direct = MagicMock()
    direct.text = "hello"
    assert _extract_text(direct) == "hello"

    grounded = MagicMock()
    grounded.text = None
    part = MagicMock()
    part.text = '{"x": 1}'
    grounded.candidates = [MagicMock(content=MagicMock(parts=[part]))]
    assert _extract_text(grounded) == '{"x": 1}'


@pytest.mark.api_dependent
def test_enrich_with_real_scouts_produces_styles_and_tropes():
    """Live: the wired scouts actually produce styles and tropes via Gemini (grounding on)."""
    from agentic_librarian.orchestration.definitions import create_scout_manager

    manager = create_scout_manager()
    result = manager.enrich("The Way of Kings", "Brandon Sanderson", format="hardcover")

    assert result["author_style"], "expected non-empty author_style from StyleScout"
    assert result["enriched_tropes"], "expected non-empty enriched_tropes from LLMTropeScout"


def test_hardcover_select_hit_prefers_author_match_and_reads():
    import agentic_librarian.scouts.metadata_scout as md_scout

    scout = md_scout.HardcoverScout(api_key="key")
    hits = [
        {
            "document": {
                "id": "999",
                "title": "Workbook on The Spanish Love Deception",
                "author_names": ["Elena Armas"],
                "users_read_count": 50,
            }
        },
        {
            "document": {
                "id": "1",
                "title": "The Spanish Love Deception",
                "author_names": ["Elena Armas"],
                "users_read_count": 578,
            }
        },
        {"document": {"id": "2", "title": "The Spanish Love Deception", "author_names": [], "users_read_count": 1}},
    ]
    assert scout._select_hit(hits, "Elena Armas") == 1  # canonical (companion excluded, max reads, author match)
    assert scout._select_hit([], "Elena Armas") is None


def test_hardcover_select_hit_ignores_blank_author_names():
    import agentic_librarian.scouts.metadata_scout as md_scout

    # A blank author_name ("") must not falsely author-match (else any query matches it). The real
    # author's hit wins even with far fewer reads.
    scout = md_scout.HardcoverScout(api_key="key")
    hits = [
        {"document": {"id": "9", "title": "X", "author_names": [""], "users_read_count": 999}},
        {"document": {"id": "1", "title": "X", "author_names": ["Elena Armas"], "users_read_count": 5}},
    ]
    assert scout._select_hit(hits, "Elena Armas") == 1


def test_hardcover_search_two_step_parses_canonical_book(monkeypatch):
    import agentic_librarian.scouts.metadata_scout as md_scout

    scout = md_scout.HardcoverScout(api_key="key")
    search_resp = {
        "data": {
            "search": {
                "results": {
                    "hits": [
                        {
                            "document": {
                                "id": "999",
                                "title": "Workbook on The Spanish Love Deception",
                                "author_names": ["Elena Armas"],
                                "users_read_count": 50,
                            }
                        },
                        {
                            "document": {
                                "id": "1",
                                "title": "The Spanish Love Deception",
                                "author_names": ["Elena Armas"],
                                "users_read_count": 578,
                            }
                        },
                    ]
                }
            }
        }
    }
    books_resp = {
        "data": {
            "books": [
                {
                    "title": "The Spanish Love Deception",
                    "description": "A rom-com.",
                    "pages": 488,
                    "release_date": "2021-02-23",
                    "contributions": [{"author": {"name": "Elena Armas"}, "contribution": "Author"}],
                    "moods": [{"tagSlug": "funny"}],
                    "genres": [{"tagSlug": "romance"}],
                    "editions": [
                        {
                            "isbn_13": "9781",
                            "edition_format": "Hardcover",
                            "pages": 500,
                            "audio_seconds": None,
                            "release_date": "2022-01-01",
                            "country": {"name": "United Kingdom"},
                        },
                        {
                            "isbn_13": "9782",
                            "edition_format": "Paperback",
                            "pages": 488,
                            "audio_seconds": None,
                            "release_date": "2021-02-23",
                            "country": {"name": "United States of America"},
                        },
                    ],
                }
            ]
        }
    }
    responses = iter([search_resp, books_resp])
    captured = {}

    def fake_make_request(method, url, **kwargs):
        captured.setdefault("ids", []).append(kwargs.get("json", {}).get("variables"))
        return next(responses)

    monkeypatch.setattr(scout, "_make_request", fake_make_request)
    md = scout.search("The Spanish Love Deception", "Elena Armas", format="Paperback")

    assert md["title"] == "The Spanish Love Deception"
    assert md["description"] == "A rom-com."
    assert md["page_count"] == 488  # the US Paperback edition, not the UK Hardcover
    assert md["isbn_13"] == "9782"
    assert [c["name"] for c in md["contributors"]] == ["Elena Armas"]
    assert md["genres"] == {"romance"} and md["moods"] == {"funny"}
    assert captured["ids"][1] == {"id": 1}  # second call queried the canonical book id (1), not the workbook (999)


def test_hardcover_search_returns_empty_when_no_hits(monkeypatch):
    import agentic_librarian.scouts.metadata_scout as md_scout

    scout = md_scout.HardcoverScout(api_key="key")
    monkeypatch.setattr(scout, "_make_request", lambda *a, **k: {"data": {"search": {"results": {"hits": []}}}})
    assert scout.search("Nonexistent", "Nobody") == {}


@pytest.mark.api_dependent
def test_hardcover_live_returns_metadata_for_known_title():
    import agentic_librarian.scouts.metadata_scout as md_scout

    md = md_scout.HardcoverScout().search("The Spanish Love Deception", "Elena Armas", format="ebook")
    assert md, "Hardcover returned nothing for a known title"
    assert md.get("description")
    assert md.get("page_count")


def test_google_books_warns_once_without_key(capsys, monkeypatch):
    import agentic_librarian.scouts.metadata_scout as md_scout

    monkeypatch.delenv("GOOGLE_BOOKS_API_KEY", raising=False)
    monkeypatch.setattr(md_scout, "_gbooks_nokey_warned", False)  # auto-restored on teardown
    scout = md_scout.GoogleBooksScout()
    monkeypatch.setattr(scout, "_make_request", lambda *a, **k: {})
    scout.search("T", "A")
    scout.search("T2", "A2")
    out = capsys.readouterr().out
    assert out.count("no GOOGLE_BOOKS_API_KEY") == 1


def test_enrich_isolates_a_failing_scout(capsys):
    import agentic_librarian.scouts.metadata_scout as md_scout

    class GoodScout(md_scout.BaseScout):
        def search(self, title, author, **kwargs):
            return {"genres": ["g1"]}

    class BoomScout(md_scout.BaseScout):
        def search(self, title, author, **kwargs):
            raise RuntimeError("429 boom")

    mgr = md_scout.ScoutManager()
    mgr.register_scout(GoodScout(), priority=1)
    mgr.register_scout(BoomScout(), priority=2)

    merged = mgr.enrich("T", "A")  # must NOT raise
    assert "g1" in merged["genres"]
    assert "BoomScout scout failed" in capsys.readouterr().out


def test_flatten_style_map_hoists_nested_and_drops_nonstrings():
    from agentic_librarian.scouts.metadata_scout import _flatten_style_map

    raw = {
        "perspective": "1st person",
        "pacing": "",  # empty -> dropped
        "differences": {"prose_density": "denser", "tone": "darker"},  # nested -> hoisted
        "junk": ["a", "b"],  # list -> dropped
    }
    assert _flatten_style_map(raw) == {
        "perspective": "1st person",
        "prose_density": "denser",
        "tone": "darker",
    }
    assert _flatten_style_map("not a dict") == {}
