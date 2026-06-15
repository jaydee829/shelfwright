# Recommendation Novelty + Re-read Labels — Implementation Plan (A1 + A3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee every recommendation set includes ≥1 genuinely-unread book, and label each recommendation New vs Re-read in both the chat reply and the Recommendations view.

**Architecture:** A deterministic, read-status-aware candidate-curation layer feeds both agent backends. A new batch `get_read_status` annotates candidates; `curate_candidates` partitions them (unread-first, drops books finished <2y ago) and reports `has_unread`; a `get_recommendation_candidates` tool exposes it to the Critic/Librarian. Re-read labels flow from the same read-status data into chat prose and the `/recommendations` payload.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / pgvector; Google ADK + Claude Agent SDK backends; React 19 + Vitest frontend.

**Spec:** `docs/superpowers/specs/2026-06-15-recommendation-novelty-and-reread-labels-design.md`

**Dependency / sequencing:** Builds on **PR #49** (default-3 recs + Librarian `check_reading_history`). Implement **after #49 merges**; rebase this branch (`feat/rec-novelty-and-reread-labels`) onto updated `main` first, since Tasks 4–5 edit the same `prompts.py` / `services.py` instructions #49 touched. All prompt edits below assume #49's text is present.

---

## Test commands (this repo)

Run via the **PowerShell tool** (Git Bash mangles the `C:\…:/app` mount).

- **Unit** (no DB):
  `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest <path> -m "not api_dependent" -q`
- **Integration** (needs the compose DB):
  `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest python -m pytest <path> -q`
- **Frontend** (Windows host, in `C:\dev\agentic_librarian\frontend`): `npm run test`

`claude_agent_sdk` is not in the runtime image, so `test/unit/test_claude_backend.py` and `test_usage_recording_backends.py` fail at import in this container — **pre-existing/environmental, not regressions**. Verify Claude-side wiring by reading the asserts; rely on CI for those.

---

## File Structure

- `src/agentic_librarian/mcp/server.py` — add `reread_eligibility`, `get_read_status`, `get_recommendation_candidates`; refactor `check_reading_history`.
- `src/agentic_librarian/agents/candidates.py` — add `_candidate_view`, `curate_candidates`.
- `src/agentic_librarian/agents/prompts.py` — Critic + Claude-Librarian instruction updates.
- `src/agentic_librarian/agents/services.py` — ADK Critic + Librarian: register `get_recommendation_candidates`; inline instruction updates.
- `src/agentic_librarian/agents/backends/claude_tools.py` — `_TOOL_SCHEMAS` entry; Claude Critic AgentDefinition tool.
- `src/agentic_librarian/agents/backends/claude.py` — add the tool to the Critic subagent's `tools`.
- `src/agentic_librarian/api/recommendations.py` — read-status on the `/recommendations` payload.
- `frontend/src/api/client.ts` — `Recommendation` optional fields.
- `frontend/src/views/RecommendationsView.tsx` (+ `.css`, `.test.tsx`) — badge.
- Tests: `test/unit/test_read_status.py` (new), `test/unit/test_curate_candidates.py` (new), `test/unit/test_prompts.py`, `test/unit/test_agent_services.py`, `test/integration/test_read_status_db.py` (new), `test/integration/test_recommendations_read_status.py` (new).

---

### Task 1: `reread_eligibility` helper + refactor `check_reading_history`

Put the 2-year rule in one place (preserving `> 2.0` behavior) and reuse it.

**Files:**
- Modify: `src/agentic_librarian/mcp/server.py` (the `check_reading_history` region, ~line 273)
- Test: `test/unit/test_read_status.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# test/unit/test_read_status.py
from datetime import date, timedelta

from agentic_librarian.mcp.server import reread_eligibility


def test_reread_eligibility_just_over_two_years_is_candidate():
    completed = date.today() - timedelta(days=int(2.0 * 365.25) + 5)
    is_candidate, years = reread_eligibility(completed)
    assert is_candidate is True
    assert years > 2.0


def test_reread_eligibility_just_under_two_years_is_not_candidate():
    completed = date.today() - timedelta(days=int(2.0 * 365.25) - 5)
    is_candidate, years = reread_eligibility(completed)
    assert is_candidate is False
    assert years < 2.0
```

