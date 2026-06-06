# Single-Title Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one book to the reading history without a CSV batch — a new `add_book_to_history` write tool (new-row-per-read model) with conversational + CLI entry points, per `docs/superpowers/specs/2026-06-05-single-title-import-design.md`.

**Architecture:** One SEC-002-validated tool in `mcp/server.py` that reuses `enrich_and_persist_work` for get-or-create + enrichment, then logs a READ EVENT (re-reads insert new rows; same-date duplicates no-op; read count derived from row count). Exposed to both Librarians (with IMPORT flow + extended CONFIRM clause) and as a `librarian add` CLI subcommand. The write-authorization invariant grows to five tools.

**Tech Stack:** Python 3.11, SQLAlchemy ORM, argparse subparsers, pytest (unit + db_integration).

**Environment notes (this machine):**
- Work in `C:\dev\agentic_librarian`, branch `spec/single-title-import`. Tests via **PowerShell** (Git Bash mangles `/app`):
  `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest <paths> -q -m "not api_dependent and not slow"`
  db_integration tests additionally need: `--network agentic_librarian_default -e POSTGRES_HOST=db`
- Git via Bash tool; ignore CRLF warnings. Never run `api_dependent` tests. Task 4 runs the authoritative in-container pre-commit gate.
- Pre-existing facts: `check_reading_history` ALREADY orders by `date_completed.desc()` (server.py:282) — Task 1 pins it with a regression test, no code change there. `_valid_name`, `_parse_uuid` exist (SEC-002 round).

---

### Task 1: The `add_book_to_history` tool + read-event regression tests

**Files:**
- Modify: `src/agentic_librarian/mcp/server.py` (new tool, placed after `update_reading_status`)
- Test: `test/integration/test_mcp_tools.py` (append; reuse its `seeded_work_id` fixture + `set_db_manager` pattern — READ the file first)

