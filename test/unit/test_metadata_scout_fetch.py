"""#103: the Audible page fetch must use the retrying session WITH a timeout."""

from unittest.mock import MagicMock, patch

from agentic_librarian.scouts import metadata_scout


def test_fetch_page_content_uses_session_with_timeout():
    scout = metadata_scout.AudiobookScout.__new__(metadata_scout.AudiobookScout)  # skip __init__ (needs keys)
    fake_response = MagicMock(content=b"<html><body>Audible page</body></html>")
    with (
        patch.object(metadata_scout.AudiobookScout, "search_audible_link", return_value="https://audible.com/x"),
        patch.object(metadata_scout._page_session, "get", return_value=fake_response) as mock_get,
    ):
        text = scout.fetch_page_content("Some Title")
    assert "Audible page" in text
    kwargs = mock_get.call_args.kwargs
    assert kwargs["timeout"] == metadata_scout._PAGE_TIMEOUT == 15


def test_page_session_mounts_retry_adapter():
    adapter = metadata_scout._page_session.get_adapter("https://audible.com")
    assert adapter.max_retries.total == metadata_scout._API_RETRY.total