- [ ] **Step 2: Run it — expect failure**

PowerShell: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_read_status.py -q`
Expected: FAIL — `ImportError: cannot import name 'reread_eligibility'`.

- [ ] **Step 3: Add the helper and refactor**

Add near the top of the tool helpers (above `check_reading_history`):

```python
def reread_eligibility(date_completed: date) -> tuple[bool, float]:
    """The re-read rule in ONE place: a finished book becomes re-read-eligible more than
    2.0 years after completion. Returns (is_re_read_candidate, years_since_completion)."""
    years_since = (date.today() - date_completed).days / 365.25
    return years_since > 2.0, years_since
```

Replace the `if entry:` block inside `check_reading_history` with:

```python
        if entry:
            is_candidate, years_since = reread_eligibility(entry.date_completed)
            return {
                "status": "Read",
                "date_completed": entry.date_completed.isoformat(),
                "years_since_completion": round(years_since, 2),
                "is_re_read_candidate": is_candidate,
                "rating": entry.user_rating,
            }
        return {"status": "Unread", "is_re_read_candidate": True}
```

- [ ] **Step 4: Run tests — expect pass (and no regression in existing history tests)**

PowerShell: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_read_status.py test/unit/test_mcp_tools.py -m "not api_dependent" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/mcp/server.py test/unit/test_read_status.py
git commit -m "feat(rec): reread_eligibility helper; single 2y definition"
```

---

### Task 2: `get_read_status` batch lookup

**Files:**
- Modify: `src/agentic_librarian/mcp/server.py`
- Test: `test/integration/test_read_status_db.py` (create) — DB-backed, follows the seeding pattern in `test/integration/test_mcp_tools.py`.

- [ ] **Step 1: Write the failing integration test**

```python
# test/integration/test_read_status_db.py
from datetime import date, timedelta

from agentic_librarian.core.user_context import as_user
from agentic_librarian.mcp import server as mcp_server


def test_get_read_status_partitions_read_unread_and_recent(scoped_db, seeded_work_ids):
    # seeded_work_ids fixture: returns {"old_read": id, "recent_read": id, "unread": id}
    # with a read >2y ago, a read <2y ago, and an unread catalog work for THIS user.
    ids = seeded_work_ids
    with as_user(scoped_db.user_id):
        status = mcp_server.get_read_status(list(ids.values()))

    assert status[ids["old_read"]]["status"] == "Read"
    assert status[ids["old_read"]]["is_re_read_candidate"] is True
    assert status[ids["recent_read"]]["status"] == "Read"
    assert status[ids["recent_read"]]["is_re_read_candidate"] is False
    assert status[ids["unread"]]["status"] == "Unread"
    assert status[ids["unread"]]["is_re_read_candidate"] is True
```

> If `scoped_db` / `seeded_work_ids` fixtures don't already exist with these shapes, add them to `test/integration/conftest.py` modeled on the existing `scoped_db` (see `test_user_isolation.py`) and `seeded_work_id` (see `test_mcp_tools.py`): create one user, three works each with an `Edition`, and `ReadingHistory` rows dated `date.today() - timedelta(days=...)` for the two read works.

- [ ] **Step 2: Run it — expect failure**

