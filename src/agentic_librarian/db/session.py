import os
import sys
from contextlib import contextmanager
from getpass import getpass

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Load environment variables from .env file
load_dotenv()


def resolve_database_url(db_url: str | None = None) -> str:
    """DATABASE_URL-first resolution, shared by the app (DatabaseManager) and Alembic
    (alembic/env.py) so migrations always hit the same database the app would."""
    if db_url is None:
        db_url = os.getenv("DATABASE_URL") or None  # "" (blanked secret) falls through to component vars

    if db_url is None:
        # Check for individual environment variables
        user = os.getenv("POSTGRES_USER")
        password = os.getenv("POSTGRES_PASSWORD")

        # Prompt for missing credentials if in an interactive terminal
        if (not user or not password) and sys.stdin.isatty():
            print("\nMissing database credentials.")
            if not user:
                user = input("Enter Postgres username: ")
            if not password:
                password = getpass("Enter Postgres password: ")

        # Error if still missing and not interactive
        if not user or not password:
            raise ValueError(
                "Database credentials not found. Please set DATABASE_URL, or "
                "POSTGRES_USER and POSTGRES_PASSWORD, in your environment or .env file."
            )

        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        db_name = os.getenv("POSTGRES_DB", "agentic_librarian")
        db_url = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"
    return db_url


class DatabaseManager:
    """Manages SQLAlchemy engine and session lifecycle."""

    def __init__(self, db_url: str = None):
        self._db_url = db_url
        self._engine = None
        self._SessionFactory = None

    def _initialize(self):
        """Lazy initialization of engine and session factory."""
        if self._engine is not None:
            return

        db_url = resolve_database_url(self._db_url)

        # Prepare connect_args for SSL and other driver-specific options
        connect_args = {}
        ssl_mode = os.getenv("DB_SSL_MODE")
        if ssl_mode:
            connect_args["sslmode"] = ssl_mode

        pool_kwargs = {}
        if not db_url.startswith("sqlite"):
            # GH #102: pre_ping heals stale connections after Cloud SQL restarts/idle;
            # recycle beats server-side idle kills. 5+5 per engine × max-instances=2 = 20
            # peak vs db-f1-micro's ~25. Overflow headroom is deliberate: #94 removed
            # scout/LLM/Thunder calls from sessions, but embedding calls still run inside
            # them (search tools + persist-time standardize_trope/style) — under a Gemini
            # 429 burst those sessions stretch to minutes. Tighten to 5+2 once embeds are
            # hoisted out of sessions (GH #123).
            # sqlite (tests) uses its own pool class that rejects QueuePool kwargs.
            pool_kwargs = {"pool_pre_ping": True, "pool_recycle": 1800, "pool_size": 5, "max_overflow": 5}
        self._engine = create_engine(db_url, connect_args=connect_args, **pool_kwargs)
        self._SessionFactory = sessionmaker(bind=self._engine)

    @property
    def engine(self):
        self._initialize()
        return self._engine

    @contextmanager
    def get_session(self) -> Session:
        """Context manager for database sessions."""
        self._initialize()
        session = self._SessionFactory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