- [ ] **Step 1: Write the failing tests** — append to `test/integration/test_mcp_tools.py` (adapt fixture plumbing to the file's existing style; assertion bodies must be these):

```python
def _stub_enrich(monkeypatch, work_id):
    """add_book_to_history delegates get-or-create+enrichment to enrich_and_persist_work
    (same module global) — stub it so tests are offline-deterministic."""
    monkeypatch.setattr(mcp_server, "enrich_and_persist_work", lambda **kw: work_id)


@pytest.mark.db_integration
def test_add_book_logs_a_read_event(db_url, seeded_work_id, monkeypatch):
    _stub_enrich(monkeypatch, seeded_work_id)
    out = mcp_server.add_book_to_history(
        title="Seeded Book", author="Seeded Author", date_completed="2026-06-01", rating=5, notes="great"
    )
    assert "Added 'Seeded Book'" in out and "read #1" in out
    with mcp_server.db_manager.get_session() as session:
        rows = (
            session.query(ReadingHistory)
            .join(Edition)
            .filter(Edition.work_id == UUID(seeded_work_id))
            .all()
        )
        assert len(rows) == 1
        assert rows[0].date_completed.isoformat() == "2026-06-01"
        assert rows[0].user_rating == 5
        assert rows[0].user_notes == "great"


@pytest.mark.db_integration
def test_add_book_same_date_duplicate_noops_but_new_date_is_a_reread(db_url, seeded_work_id, monkeypatch):
    _stub_enrich(monkeypatch, seeded_work_id)
    mcp_server.add_book_to_history(title="Seeded Book", author="Seeded Author", date_completed="2024-01-01")
    # Same work + same date -> duplicate guard, no second row.
    out = mcp_server.add_book_to_history(title="Seeded Book", author="Seeded Author", date_completed="2024-01-01")
    assert "already logged" in out
    # Different date -> a RE-READ: new row, original untouched, message counts reads.
    out = mcp_server.add_book_to_history(title="Seeded Book", author="Seeded Author", date_completed="2026-05-01")
    assert "read #2" in out
    with mcp_server.db_manager.get_session() as session:
        dates = sorted(
            r.date_completed.isoformat()
            for r in session.query(ReadingHistory).join(Edition).filter(Edition.work_id == UUID(seeded_work_id))
        )
        assert dates == ["2024-01-01", "2026-05-01"]


@pytest.mark.db_integration
def test_add_book_defaults_date_to_today(db_url, seeded_work_id, monkeypatch):
    from datetime import date as _date

    _stub_enrich(monkeypatch, seeded_work_id)
    out = mcp_server.add_book_to_history(title="Seeded Book", author="Seeded Author")
    assert "Added" in out
    with mcp_server.db_manager.get_session() as session:
        row = session.query(ReadingHistory).join(Edition).filter(Edition.work_id == UUID(seeded_work_id)).one()
        assert row.date_completed == _date.today()


@pytest.mark.db_integration
def test_add_book_rejections_write_nothing(db_url, monkeypatch):
    # Validation precedes enrichment: a call that reaches enrich here is a failure.
    monkeypatch.setattr(
        mcp_server, "enrich_and_persist_work", lambda **kw: pytest.fail("enrich must not run on invalid input")
    )
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)
    assert "Error" in mcp_server.add_book_to_history(title="  ", author="A")
    assert "Error" in mcp_server.add_book_to_history(title="T", author="A", date_completed="June 1st")
    assert "Error" in mcp_server.add_book_to_history(title="T", author="A", date_completed="2999-01-01")
    assert "Error" in mcp_server.add_book_to_history(title="T", author="A", rating=0)
    assert "Error" in mcp_server.add_book_to_history(title="T", author="A", rating=6)
    assert "Error" in mcp_server.add_book_to_history(title="T", author="A", rating=3.5)  # type: ignore[arg-type]
    assert "Error" in mcp_server.add_book_to_history(title="T", author="A", rating=True)  # type: ignore[arg-type]
    with mcp_server.db_manager.get_session() as session:
        assert session.query(ReadingHistory).count() == 0  # nothing written


@pytest.mark.db_integration
def test_add_book_unresolvable_title_errors(db_url, monkeypatch):
    monkeypatch.setattr(mcp_server, "enrich_and_persist_work", lambda **kw: None)
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)
    out = mcp_server.add_book_to_history(title="Definitely Fake", author="Nobody")
    assert "Error" in out and "could not resolve" in out


@pytest.mark.db_integration
def test_check_reading_history_uses_latest_read(db_url, seeded_work_id, monkeypatch):
    # Pins the read-event model's guarantee: an old read + a recent re-read means
    # the work is NOT a re-read candidate (server.py already orders by date desc —
    # this test freezes that property).
    _stub_enrich(monkeypatch, seeded_work_id)
    mcp_server.add_book_to_history(title="Seeded Book", author="Seeded Author", date_completed="2020-01-01")
    mcp_server.add_book_to_history(title="Seeded Book", author="Seeded Author", date_completed="2026-06-01")
    with mcp_server.db_manager.get_session() as session:
        work = session.get(Work, UUID(seeded_work_id))
        title, author = work.title, work.contributors[0].author.name
    result = mcp_server.check_reading_history(title=title, author=author)
    assert result["date_completed"] == "2026-06-01"
    assert result["is_re_read_candidate"] is False
```

NOTE: the `seeded_work_id` fixture creates a Work titled per its own convention — if its title/author differ from "Seeded Book"/"Seeded Author", use the fixture's actual values in the calls (the tool's title/author only feed the STUBBED enrich + messages here; the work resolution comes from the stub's returned id). Check imports the file already has (`DataBaseManager`, `set_db_manager`, `ReadingHistory`, `Edition`, `Work`, `UUID`) and add missing ones in its style.

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest python -m pytest test/integration/test_mcp_tools.py -q -m "not api_dependent and not slow"`
Expected: all six FAIL with `AttributeError: ... has no attribute 'add_book_to_history'`.

- [ ] **Step 3: Implement** — add to `mcp/server.py` after `update_reading_status` (uses existing `_valid_name`, `_parse_uuid`, `date` import):

```python
@mcp.tool()
def add_book_to_history(
    title: str,
    author: str,
    date_completed: str | None = None,
    rating: int | None = None,
    format: str = "ebook",
    notes: str | None = None,
) -> str:
    """Add ONE book to the reading history (single-title import). Enriches + persists the
    work first if it isn't in the catalog (runs the scouts — takes a minute or two), then
    logs a READ EVENT. History is a log of read events: a re-read (different completion
    date) inserts a new row; the same work+date is a duplicate and is not double-logged.
    date_completed defaults to today (the Phase-4 UI will auto-fill it visibly)."""
    if not _valid_name(title):
        return "Error: title must be a non-empty string of at most 500 characters."
    if not _valid_name(author):
        return "Error: author must be a non-empty string of at most 500 characters."
    if date_completed is None:
        completed = date.today()
    else:
        try:
            completed = date.fromisoformat(str(date_completed))
        except ValueError:
            return f"Error: date_completed must be ISO YYYY-MM-DD; got {date_completed!r}."
        if completed > date.today():
            return f"Error: date_completed {completed.isoformat()} is in the future."
    # bool is an int subclass — reject it explicitly so rating=True can't slip in as 1.
    if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5):
        return f"Error: rating must be an integer from 1 to 5; got {rating!r}."
    format = (format or "ebook")[:50]
    notes = notes[:2000] if isinstance(notes, str) else None

    work_id = enrich_and_persist_work(title=title, author=author, format=format)
    if work_id is None:
        return f"Error: could not resolve '{title}' by {author} — check the spelling, or the scouts found nothing."

    try:
        with db_manager.get_session() as session:
            uuid_obj = _parse_uuid(work_id)
            edition = session.query(Edition).filter_by(work_id=uuid_obj, format=format).first()
            if not edition:
                edition = Edition(work_id=uuid_obj, format=format)
                session.add(edition)
                session.flush()
            prior_reads = (
                session.query(ReadingHistory)
                .join(Edition)
                .filter(Edition.work_id == uuid_obj)
                .all()
            )
            if any(r.date_completed == completed for r in prior_reads):
                return f"'{title}' is already logged as completed {completed.isoformat()}. No new entry written."
            session.add(
                ReadingHistory(
                    edition_id=edition.id,
                    date_completed=completed,
                    user_rating=rating,
                    user_notes=notes,
                )
            )
            session.flush()
            return f"Added '{title}' to your reading history (work {work_id}, read #{len(prior_reads) + 1})."
    except Exception as e:
        return f"Error adding to reading history: {str(e)}"