PowerShell: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest python -m pytest test/integration/test_read_status_db.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'get_read_status'`.

- [ ] **Step 3: Implement `get_read_status`**

Add to `mcp/server.py` (after `check_reading_history`):

```python
@mcp.tool()
def get_read_status(work_ids: list[str]) -> dict:
    """Batch read-status for the current user across many works (one query). For each given
    work id: {"status": "Read"|"Unread", "last_read": ISO|None, "years_since": float|None,
    "is_re_read_candidate": bool, "rating": int|None}. Works with no read row are "Unread".
    Used by the recommendation curation to annotate candidates without N per-title calls."""
    user_id = get_required_user_id()
    by_uuid: dict = {}
    for wid in work_ids:
        u = _parse_uuid(wid)
        if u is not None:
            by_uuid[u] = wid
    result: dict[str, dict] = {
        wid: {
            "status": "Unread",
            "last_read": None,
            "years_since": None,
            "is_re_read_candidate": True,
            "rating": None,
        }
        for wid in work_ids
    }
    if not by_uuid:
        return result
    with db_manager.get_session() as session:
        rows = (
            session.query(ReadingHistory, Edition.work_id)
            .join(Edition)
            .filter(Edition.work_id.in_(list(by_uuid.keys())), ReadingHistory.user_id == user_id)
            .order_by(ReadingHistory.date_completed.desc())
            .all()
        )
        seen: set = set()
        for rh, work_uuid in rows:
            if work_uuid in seen:  # rows are date-desc; first row per work is the latest read
                continue
            seen.add(work_uuid)
            is_candidate, years_since = reread_eligibility(rh.date_completed)
            result[by_uuid[work_uuid]] = {
                "status": "Read",
                "last_read": rh.date_completed.isoformat(),
                "years_since": round(years_since, 2),
                "is_re_read_candidate": is_candidate,
                "rating": rh.user_rating,
            }
    return result
```

- [ ] **Step 4: Run it — expect pass**

Same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/mcp/server.py test/integration/test_read_status_db.py test/integration/conftest.py
git commit -m "feat(rec): get_read_status batch read-status by work id"
```

---

### Task 3: `curate_candidates` (read-status-aware, novelty-balanced)

**Files:**
- Modify: `src/agentic_librarian/agents/candidates.py`
- Test: `test/unit/test_curate_candidates.py` (create) — pure unit, monkeypatch the three data fns.

- [ ] **Step 1: Write the failing test**

```python
# test/unit/test_curate_candidates.py
from agentic_librarian.agents import candidates


def _patch(monkeypatch, internal, unacted, status):
    monkeypatch.setattr(candidates, "search_internal_database", lambda **kw: internal)
    monkeypatch.setattr(candidates, "get_unacted_suggestions", lambda **kw: unacted)
    monkeypatch.setattr(candidates, "get_read_status", lambda ids: status)


def test_curate_orders_unread_first_and_drops_recent_reads(monkeypatch):
    internal = [
        {"id": "w-old", "title": "Old Read", "authors": ["A"], "genres": ["sf"], "description": "d1"},
        {"id": "w-recent", "title": "Recent Read", "authors": ["B"], "genres": [], "description": "d2"},
        {"id": "w-new", "title": "Fresh", "authors": ["C"], "genres": [], "description": "d3"},
    ]
    status = {
        "w-old": {"status": "Read", "last_read": "2019-01-01", "is_re_read_candidate": True, "rating": 5},
        "w-recent": {"status": "Read", "last_read": "2025-12-01", "is_re_read_candidate": False, "rating": None},
        "w-new": {"status": "Unread", "last_read": None, "is_re_read_candidate": True, "rating": None},
    }
    _patch(monkeypatch, internal, [], status)

    out = candidates.curate_candidates(["cozy"], ["lyrical"])

    ids = [c["id"] for c in out["candidates"]]
    assert ids == ["w-new", "w-old"]  # unread first; recent read dropped
    assert out["has_unread"] is True
    assert out["unread_count"] == 1 and out["reread_count"] == 1
    new_card = out["candidates"][0]
    assert new_card["read_status"] == "new" and new_card["last_read"] is None
    old_card = out["candidates"][1]
    assert old_card["read_status"] == "reread" and old_card["last_read"] == "2019-01-01" and old_card["rating"] == 5


def test_curate_reports_no_unread_when_all_reads_eligible(monkeypatch):
    internal = [{"id": "w1", "title": "T", "authors": [], "genres": [], "description": ""}]
    status = {"w1": {"status": "Read", "last_read": "2018-01-01", "is_re_read_candidate": True, "rating": None}}
    _patch(monkeypatch, internal, [], status)

    out = candidates.curate_candidates(["x"], None)
    assert out["has_unread"] is False
    assert [c["id"] for c in out["candidates"]] == ["w1"]


def test_curate_falls_back_to_unacted_when_search_empty(monkeypatch):
    unacted = [{"id": "s1", "title": "Prior Pick", "justification": "you might like this"}]
    status = {"s1": {"status": "Unread", "last_read": None, "is_re_read_candidate": True, "rating": None}}
    _patch(monkeypatch, [], unacted, status)

    out = candidates.curate_candidates([], [])
    assert out["has_unread"] is True
    assert out["candidates"][0]["title"] == "Prior Pick"
    assert out["candidates"][0]["description"] == "you might like this"  # justification -> description
```

