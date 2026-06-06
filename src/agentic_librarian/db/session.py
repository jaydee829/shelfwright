import os
import sys
from contextlib import contextmanager
from getpass import getpass

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Load environment variables from .env file
load_dotenv()


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

        db_url = self._db_url

        # A full DATABASE_URL (e.g. Cloud Run injecting the Secret Manager connection
        # string) takes priority over component vars — checked BEFORE demanding
        # POSTGRES_USER/PASSWORD, which are only required for component-wise construction.
        if db_url is None:
            db_url = os.getenv("DATABASE_URL")

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

        # Prepare connect_args for SSL and other driver-specific options
        connect_args = {}
        ssl_mode = os.getenv("DB_SSL_MODE")
        if ssl_mode:
            connect_args["sslmode"] = ssl_mode

        self._engine = create_engine(db_url, connect_args=connect_args)
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