```

- [ ] **Step 4: Run tests to verify they pass**

Same command as Step 2. Expected: ALL PASS (new six + every pre-existing test).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/mcp/server.py test/integration/test_mcp_tools.py
git commit -m "feat: add_book_to_history — single-title import with read-event re-reads (validated, SEC-002 style)"
```

---

### Task 2: Conversational wiring — both Librarians, prompts, invariant

**Files:**
- Modify: `src/agentic_librarian/agents/backends/claude_tools.py` (`_TOOL_SCHEMAS` entry)
- Modify: `src/agentic_librarian/agents/services.py` (import + `FunctionTool` + inline instruction IMPORT/CONFIRM text)
- Modify: `src/agentic_librarian/agents/prompts.py` (`LIBRARIAN_INSTRUCTION` IMPORT/CONFIRM text)
- Modify: `test/unit/test_write_authorization.py` (`WRITE_TOOLS` grows to five)
- Test: `test/unit/test_claude_tools.py`, `test/unit/test_prompts.py`, `test/unit/test_agent_services.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `test/unit/test_claude_tools.py`:

```python
def test_add_book_tool_is_exposed():
    from agentic_librarian.agents.backends.claude_tools import LIBRARIAN_TOOL_NAMES

    assert "mcp__librarian__add_book_to_history" in LIBRARIAN_TOOL_NAMES
```

Append to `test/unit/test_prompts.py`:

```python
def test_librarian_has_the_import_flow():
    text = prompts.LIBRARIAN_INSTRUCTION
    assert "IMPORT" in text
    assert "add_book_to_history" in text
    assert "defaults to today" in text
    assert "minute or two" in text  # sets latency expectations before enrichment runs


