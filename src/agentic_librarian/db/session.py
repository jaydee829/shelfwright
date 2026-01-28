import os
import sys
from getpass import getpass
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class DatabaseManager:
    """Manages SQLAlchemy engine and session lifecycle."""
    
    def __init__(self, db_url: str = None):
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
                    "Database credentials not found. Please set POSTGRES_USER and "
                    "POSTGRES_PASSWORD in your environment or .env file."
                )
            
            host = os.getenv("POSTGRES_HOST", "localhost")
            port = os.getenv("POSTGRES_PORT", "5432")
            db_name = os.getenv("POSTGRES_DB", "agentic_librarian")
            
            # Allow full DATABASE_URL to override components if provided
            db_url = os.getenv("DATABASE_URL")
            if not db_url:
                db_url = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"
        
        # Prepare connect_args for SSL and other driver-specific options
        connect_args = {}
        ssl_mode = os.getenv("DB_SSL_MODE")
        if ssl_mode:
            connect_args["sslmode"] = ssl_mode

        self.engine = create_engine(db_url, connect_args=connect_args)
        self.SessionFactory = sessionmaker(bind=self.engine)
        
    @contextmanager
    def get_session(self) -> Session:
        """Context manager for database sessions."""
        session = self.SessionFactory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