- [ ] **Step 2: Run it — expect failure**

PowerShell: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_curate_candidates.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'curate_candidates'`.

- [ ] **Step 3: Implement**

Edit `candidates.py` imports (top):

```python
from agentic_librarian.mcp.server import get_read_status, get_unacted_suggestions, search_internal_database
```

Append:

```python
def _candidate_view(r: dict) -> dict:
    """Normalize a row from either source into a common candidate shape.
    search_internal_database -> id/title/authors/genres/description;
    get_unacted_suggestions -> id/title/justification (no authors/genres)."""
    return {
        "id": r.get("id"),
        "title": r.get("title"),
        "authors": r.get("authors") or [],
        "genres": r.get("genres") or [],
        "description": r.get("description") or r.get("justification") or "",
    }


def curate_candidates(target_tropes: list[str], target_styles: list[str] = None, limit: int = 10) -> dict:
    """Deterministic, read-status-aware candidate set for recommendations (spec A1/A3).
    Unions internal vector matches + prior unacted (unread) suggestions, annotates each with
    read status, DROPS books finished <2y ago, orders unread-first, and reports has_unread so
    the caller can fall back to the Explorer for a fresh discovery."""
    rows = search_internal_database(target_tropes=target_tropes, target_styles=target_styles, limit=limit)
    rows += get_unacted_suggestions(target_tropes=target_tropes, target_styles=target_styles, limit=limit)
    by_id: dict[str, dict] = {}
    for r in rows:
        wid = r.get("id")
        if wid and wid not in by_id:
            by_id[wid] = r
    if not by_id:
        return {"candidates": [], "has_unread": False, "unread_count": 0, "reread_count": 0}

    status = get_read_status(list(by_id.keys()))
    unread: list[dict] = []
    reread: list[dict] = []
    for wid, r in by_id.items():
        st = status.get(wid) or {}
        if st.get("status") == "Read":
            if not st.get("is_re_read_candidate"):
                continue  # finished <2y ago: neither new nor a valid re-read — drop
            reread.append(
                {**_candidate_view(r), "read_status": "reread", "last_read": st.get("last_read"), "rating": st.get("rating")}
            )
        else:
            unread.append({**_candidate_view(r), "read_status": "new", "last_read": None, "rating": None})

    candidates = (unread + reread)[:limit]
    return {
        "candidates": candidates,
        "has_unread": bool(unread),
        "unread_count": len(unread),
        "reread_count": len(reread),
    }
```

- [ ] **Step 4: Run it — expect pass**

Same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/agents/candidates.py test/unit/test_curate_candidates.py
git commit -m "feat(rec): curate_candidates - unread-first, drop recent reads, has_unread"
```

---

### Task 4: `get_recommendation_candidates` tool + backend wiring

**Files:**
- Modify: `src/agentic_librarian/mcp/server.py`, `src/agentic_librarian/agents/services.py`, `src/agentic_librarian/agents/backends/claude_tools.py`, `src/agentic_librarian/agents/backends/claude.py`
- Test: `test/unit/test_agent_services.py`

- [ ] **Step 1: Write the failing wiring tests**

Append to `test/unit/test_agent_services.py`:

```python
def test_critic_has_the_recommendation_candidates_tool():
    mesh = create_agent_mesh()
    assert "get_recommendation_candidates" in [t.name for t in mesh["critic"].tools]


def test_librarian_has_the_recommendation_candidates_tool():
    mesh = create_agent_mesh()
    assert "get_recommendation_candidates" in [t.name for t in mesh["librarian"].tools]
```

- [ ] **Step 2: Run — expect failure**