def test_confirm_clause_covers_the_import_tool():
    # The CONFIRM HISTORY WRITES clause must gate BOTH history-writing tools.
    text = prompts.LIBRARIAN_INSTRUCTION
    confirm = text[text.index("CONFIRM HISTORY WRITES") :]
    assert "update_reading_status" in confirm
    assert "add_book_to_history" in confirm
```

Append to `test/unit/test_agent_services.py`:

```python
def test_adk_librarian_has_the_import_tool_and_flow():
    mesh = create_agent_mesh()
    assert "add_book_to_history" in [t.name for t in mesh["librarian"].tools]
    text = mesh["librarian"].instruction
    assert "add_book_to_history" in text
    confirm = text[text.index("CONFIRM HISTORY WRITES") :]
    assert "add_book_to_history" in confirm
```

In `test/unit/test_write_authorization.py`, extend `WRITE_TOOLS`:

```python
WRITE_TOOLS = {
    "log_suggestion",
    "update_reading_status",
    "update_suggestion_status",
    "enrich_and_persist_work",
    "add_book_to_history",
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_claude_tools.py test/unit/test_prompts.py test/unit/test_agent_services.py test/unit/test_write_authorization.py -q -m "not api_dependent and not slow"`
Expected: the four new tests FAIL; `test_write_authorization` positive assertions FAIL (the Librarians don't hold the fifth tool yet) — that's the intended RED.

- [ ] **Step 3: Implement**

`claude_tools.py` — append to `_TOOL_SCHEMAS` after the `enrich_and_persist_work` tuple:

```python
    (
        "add_book_to_history",
        "Add ONE book to the reading history (enrich first if needed); a re-read with a new date adds a new read event.",
        _schema(
            {
                "title": _STR,
                "author": _STR,
                "date_completed": _STR,
                "rating": _INT,
                "format": _STR,
                "notes": _STR,
            },
            required=["title", "author"],
        ),
        mcp_server.add_book_to_history,
    ),
```

`prompts.py` — in `LIBRARIAN_INSTRUCTION`, insert this paragraph directly after the `SERIES:` paragraph (before TRUST BOUNDARY; flush-left like the rest):

```
IMPORT: when the user says they read a book that is not in their history, add it with
'add_book_to_history' (title, author, optional rating 1-5, optional completion date —
defaults to today). If the book is not in the catalog yet this runs enrichment and takes
a minute or two; say so before calling. A re-read (different completion date) adds a new
read event rather than editing the old one.
```

And in the same constant's `CONFIRM HISTORY WRITES` paragraph, change:
`only call 'update_reading_status' when the user explicitly stated`
to:
`only call 'update_reading_status' or 'add_book_to_history' when the user explicitly stated`

`services.py` — three changes:
1. Add `add_book_to_history` to the `from agentic_librarian.mcp.server import (...)` block (alphabetically first).
2. Insert the SAME IMPORT paragraph (indented to the inline block's level) after the inline instruction's `SERIES:` paragraph, and make the same `CONFIRM HISTORY WRITES` edit there.
3. Add `FunctionTool(add_book_to_history),` to the Librarian's `tools=[...]` list (before `FunctionTool(enrich_and_persist_work)`).

- [ ] **Step 4: Run tests to verify they pass**

Same command as Step 2, PLUS `test/unit/test_claude_backend.py` (the mesh-options test must keep passing — the new tool joins `allowed_tools` automatically via `LIBRARIAN_TOOL_NAMES`).
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/agents/backends/claude_tools.py src/agentic_librarian/agents/prompts.py src/agentic_librarian/agents/services.py test/unit/test_claude_tools.py test/unit/test_prompts.py test/unit/test_agent_services.py test/unit/test_write_authorization.py
git commit -m "feat: expose add_book_to_history to both Librarians — IMPORT flow + extended confirm clause + invariant pins five write tools"
```

---

### Task 3: CLI subcommand — `librarian add`

**Files:**
- Modify: `src/agentic_librarian/cli.py` (`_parse_args` → subparsers; `main` dispatch; new `_run_add`)
- Test: `test/unit/test_cli.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `test/unit/test_cli.py`:

```python
def test_add_subcommand_success(monkeypatch, capsys):
    captured = {}

    def _fake_add(**kwargs):
        captured.update(kwargs)
        return "Added 'Project Hail Mary' to your reading history (work abc, read #1)."

    monkeypatch.setattr("agentic_librarian.mcp.server.add_book_to_history", _fake_add)
    rc = cli.main(["add", "Project Hail Mary", "--author", "Andy Weir", "--rating", "5", "--date", "2026-06-01"])
    assert rc == 0
    assert "Added 'Project Hail Mary'" in capsys.readouterr().out
    assert captured == {
        "title": "Project Hail Mary",
        "author": "Andy Weir",
        "date_completed": "2026-06-01",
        "rating": 5,
        "format": "ebook",
        "notes": None,
    }


def test_add_subcommand_error_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(
        "agentic_librarian.mcp.server.add_book_to_history", lambda **kw: "Error: rating must be an integer from 1 to 5; got 9."
    )
    rc = cli.main(["add", "T", "--author", "A", "--rating", "9"])
    assert rc == 1
    assert "Error" in capsys.readouterr().out


def test_add_requires_author(capsys):
    with pytest.raises(SystemExit):
        cli.main(["add", "Some Title"])  # argparse exits on missing --author


def test_repl_default_unaffected_by_subparsers(monkeypatch, capsys, no_mlflow_dir):
    # Bare `librarian` (no subcommand) must still enter the REPL path.
    fake = _FakeBackend(replies=("ok",))
    monkeypatch.setattr(cli, "get_backend", lambda: fake)
    _feed_stdin(monkeypatch, ["hi", "/quit"])
    assert cli.main(["--no-mlflow"]) == 0
```

(`pytest` may need importing at the top of the file if not already there — check.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_cli.py -q -m "not api_dependent and not slow"`
Expected: the three `add` tests FAIL (`SystemExit: 2` — argparse rejects the unknown `add` argument); the REPL test passes already.

- [ ] **Step 3: Implement** — in `cli.py`:

Replace `_parse_args` with:

```python
def _parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="librarian", description="Chat with the Librarian recommendation agent.")
    parser.add_argument("--once", metavar="PROMPT", help="one-shot recommendation (pipeline), then exit")
    parser.add_argument("--backend", choices=["adk", "claude"], help="override AGENT_BACKEND for this run")
    parser.add_argument("--user-id", default="local", help="user id for sessions and history (default: local)")
    parser.add_argument("--quiet", action="store_true", help="suppress the key-event trace")
    parser.add_argument("--no-mlflow", action="store_true", help="disable MLflow conversation capture")
    subparsers = parser.add_subparsers(dest="command")
    add_parser = subparsers.add_parser("add", help="add one book to your reading history (no LLM involved)")
    add_parser.add_argument("title", help="book title")
    add_parser.add_argument("--author", required=True, help="author name")
    add_parser.add_argument("--date", default=None, help="completion date YYYY-MM-DD (default: today)")
    add_parser.add_argument("--rating", type=int, default=None, help="rating 1-5")
    add_parser.add_argument("--format", default="ebook", help="edition format (default: ebook)")
    add_parser.add_argument("--notes", default=None, help="free-text notes")
    return parser.parse_args(argv)
```

In `main`, after `args = _parse_args(argv)` and BEFORE the backend/recorder setup, add:

```python
    if getattr(args, "command", None) == "add":
        return _run_add(args)
```

Add the handler (after `_run_once`):

```python
def _run_add(args) -> int:
    """Deterministic single-title import — calls the validated MCP tool directly (no LLM,
    no recorder; the tool itself runs enrichment, which can take a minute or two)."""
    # Lazy import: the MCP server module pulls in the DB/scout stack, which the REPL
    # path loads via the backends instead.
    from agentic_librarian.mcp.server import add_book_to_history

    result = add_book_to_history(
        title=args.title,
        author=args.author,
        date_completed=args.date,
        rating=args.rating,
        format=args.format,
        notes=args.notes,
    )
    print(result)
    return 1 if result.startswith("Error") else 0
```

NOTE on the test's monkeypatch target: `_run_add` imports the function at CALL time from `agentic_librarian.mcp.server`, so `monkeypatch.setattr("agentic_librarian.mcp.server.add_book_to_history", ...)` intercepts it.

- [ ] **Step 4: Run tests to verify they pass**

Same command. Expected: ALL PASS (including every pre-existing CLI test — the REPL default and `--once` behavior are unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/cli.py test/unit/test_cli.py
git commit -m "feat: librarian add — CLI single-title import subcommand"
```

---

### Task 4: Docs + gates

**Files:**
- Modify: `docs/project_notes/key_facts.md`, `docs/project_notes/security.md`, `docs/project_notes/issues.md`

- [ ] **Step 1: Update the three docs**

`key_facts.md` — append to the "Data Ingestion Assumptions" section:

```markdown
- **History source of truth (2026-06-05)**: the DATABASE. Single-title adds happen via
  `add_book_to_history` (conversationally or `librarian add`) and do NOT update the
  DVC-tracked CSVs — accepted drift; `pg_dump` snapshots are the backup. Bulk imports
  still go through the CSV/Dagster path. Reading history is a log of READ EVENTS: a
  re-read inserts a new row (re-read count = rows per work).
```

`security.md` — in the "Write-tool validation" posture bullet, change "Writes exist ONLY on the Librarian" sentence to mention five tools, e.g. append to that bullet:

```markdown
  `add_book_to_history` (2026-06-05) joined the validated write set — same upfront
  validation pattern; the invariant test now pins five write tools.
```

`issues.md` — append after TUNE-027:

```markdown
### 2026-06-05 - IMP-028: Single-title import (spec/single-title-import)
- **Status**: Merged pending live verification
- **Description**: `add_book_to_history` MCP tool (new-row-per-read model: re-reads
  insert read events; same-date duplicate guard; read count derived from row count) +
  conversational IMPORT flow on both Librarians + `librarian add` CLI subcommand.
  date_completed defaults to today. Spec: docs/superpowers/specs/2026-06-05-single-title-import-design.md.
- **URL**: N/A (PR pending)
- **Notes**:
    - **Phase-4 front end (logged per user decision)**: the web UI's add form must
      AUTO-FILL the completion-date field with today (visible + editable) rather than
      hiding the default — the default stays explicit to the user.
    - CSV drift accepted: DB is the history source of truth as of 2026-06-05 (see
      key_facts.md); bulk imports remain CSV/Dagster.
```

- [ ] **Step 2: Run the FULL fast suite**

`docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest python -m pytest test/unit test/integration -q -m "not api_dependent and not slow"`
Expected: ALL PASS (baseline 247 + ~14 new). Report the exact count.

- [ ] **Step 3: Run the authoritative pre-commit gate**

PowerShell: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest bash -c "git config --global --add safe.directory /app; pip install -q --user pre-commit 2>/dev/null; export PATH=$PATH:~/.local/bin; SKIP=pytest pre-commit run --all-files"`
Expected: all hooks Passed. If hooks auto-fix: only `git diff --stat` shows true content changes (LF rewrites are noise); `git add` the real ones, `git checkout -- .` the rest, include them in the commit. **Re-run the affected tests if any auto-fix touched a test or source file.**

- [ ] **Step 4: Commit**

```bash
git add docs/project_notes/key_facts.md docs/project_notes/security.md docs/project_notes/issues.md
git commit -m "docs: IMP-028 single-title import — source-of-truth note, write-tool posture, Phase-4 auto-fill follow-up"
```

- [ ] **Step 5 (manual, user-gated): live verification**

After merge + clone sync: one CLI add (`docker exec -it agentic_librarian_app python -m agentic_librarian.cli add "..." --author "..." --rating N`) and one conversational add ("I just finished X by Y, 4 stars"); verify the history rows, ratings, dates — and that a second conversational mention triggers the confirm step rather than a duplicate row.

---

## Definition of done

- All unit + db_integration tests green; pre-commit gate clean; CI green on the PR.
- The five-write-tool invariant pins `add_book_to_history` to the Librarians.
- Both entry points share the single validated write path.
- Docs record the source-of-truth decision and the Phase-4 auto-fill note.
- Live verification (user-gated) confirms both entry points against the real DB.
