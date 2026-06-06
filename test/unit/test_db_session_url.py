"""DATABASE_URL must take priority over component vars (Lift 0: Cloud Run injects only DATABASE_URL)."""

from agentic_librarian.db.session import DatabaseManager


def test_database_url_alone_is_sufficient(monkeypatch):
    """With only DATABASE_URL set (no POSTGRES_USER/PASSWORD), the engine builds from it."""
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://cloud_user:cloud_pw@/agentic_librarian?host=/cloudsql/proj:region:inst")

    manager = DatabaseManager()
    url = manager.engine.url
    assert url.username == "cloud_user"
    assert url.database == "agentic_librarian"
    assert url.query["host"] == "/cloudsql/proj:region:inst"


def test_database_url_beats_component_vars(monkeypatch):
    """DATABASE_URL wins even when component vars are also present."""
    monkeypatch.setenv("POSTGRES_USER", "componentuser")
    monkeypatch.setenv("POSTGRES_PASSWORD", "componentpw")
    monkeypatch.setenv("POSTGRES_HOST", "componenthost")
    monkeypatch.setenv("DATABASE_URL", "postgresql://urluser:urlpw@urlhost:5432/urldb")

    manager = DatabaseManager()
    assert manager.engine.url.host == "urlhost"
    assert manager.engine.url.username == "urluser"


def test_component_vars_still_work_without_database_url(monkeypatch):
    """Backwards compatibility: the component path is unchanged when DATABASE_URL is absent."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "componentuser")
    monkeypatch.setenv("POSTGRES_PASSWORD", "componentpw")
    monkeypatch.setenv("POSTGRES_HOST", "componenthost")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_DB", "componentdb")

    manager = DatabaseManager()
    url = manager.engine.url
    assert url.host == "componenthost"
    assert url.port == 5433
    assert url.database == "componentdb"


def test_explicit_db_url_argument_still_wins(monkeypatch):
    """A constructor-passed URL beats everything (existing contract, pinned)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://env:env@envhost:5432/envdb")
    manager = DatabaseManager(db_url="postgresql://arg:arg@arghost:5432/argdb")
    assert manager.engine.url.host == "arghost"


def test_empty_database_url_falls_through_to_component_vars(monkeypatch):
    """DATABASE_URL set-but-empty (blanked secret) must not reach create_engine('')."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("POSTGRES_USER", "componentuser")
    monkeypatch.setenv("POSTGRES_PASSWORD", "componentpw")
    monkeypatch.setenv("POSTGRES_HOST", "componenthost")

    manager = DatabaseManager()
    assert manager.engine.url.host == "componenthost"