PowerShell: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_agent_services.py -q`
Expected: FAIL on the two new tests.

- [ ] **Step 3: Implement the tool + wiring**

In `mcp/server.py` add (lazy import avoids the candidates↔server cycle):

```python
@mcp.tool()
def get_recommendation_candidates(target_tropes: list[str], target_styles: list[str] = None, limit: int = 10) -> dict:
    """Read-status-aware, novelty-balanced candidates for a recommendation. Returns
    {"candidates":[{id,title,authors,genres,description,read_status,last_read,rating}],
    "has_unread","unread_count","reread_count"}. candidates is unread-first and excludes books
    finished <2y ago. If has_unread is false, delegate to the Explorer for a fresh discovery.
    This is the Critic's primary catalog tool."""
    from agentic_librarian.agents.candidates import curate_candidates

    return curate_candidates(target_tropes, target_styles, limit=limit)
```

In `agents/services.py`: add `get_recommendation_candidates` to the import block from `agentic_librarian.mcp.server`; add `FunctionTool(get_recommendation_candidates)` to **CriticAgent** `tools` (keep the others) and to **LibrarianAgent** `tools` (after `check_reading_history` / `get_unacted_suggestions`).

In `agents/backends/claude_tools.py`: add a `_TOOL_SCHEMAS` entry (so it joins `LIBRARIAN_TOOL_NAMES`, granting the conversational Librarian access):

```python
    (
        "get_recommendation_candidates",
        "Read-status-aware, novelty-balanced catalog candidates (unread-first; has_unread flag).",
        _schema({"target_tropes": _STR_ARRAY, "target_styles": _STR_ARRAY, "limit": _INT}, required=["target_tropes"]),
        mcp_server.get_recommendation_candidates,
    ),
```

In `agents/backends/claude.py`: add `"mcp__librarian__get_recommendation_candidates"` to the Critic subagent `AgentDefinition.tools` list (`_conversation_options`, alongside `search_internal_database`).

- [ ] **Step 4: Run — expect pass**

Same command as Step 2. Expected: PASS. Also run `test/unit/test_curate_candidates.py` to confirm no cycle on import.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/mcp/server.py src/agentic_librarian/agents/services.py src/agentic_librarian/agents/backends/claude_tools.py src/agentic_librarian/agents/backends/claude.py test/unit/test_agent_services.py
git commit -m "feat(rec): get_recommendation_candidates tool wired to both backends"
```

---

### Task 5: Prompt updates — ≥1-new guarantee + re-read tagging (both backends)

Assumes #49 text is present (Critic "Recommend 3 books by default…"; Librarian step 6 "PRESENT 3 recommendations by default…").

**Files:**
- Modify: `src/agentic_librarian/agents/prompts.py` (CRITIC_INSTRUCTION, LIBRARIAN_INSTRUCTION), `src/agentic_librarian/agents/services.py` (inline Librarian instruction)
- Test: `test/unit/test_prompts.py`, `test/unit/test_agent_services.py`

- [ ] **Step 1: Write the failing guard tests**

Append to `test/unit/test_prompts.py`:

```python
def test_critic_uses_curated_candidates_and_guarantees_novelty():
    text = prompts.CRITIC_INSTRUCTION
    assert "get_recommendation_candidates" in text
    assert "at least one" in text.lower() and "new" in text.lower()
    assert "[New]" in text and "[Re-read" in text  # the per-rec tag format


def test_librarian_guarantees_one_new_and_falls_back_to_explorer():
    text = prompts.LIBRARIAN_INSTRUCTION
    assert "get_recommendation_candidates" in text
    assert "has_unread" in text  # explorer fallback keys off the flag
    assert "at least one" in text.lower() and "new" in text.lower()
    assert "[New]" in text and "[Re-read" in text
```

Append to `test/unit/test_agent_services.py`:

```python
def test_adk_librarian_guarantees_one_new_and_tags_rereads():
    mesh = create_agent_mesh()
    text = mesh["librarian"].instruction
    assert "get_recommendation_candidates" in text
    assert "has_unread" in text
    assert "at least one" in text.lower() and "new" in text.lower()
    assert "[New]" in text and "[Re-read" in text
```

- [ ] **Step 2: Run — expect failure**

PowerShell: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_prompts.py test/unit/test_agent_services.py -q`
Expected: FAIL on the three new tests.

- [ ] **Step 3: Edit the prompts**

In `prompts.py` **CRITIC_INSTRUCTION**, replace step 1 and the closing recommendation line.

Replace:
```
            1. Use 'search_internal_database' with both target tropes and target styles.
