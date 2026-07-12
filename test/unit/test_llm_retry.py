from agentic_librarian.llm_retry import RETRY_OPTIONS, genai_http_options


def test_retry_options_cover_transient_codes():
    for code in (429, 500, 502, 503, 504):
        assert code in RETRY_OPTIONS.http_status_codes
    assert RETRY_OPTIONS.attempts >= 3


def test_genai_http_options_carries_retry():
    ho = genai_http_options()
    assert ho.retry_options is RETRY_OPTIONS


def test_genai_http_options_sets_timeout():
    """#103: every Gemini call must carry a client-side timeout. HttpOptions.timeout is
    MILLISECONDS (google-genai 2.8.0) — 120s accommodates grounded deep-scout calls."""
    from agentic_librarian.llm_retry import GENAI_TIMEOUT_MS, genai_http_options

    assert GENAI_TIMEOUT_MS == 120_000
    assert genai_http_options().timeout == 120_000
