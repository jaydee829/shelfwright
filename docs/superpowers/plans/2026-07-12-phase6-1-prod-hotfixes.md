# Phase 6.1 Prod-Risk Hotfixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the five act-first prod risks (#89 memory drift, #90 squash leak docs, #91 backup codification, #92 migration guard, #99 stranded imports) in one PR on branch `fix/phase6-1-prod-hotfixes`.

**Architecture:** A new startup migration guard module (`db/migration_guard.py`, ADR-058) wired into the FastAPI lifespan makes a code-vs-schema mismatch fail the Cloud Run revision (traffic stays on the old one). The rest are surgical config/filter fixes: deploy.yml memory pin, infra script codification of live tuning, one SQLAlchemy filter extension, and doc updates. Live gcloud/gh operations happen OUTSIDE this plan (run by the controller/user).

**Tech Stack:** FastAPI, SQLAlchemy, alembic (`ScriptDirectory`), pytest, GitHub Actions, bash infra scripts.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-12-phase6-1-prod-risk-hotfixes-design.md` — its Behavior matrix for the guard is binding.
- Cloud Run memory value: exactly `2Gi`. Enrich queue values: exactly `--max-concurrent-dispatches=4 --max-dispatches-per-second=5`. Backup flags: exactly `--backup-start-time=09:00 --enable-point-in-time-recovery`.
- Guard module API: `check_migrations(db_manager, config_path="alembic.ini") -> None`, raising `MigrationMismatchError` (subclass of `RuntimeError`); escape hatch env var named `MIGRATION_GUARD` (off values: `off`/`0`/`false`).
- Guard behavior: DB **unreachable** (connectivity probe fails) → log warning + continue; `alembic_version` **table missing** or **version mismatch** → raise; multiple/zero alembic heads or missing `alembic.ini` → raise (the in-runner docker smoke test then catches a forgotten Dockerfile COPY).
- New unit tests must be DB-free-or-sqlite (NO `db_integration` marker) so they run locally; the `db_integration`-marked import-status tests only run in CI — do not claim them as locally verified.
- Python tests: run with `.venv/Scripts/python -m pytest` from the repo root (Windows host venv). Lint: `.venv/Scripts/python -m ruff check <changed files>` (if ruff is missing from the venv, `uvx ruff check`).
- Commit per task; commit subjects reference the issue numbers. Do NOT put `[skip ci]` anywhere in any commit message.
- Do not modify: `frontend/**`, persona/chat code, anything outside the files each task lists.

---

### Task 1: Migration guard module (`db/migration_guard.py`) — #92

**Files:**
- Create: `src/agentic_librarian/db/migration_guard.py`
- Test: `test/unit/test_migration_guard.py`

**Interfaces:**
- Consumes: `agentic_librarian.db.session.DatabaseManager` (existing; `get_session()` context manager).
- Produces: `check_migrations(db_manager, config_path: str = "alembic.ini") -> None` and `MigrationMismatchError(RuntimeError)` — Task 2 wires these into the lifespan.

- [ ] **Step 1: Write the failing tests**

Create `test/unit/test_migration_guard.py`:

```python
"""Startup migration guard (ADR-058): mismatch fails startup, unreachable DB does not."""

import pytest
from sqlalchemy import text

from agentic_librarian.db.migration_guard import (
    MigrationMismatchError,
    check_migrations,
    expected_head,
)
from agentic_librarian.db.session import DatabaseManager


@pytest.fixture()
def sqlite_manager(tmp_path):
    # File-based (NOT :memory:) so every new connection sees the same database.
    return DatabaseManager(f"sqlite:///{tmp_path}/guard.db")


def _stamp(manager, version):
    with manager.get_session() as s:
        s.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        s.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": version})


def test_env_off_skips_everything(monkeypatch):
    monkeypatch.setenv("MIGRATION_GUARD", "off")
    # Would raise on any real check (nonexistent config + unreachable DB) — off must short-circuit.
    check_migrations(DatabaseManager("postgresql://x:x@nohost:1/x"), config_path="no-such.ini")


def test_unreachable_db_warns_and_continues(monkeypatch, caplog):
    monkeypatch.setenv("MIGRATION_GUARD", "on")
    check_migrations(DatabaseManager("postgresql+psycopg2://x:x@nohost:1/x"))
    assert any("unreachable" in r.message for r in caplog.records)


def test_missing_alembic_version_table_raises(monkeypatch, sqlite_manager):
    monkeypatch.setenv("MIGRATION_GUARD", "on")
    with pytest.raises(MigrationMismatchError, match="not stamped"):
        check_migrations(sqlite_manager)


def test_version_mismatch_raises(monkeypatch, sqlite_manager):
    monkeypatch.setenv("MIGRATION_GUARD", "on")
    _stamp(sqlite_manager, "0000deadbeef")
    with pytest.raises(MigrationMismatchError, match="0000deadbeef"):
        check_migrations(sqlite_manager)


def test_matching_version_passes(monkeypatch, sqlite_manager):
    monkeypatch.setenv("MIGRATION_GUARD", "on")
    _stamp(sqlite_manager, expected_head())
    check_migrations(sqlite_manager)  # must not raise


def test_missing_config_raises(monkeypatch, sqlite_manager):
    monkeypatch.setenv("MIGRATION_GUARD", "on")
    with pytest.raises(Exception):  # packaging bug must be loud (spec: smoke test catches it)
        check_migrations(sqlite_manager, config_path="does-not-exist.ini")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest test/unit/test_migration_guard.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'agentic_librarian.db.migration_guard'`

- [ ] **Step 3: Implement the module**

Create `src/agentic_librarian/db/migration_guard.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest test/unit/test_migration_guard.py -v`
Expected: 6 passed. (If `test_unreachable_db_warns_and_continues` is slow, that's the psycopg2 connect timeout to `nohost` — acceptable if it passes; it should fail DNS instantly.)

- [ ] **Step 5: Lint and commit**

Run: `.venv/Scripts/python -m ruff check src/agentic_librarian/db/migration_guard.py test/unit/test_migration_guard.py`

```bash
git add src/agentic_librarian/db/migration_guard.py test/unit/test_migration_guard.py
git commit -m "feat(db): startup migration guard — fail the revision when prod schema is behind (#92)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Wire the guard into the lifespan + ship alembic in the image — #92

**Files:**
- Modify: `src/agentic_librarian/api/main.py:48-67` (lifespan)
- Modify: `Dockerfile.api:36-39` (COPY block)
- Modify: `test/conftest.py` (top-level: default the guard off for the whole suite)
- Test: `test/unit/test_migration_guard.py` (one added test)

**Interfaces:**
- Consumes: `check_migrations(db_manager)` and `MigrationMismatchError` from Task 1 (exact names).
- Produces: lifespan behavior relied on by deploy (container exits on mismatch). No new symbols.

- [ ] **Step 1: Write the failing test**

Append to `test/unit/test_migration_guard.py`:

```python
def test_lifespan_calls_guard(monkeypatch):
    """The app lifespan must run the guard before serving (ADR-058)."""
    from fastapi.testclient import TestClient

    from agentic_librarian.api import main as main_mod

    calls = []
    monkeypatch.setattr(main_mod, "check_migrations", lambda mgr: calls.append(mgr))
    with TestClient(main_mod.app):
        pass
    assert len(calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest test/unit/test_migration_guard.py::test_lifespan_calls_guard -v`
Expected: FAIL — `AttributeError: <module ...main> has no attribute 'check_migrations'`

- [ ] **Step 3: Wire the guard into `main.py`**

In `src/agentic_librarian/api/main.py`, add to the imports (after the existing `from agentic_librarian.db.session import DatabaseManager`):

```python
from agentic_librarian.db.migration_guard import check_migrations
```

In `lifespan`, immediately after `shared = DatabaseManager()` and before `app.state.db_manager = shared`, add:

```python
    # ADR-058 (#92): refuse to serve when the DB schema is behind this code's migration
    # head — the failed revision keeps traffic on the previous one. Unreachable DB only
    # warns (cold-start protection); MIGRATION_GUARD=off is the emergency bypass.
    check_migrations(shared)
```

- [ ] **Step 4: Default the guard off for the test suite**

In `test/conftest.py`, add near the top (after the imports, before any fixtures):

```python
# ADR-058: the startup migration guard is opt-in for tests. Any test that runs the app
# lifespan (TestClient used as a context manager) would otherwise probe a real DB.
# The guard's own unit tests monkeypatch MIGRATION_GUARD back on explicitly.
os.environ.setdefault("MIGRATION_GUARD", "off")
```

(Ensure `import os` exists in the file; add it if missing.)

Then in `test/unit/test_migration_guard.py`, replace every `monkeypatch.setenv("MIGRATION_GUARD", "on")` with `monkeypatch.setenv("MIGRATION_GUARD", "on")` — conftest now defaults it off, and these tests must exercise the on path. (`test_env_off_skips_everything` and `test_lifespan_calls_guard` stay as they are.)

- [ ] **Step 5: Ship alembic in the prod image**

In `Dockerfile.api`, change the runtime-stage COPY block:

```dockerfile
# Non-editable install of the package + prod deps only (no [dev] or [claude] extras).
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Migration metadata for the startup guard (ADR-058): the guard reads the expected
# head from alembic/ at boot. NOT used to run migrations (those stay operator-manual).
COPY alembic.ini ./
COPY alembic ./alembic
```

- [ ] **Step 6: Run the full local suite**

Run: `.venv/Scripts/python -m pytest test/unit -v` then `.venv/Scripts/python -m pytest -m "not api_dependent and not slow and not live and not db_integration" -q`
Expected: all pass (db_integration tests deselect locally by design).

- [ ] **Step 7: Lint and commit**

Run: `.venv/Scripts/python -m ruff check src/agentic_librarian/api/main.py test/unit/test_migration_guard.py test/conftest.py`

```bash
git add src/agentic_librarian/api/main.py Dockerfile.api test/conftest.py test/unit/test_migration_guard.py
git commit -m "feat(api): run the migration guard in the lifespan; ship alembic/ in the image (#92)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Stranded-pending import rows — #99

**Files:**
- Modify: `src/agentic_librarian/api/imports.py:195-242` (`get_status` stalled count + `retry` filter)
- Test: `test/integration/test_api_import_status.py` (extend; `db_integration`-marked → verifiable in CI only)

**Interfaces:**
- Consumes: existing `ImportRow` model (`status`, `updated_at` columns), `STALLED_AFTER` constant.
- Produces: no new symbols; `stalled` in the GET payload now also counts stale `pending` rows.

- [ ] **Step 1: Write the failing tests**

Append to `test/integration/test_api_import_status.py`:

```python
def _seed_row(manager, status, minutes_old):
    from datetime import UTC, datetime, timedelta

    with manager.get_session() as s:
        job = ImportJob(user_id=DEFAULT_USER_ID, source="goodreads", total_rows=1)
        s.add(job)
        s.flush()
        s.add(
            ImportRow(
                import_job_id=job.id,
                user_id=DEFAULT_USER_ID,
                destination="history",
                status=status,
                updated_at=datetime.now(UTC) - timedelta(minutes=minutes_old),
            )
        )
        s.flush()
        return job.id


def test_retry_re_enqueues_stranded_pending_rows(client, monkeypatch):
    """A row whose enqueue RPC failed stays 'pending' with no task behind it (#99)."""
    c, manager = client
    job_id = _seed_row(manager, "pending", minutes_old=30)
    enq = []
    monkeypatch.setattr(imports_mod, "enqueue_import_row", lambda rid: enq.append(rid) or True)
    r = c.post(f"/import/{job_id}/retry")
    assert r.status_code == 200
    assert r.json()["retried"] == 1
    assert len(enq) == 1


def test_retry_ignores_fresh_pending_rows(client, monkeypatch):
    """Fresh pending rows are normally in-flight — retry must not double-enqueue them."""
    c, manager = client
    job_id = _seed_row(manager, "pending", minutes_old=0)
    enq = []
    monkeypatch.setattr(imports_mod, "enqueue_import_row", lambda rid: enq.append(rid) or True)
    r = c.post(f"/import/{job_id}/retry")
    assert r.json()["retried"] == 0
    assert enq == []


def test_stalled_counts_stale_pending_rows(client):
    """'stalled' = rows a retry will re-drive: stale processing + stale pending (#99)."""
    c, manager = client
    job_id = _seed_row(manager, "pending", minutes_old=30)
    body = c.get(f"/import/{job_id}").json()
    assert body["stalled"] == 1
    assert body["complete"] is False
```

- [ ] **Step 2: Verify they fail (locally they skip — reason through instead)**

Run: `.venv/Scripts/python -m pytest test/integration/test_api_import_status.py -v`
Expected locally: tests SKIP/deselect (`db_integration` needs Postgres). State in your report that CI is the verification gate for this file, per the repo's known constraint. Confirm the new tests at least COLLECT: `.venv/Scripts/python -m pytest test/integration/test_api_import_status.py --collect-only -q` lists them.

- [ ] **Step 3: Implement the filter + count changes**

In `src/agentic_librarian/api/imports.py`:

In `get_status`, replace the `stalled` query's filter:

```python
        stalled = (
            session.query(func.count())
            .select_from(ImportRow)
            .filter(
                ImportRow.import_job_id == job_id,
                # Rows a retry will re-drive (#99): stale 'processing' (worker died) AND
                # stale 'pending' (the enqueue RPC failed, so no task exists for the row).
                ImportRow.status.in_(("processing", "pending")),
                ImportRow.updated_at < datetime.now(UTC) - STALLED_AFTER,
            )
            .scalar()
        )
```

In `retry`, replace the row filter:

```python
        rows = (
            session.query(ImportRow)
            .filter(
                ImportRow.import_job_id == job_id,
                (ImportRow.status == "failed")
                # Stale processing (worker died) or stale pending (enqueue failed, #99) —
                # re-enqueueing is safe: the worker's status machine is idempotent.
                | (ImportRow.status.in_(("processing", "pending")) & (ImportRow.updated_at < cutoff)),
            )
            .all()
        )
```

- [ ] **Step 4: Run the DB-free suite to confirm nothing else broke**

Run: `.venv/Scripts/python -m pytest test/unit -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

Run: `.venv/Scripts/python -m ruff check src/agentic_librarian/api/imports.py test/integration/test_api_import_status.py`

```bash
git add src/agentic_librarian/api/imports.py test/integration/test_api_import_status.py
git commit -m "fix(imports): retry + stalled-count cover stranded 'pending' rows (#99)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Config codification — deploy.yml 2Gi + infra queue/backup pins — #89 #91

**Files:**
- Modify: `.github/workflows/deploy.yml:156`
- Modify: `infra/08-cloud-tasks.sh:26-28`
- Modify: `infra/02-cloudsql.sh:7-13`

**Interfaces:** none (config only). Exact values are in Global Constraints.

- [ ] **Step 1: deploy.yml memory pin**

In `.github/workflows/deploy.yml`, in the deploy step's `flags:`, change:

```yaml
            --memory=512Mi
```
to
```yaml
            --memory=2Gi
```

(No other flags change — `gcloud run deploy` retains live values for unspecified settings; memory only drifted because it was explicitly pinned. See ADR-051.)

- [ ] **Step 2: infra/08 enrich-queue pinning**

In `infra/08-cloud-tasks.sh`, replace the section-1 queue create (lines 26-28):

```bash
# 1) The queue the fast /books pass enqueues onto. Rates are pinned to the ADR-051 OOM
#    remediation (deep scouts ≈ ½GiB each; 4 concurrent fits the 2Gi service): defaults
#    (1000 concurrent / 500 per sec) caused the 2026-06-23 OOM storm. The update branch
#    converges a pre-existing queue (created with defaults or hand-tuned) to the same state.
ENRICH_QUEUE_FLAGS=(--max-concurrent-dispatches=4 --max-dispatches-per-second=5)
if gcloud tasks queues describe "${TASKS_QUEUE_NAME}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud tasks queues update "${TASKS_QUEUE_NAME}" --location="${REGION}" "${ENRICH_QUEUE_FLAGS[@]}"
else
  gcloud tasks queues create "${TASKS_QUEUE_NAME}" --location="${REGION}" "${ENRICH_QUEUE_FLAGS[@]}"
fi
```

- [ ] **Step 3: infra/02 backup codification**

In `infra/02-cloudsql.sh`, extend the create command and add a trailing note:

```bash
gcloud sql instances create "${SQL_INSTANCE}" \
  --database-version=POSTGRES_16 \
  --edition=enterprise \
  --tier=db-f1-micro \
  --region="${REGION}" \
  --storage-size=10GB \
  --storage-type=SSD \
  --backup-start-time=09:00 \
  --enable-point-in-time-recovery
```

And after the final `echo` block:

```bash
# Backups (GH #91): nightly automated backups at 09:00 UTC + PITR (7-day WAL default).
# For a PRE-EXISTING instance apply the same with:
#   gcloud sql instances patch "${SQL_INSTANCE}" --backup-start-time=09:00 --enable-point-in-time-recovery
```

- [ ] **Step 4: Syntax-check the scripts and validate the workflow**

Run: `bash -n infra/08-cloud-tasks.sh && bash -n infra/02-cloudsql.sh && echo OK`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/deploy.yml infra/08-cloud-tasks.sh infra/02-cloudsql.sh
git commit -m "fix(cd/infra): pin 2Gi in deploy.yml; codify enrich-queue rates + SQL backups (#89, #91)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Docs — ADR-058, bugs.md #90 resolution, key_facts refresh

**Files:**
- Modify: `docs/project_notes/decisions.md` (append after ADR-057)
- Modify: `docs/project_notes/bugs.md` (2026-06-17 entry, Prevention list)
- Modify: `docs/project_notes/key_facts.md` (Deploys + Database bullets)

**Interfaces:** none (docs only).

- [ ] **Step 1: Append ADR-058 to `docs/project_notes/decisions.md`** (after the ADR-057 block, same format):

```markdown
### ADR-058: Startup migration guard — a schema-behind deploy fails the revision (2026-07-12)
**Context:**
- deploy.yml has no alembic step by design (migrations are operator-manual, lift1 runbook), but
  nothing enforced the migrate-before-merge ordering: a merged migration PR deployed against an
  un-migrated prod DB → runtime 500s the /health smoke can't see (GH #92, 2026-07-02 review).
- Alternatives: a workflow-side DB query (hands prod DB credentials to CI) or a
  /health/migrations smoke assertion (checks only after traffic has shifted; and the deployer
  cannot mint Firebase tokens to pass the auth gate).
**Decision:**
- `db/migration_guard.py`, called in the FastAPI lifespan: compare the DB's `alembic_version`
  to the image's migration head (`alembic/` + `alembic.ini` now ship in the prod image). On
  mismatch (or missing alembic_version table, or ≠1 heads) the container exits → the Cloud Run
  revision never goes ready → the deploy fails while traffic keeps serving the old revision.
- DB **unreachable** only warns and continues: transient DB blips must not kill scale-from-zero
  cold starts, and the in-runner docker smoke boots with a bogus DATABASE_URL by design.
- `MIGRATION_GUARD=off|0|false` is the emergency bypass (e.g. deploying the fix for a bad
  migration). The test suite defaults it off in conftest; the guard's unit tests re-enable it.
**Consequences:**
- Migrations stay manual; the mismatch is now loud and self-rolls-back instead of silent 500s.
- Any deploy path is covered (workflow or manual gcloud), with no DB credentials in CI.
- A forgotten alembic COPY in Dockerfile.api fails the runner smoke test (guard raises on a
  missing script dir), so the guard cannot silently vanish from the image.
```

- [ ] **Step 2: Record the #90 durable fix in `docs/project_notes/bugs.md`**

In the 2026-06-17 entry, replace the Prevention list's item 1:

```markdown
  1. ~~At squash-merge, **edit the squash commit message** in GitHub's merge dialog and delete the
     `* …[skip ci]` bullets before confirming.~~ **DURABLE FIX APPLIED 2026-07-12 (GH #90):** repo
     setting changed to squash title = PR title, squash message = blank — commit bodies no longer
     enter the merge commit, so nothing can leak. Verified by the Phase 6.1 PR's own squash-merge
     auto-deploying.
```

(Leave items 2-4 unchanged — they remain good hygiene.)

- [ ] **Step 3: Refresh `docs/project_notes/key_facts.md`**

In the **Deploys** bullet of the Production section, after "tags = git SHAs.", append:

```markdown
  deploy.yml pins `--memory=2Gi` (ADR-051/GH #89 — was 512Mi drift) and the lifespan migration
  guard (ADR-058) fails the revision if prod's alembic_version is behind the image's head.
```

In the **Database** bullet, after "schema managed by Alembic (Lift 1+).", append:

```markdown
  Nightly automated backups (09:00 UTC) + 7-day PITR enabled 2026-07-12 (GH #91; codified in
  `infra/02-cloudsql.sh`).
```

- [ ] **Step 4: Commit**

```bash
git add docs/project_notes/decisions.md docs/project_notes/bugs.md docs/project_notes/key_facts.md
git commit -m "docs(project-notes): ADR-058 migration guard; #90 durable fix; deploy/backup facts (#89-#92)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Out of scope for this plan (controller/user runs live ops)

1. `gcloud run services update librarian-api --region us-central1 --memory=2Gi` (immediate stabilization).
2. `gh api -X PATCH repos/jaydee829/shelfwright -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=BLANK` (#90).
3. `gcloud sql instances patch librarian-sql --backup-start-time=09:00 --enable-point-in-time-recovery` (#91; pause + notify if gcloud warns of a restart).
4. Post-merge acceptance: push-triggered deploy fires (#90), live memory still 2Gi after it (#89), first nightly backup exists next day (#91); close #89 #90 #91 #92 #99 with PR references.