```
with:
```
            1. Use 'get_recommendation_candidates' with the target tropes and styles to get
               read-status-tagged, novelty-balanced candidates (unread-first, with has_unread).
               You may also use 'search_internal_database' for extra nuance.
```

Replace the closing line (added in #49):
```
            Always end with a clear final recommendation. Recommend 3 books by default (unless the
            user asked for a specific number); if fewer than 3 sound candidates exist, recommend as
            many as are genuinely good rather than padding the list with weak matches.
```
with:
```
            Always end with a clear final recommendation. Recommend 3 books by default (unless the
            user asked for a specific number) and ALWAYS include at least one candidate whose
            read_status is "new"; if fewer than 3 sound candidates exist, recommend as many as are
            genuinely good rather than padding the list with weak matches.
            TAG each recommendation using the candidate's read_status: "[New]" for unread, or
            "[Re-read: last read YYYY]" using its last_read date.
```

In `prompts.py` **LIBRARIAN_INSTRUCTION**, replace step 2 and step 6 (#49) of the DELEGATION STRATEGY.

Replace:
```
2. Use 'get_unacted_suggestions' with target vibes to see if we already have good matches.
```
with:
```
2. Use 'get_recommendation_candidates' with target vibes to get read-status-tagged, novelty-
   balanced candidates plus a has_unread flag.
```

Replace step 6 (from #49):
```
6. PRESENT 3 recommendations by default unless the user asks for a different number; do not
   return a single pick when more good matches are available.
```
with:
```
6. PRESENT 3 recommendations by default unless the user asks for a different number, and ALWAYS
   include at least one whose read_status is "new". If has_unread is false, delegate to the
   'explorer' for a fresh discovery, enrich it, and use it as the new pick. TAG each as "[New]"
   or "[Re-read: last read YYYY]" from its read_status/last_read.
```

> Keep the existing `get_unacted_suggestions` mention elsewhere? `test_librarian_instruction_delegates_to_the_mesh` (test_prompts.py) asserts `"get_unacted_suggestions" in text`. To keep it green, leave one reference: append to step 2 — `(get_recommendation_candidates wraps get_unacted_suggestions + the catalog search)`. This keeps the assertion satisfied and documents the relationship.

In `services.py` inline Librarian instruction, apply the SAME two edits to its step 2 and step 6 (12-space indentation; mirror the wording above).

- [ ] **Step 4: Run — expect pass (full prompt/services unit set)**

PowerShell: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_prompts.py test/unit/test_agent_services.py -m "not api_dependent" -q`
Expected: PASS (including the pre-existing delegation/`get_unacted_suggestions` test).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/agents/prompts.py src/agentic_librarian/agents/services.py test/unit/test_prompts.py test/unit/test_agent_services.py
git commit -m "feat(rec): instruct >=1-new + re-read tagging via curated candidates (both backends)"
```

---

### Task 6: Re-read status on the `/recommendations` payload (A3, Rec-view)

A card is "reread" if the user has any read row for the suggested work, else "new". (Eligibility isn't needed for the card label — it shows whether you've read it, with last-read year + rating.)

**Files:**
- Modify: `src/agentic_librarian/api/recommendations.py`
- Test: `test/integration/test_recommendations_read_status.py` (create)

- [ ] **Step 1: Write the failing integration test**

```python
# test/integration/test_recommendations_read_status.py
# Follows the auth + seeding pattern in test/integration/test_books_api.py / test_api_history_db.py.
# Seed: one user; two works each with a Suggestion (status "Suggested"); a ReadingHistory row for
# ONE of them. Then GET /recommendations as that user.

def test_recommendations_payload_carries_read_status(client_for_seeded_user, seeded_recs):
    resp = client_for_seeded_user.get("/recommendations")
    assert resp.status_code == 200
    by_title = {r["title"]: r for r in resp.json()}

    read = by_title[seeded_recs["read_title"]]
    assert read["read_status"] == "reread"
    assert read["last_read"] == seeded_recs["read_date"]  # ISO string
    assert read["rating"] == seeded_recs["read_rating"]

    fresh = by_title[seeded_recs["unread_title"]]
    assert fresh["read_status"] == "new"
    assert fresh["last_read"] is None
    assert fresh["rating"] is None
