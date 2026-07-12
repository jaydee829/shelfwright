"""Startup migration guard (ADR-058, GH #92).

Compares the database's alembic_version to the migration head shipped in the image.
Called from the FastAPI lifespan: a MISMATCH raises, the container exits, the new
Cloud Run revision never becomes ready, and traffic keeps serving from the previous
revision — deploy-time enforcement without handing CI any database credentials.

An UNREACHABLE database only logs a warning: a transient DB blip must not kill
scale-from-zero cold starts (DB health has its own signals), and the in-runner
docker smoke test (deploy.yml) boots with a bogus DATABASE_URL on purpose.
"""

import logging
import os

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

logger = logging.getLogger(__name__)

_OFF_VALUES = {"off", "0", "false"}


class MigrationMismatchError(RuntimeError):
    """The database schema version does not match the code's migration head."""


def expected_head(config_path: str = "alembic.ini") -> str:
    """The single migration head shipped with this code. Multiple or zero heads is a
    packaging/branching bug and must fail startup loudly (the runner smoke test then
    catches e.g. a forgotten Dockerfile COPY of alembic/)."""
    script = ScriptDirectory.from_config(Config(config_path))
    heads = script.get_heads()
    if len(heads) != 1:
        raise MigrationMismatchError(f"expected exactly one alembic head, found {heads!r}")
    return heads[0]


def check_migrations(db_manager, config_path: str = "alembic.ini") -> None:
    """Raise MigrationMismatchError when the DB is behind/diverged from the code head.

    MIGRATION_GUARD=off|0|false skips the check entirely (emergency escape hatch,
    e.g. deploying the fix for a bad migration).
    """
    if os.getenv("MIGRATION_GUARD", "on").strip().lower() in _OFF_VALUES:
        logger.warning("MIGRATION_GUARD is off — skipping the startup migration check")
        return

    head = expected_head(config_path)

    # Connectivity probe, separate from the version query so "DB down" (tolerated)
    # is distinguishable from "alembic_version missing" (a mismatch, loud).
    try:
        with db_manager.get_session() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        logger.warning(
            "migration guard: database unreachable at startup — skipping check (code head %s)",
            head,
            exc_info=True,
        )
        return

    try:
        with db_manager.get_session() as session:
            current = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception as exc:
        raise MigrationMismatchError(
            "alembic_version table is missing — the database is not stamped; "
            "run 'alembic upgrade head' (or 'alembic stamp') before deploying"
        ) from exc

    if current != head:
        raise MigrationMismatchError(
            f"database is at migration {current!r} but the code head is {head!r} — "
            "run 'alembic upgrade head' against prod before deploying "
            "(emergency bypass: MIGRATION_GUARD=off)"
        )
