import pytest

from agentic_librarian.etl import tag_backfill


@pytest.mark.parametrize(
    "url,ok",
    [
        ("postgresql://u:p@10.1.2.3:5432/agentic_librarian", True),
        ("postgresql://u:p@host.docker.internal:5433/agentic_librarian", True),
        ("postgresql://u:p@localhost:5432/agentic_librarian", False),
        ("postgresql://u:p@127.0.0.1:5432/agentic_librarian", False),
        ("sqlite:///data/backups/snapshot.db", False),
        ("postgresql://u:p@/agentic_librarian?host=/cloudsql/proj:reg:inst", True),
    ],
)
def test_is_prod_url(url, ok):
    assert tag_backfill.is_prod_url(url) is ok