```

> Build `client_for_seeded_user` / `seeded_recs` on the existing recommendations integration fixtures (see `test/integration/test_books_api.py` for the authed `TestClient` + `set_db_manager` injection, and `test_api_history_db.py` for seeding works/editions/history).

- [ ] **Step 2: Run — expect failure**

PowerShell: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest python -m pytest test/integration/test_recommendations_read_status.py -q`
Expected: FAIL — `KeyError: 'read_status'`.

- [ ] **Step 3: Implement**

In `api/recommendations.py`, extend imports:

```python
from agentic_librarian.db.models import Edition, ReadingHistory, Suggestions, Work, WorkContributor
```

In `get_recommendations`, after `rows = (...).all()` and before the return, build a read-status map and include it per item:

```python
        work_ids = [s.work_id for s in rows]
        read_by_work: dict = {}
        if work_ids:
            rh_rows = (
                session.query(ReadingHistory, Edition.work_id)
                .join(Edition)
                .filter(Edition.work_id.in_(work_ids), ReadingHistory.user_id == user.id)
                .order_by(ReadingHistory.date_completed.desc())
                .all()
            )
            for rh, wid in rh_rows:
                read_by_work.setdefault(wid, rh)  # first per work = latest (date-desc)

        def _read_fields(work_id):
            rh = read_by_work.get(work_id)
            if rh is None:
                return {"read_status": "new", "last_read": None, "rating": None}
            return {
                "read_status": "reread",
                "last_read": rh.date_completed.isoformat(),
                "rating": rh.user_rating,
            }

        return [
            {
                "id": str(s.id),
                "work_id": str(s.work_id),
                "title": s.work.title,
                "authors": [c.author.name for c in s.work.contributors if c.role == "Author"],
                "justification": s.justification,
                "context": s.context,
                "suggested_at": s.suggested_at.isoformat() if s.suggested_at else None,
                "status": s.status,
                **_read_fields(s.work_id),
            }
            for s in rows
        ]
```

- [ ] **Step 4: Run — expect pass**

Same command as Step 2, plus the existing recs tests:
`... python -m pytest test/integration/test_recommendations_read_status.py test/unit/test_api_history.py -q` (run the existing recommendations test module too if present).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/api/recommendations.py test/integration/test_recommendations_read_status.py
git commit -m "feat(rec): /recommendations payload carries read_status/last_read/rating"
```

---

### Task 7: Frontend — New / Re-read badge on rec cards

**Files:**
- Modify: `frontend/src/api/client.ts`, `frontend/src/views/RecommendationsView.tsx`, `frontend/src/views/RecommendationsView.css`
- Test: `frontend/src/views/RecommendationsView.test.tsx`

Run frontend tests on the Windows host: `npm run test` in `C:\dev\agentic_librarian\frontend`.

- [ ] **Step 1: Write the failing test**

Add to `RecommendationsView.test.tsx` (follow the existing mock of `../api/client`; remember vitest-4 `...Once` mock-leak rule):

```tsx
it('renders a New badge for unread recs and a Re-read badge for read ones', async () => {
  vi.mocked(client.getRecommendations).mockResolvedValueOnce([
    { id: '1', work_id: 'w1', title: 'Fresh Pick', authors: ['A'], justification: null,
      context: null, suggested_at: null, status: 'Suggested', read_status: 'new', last_read: null, rating: null },
    { id: '2', work_id: 'w2', title: 'Old Favorite', authors: ['B'], justification: null,
      context: null, suggested_at: null, status: 'Suggested', read_status: 'reread', last_read: '2019-05-01', rating: 4 },
  ])
  render(<RecommendationsView />, { wrapper: MemoryRouter })

  expect(await screen.findByText('New')).toBeInTheDocument()
  expect(screen.getByText(/Re-read/)).toBeInTheDocument()
  expect(screen.getByText(/2019/)).toBeInTheDocument()
})
```

- [ ] **Step 2: Run — expect failure**

`npm run test` → FAIL (no badge rendered; type error on `read_status`).

- [ ] **Step 3: Implement**

In `client.ts`, extend `Recommendation`:

```typescript
export interface Recommendation {
  id: string
  work_id: string
  title: string
  authors: string[]
  justification: string | null
  context: string | null
  suggested_at: string | null
  status: string
  read_status?: 'new' | 'reread'
  last_read?: string | null
  rating?: number | null
}
```

In `RecommendationsView.tsx`, add a badge helper and render it in `rec-head`:

```tsx
function ReadBadge({ r }: { r: Recommendation }) {
  if (r.read_status === 'reread') {
    const year = r.last_read ? new Date(r.last_read).getFullYear() : null
    const stars = r.rating ? ` · ${'★'.repeat(r.rating)}` : ''
    return <span className="rec-badge reread">{year ? `Re-read · ${year}${stars}` : `Re-read${stars}`}</span>
  }
  if (r.read_status === 'new') return <span className="rec-badge new">New</span>
  return null
}
```

Render inside `rec-head` (after the authors span):

```tsx
            <div className="rec-head">
              <span className="rec-title">{r.title}</span>
              <span className="rec-authors">{r.authors.join(', ')}</span>
              <ReadBadge r={r} />
            </div>
```

In `RecommendationsView.css`, add minimal badge styling:

```css
.rec-badge { font-size: 0.75rem; padding: 0.1rem 0.4rem; border-radius: 0.5rem; margin-left: 0.5rem; }
.rec-badge.new { background: #1f6f43; color: #fff; }
.rec-badge.reread { background: #5a4a8a; color: #fff; }
```

- [ ] **Step 4: Run — expect pass**

`npm run test` → PASS. Then `npm run build` and `npm run lint` to confirm the type + lint are clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/views/RecommendationsView.tsx frontend/src/views/RecommendationsView.css frontend/src/views/RecommendationsView.test.tsx
git commit -m "feat(rec): New / Re-read badge on recommendation cards"
```

---

### Task 8: Full-suite verification

- [ ] **Step 1: Backend unit suite**

PowerShell: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit -m "not api_dependent" -q`
Expected: all pass except the pre-existing `claude_agent_sdk` import failures in `test_claude_backend.py` / `test_usage_recording_backends.py` (environmental — note them, don't "fix").

- [ ] **Step 2: Backend integration suite**

PowerShell: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest python -m pytest test/integration -m "not api_dependent" -q`
Expected: pass (live `api_dependent` scout tests deselected).

- [ ] **Step 3: Frontend**

In `frontend/`: `npm run test && npm run build && npm run lint` → all green.

- [ ] **Step 4: Lint (CI parity)**

Ruff runs in CI (not in the runtime image). Keep added lines ≤120 cols; match surrounding format. If a clean `python:3.11-slim` + `pip install ruff==0.15.16` is available, run `ruff check` + `ruff format --check` on the changed `src/`+`test/` files; otherwise rely on CI.

- [ ] **Step 5: Open the PR**

After rebasing onto post-#49 `main`, push and open the PR (Gemini reviews; reply with commit hash; squash-merge "(#N)").

---

## Self-Review

**Spec coverage:** A1 pool guarantee (Tasks 2–4 + 5), tiered new-pick source / Explorer-on-empty (Task 5 `has_unread`), 2y threshold preserved (Task 1), drop <2y reads (Task 3), A3 chat tags (Task 5) + Rec-card badge (Tasks 6–7), error degradation (Task 3 empty-search test; `get_read_status` fail-closed inherits `get_required_user_id`). One-shot pipeline inherits the curated tool via the shared CRITIC_INSTRUCTION + CriticAgent tool (Tasks 4–5) — no separate pipeline edit needed (Explorer already runs unconditionally there).

**Placeholder scan:** Integration fixtures (`scoped_db`/`seeded_work_ids`/`client_for_seeded_user`/`seeded_recs`) reference existing conftest patterns and say where to model them — concrete shapes given, not "TODO".

**Type consistency:** candidate dict keys (`read_status`/`last_read`/`rating`/`has_unread`) match across `curate_candidates` (Task 3), the tool docstring (Task 4), prompts (Task 5), and the frontend type (Task 7). The `/recommendations` field names (Task 6) match the frontend `Recommendation` additions (Task 7).
