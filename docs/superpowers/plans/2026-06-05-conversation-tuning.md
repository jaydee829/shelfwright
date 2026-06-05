# Conversation Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut explorer WebSearch volume, make recommendations series-aware, route internal-first, verify discoveries via enrichment, and time-stamp the event trace — per `docs/superpowers/specs/2026-06-05-conversation-tuning-design.md`.

**Architecture:** Prompt edits to the shared specialist instructions (both backends benefit); `enrich_and_persist_work` exposed to BOTH conversational Librarians (Claude `_TOOL_SCHEMAS` + ADK `FunctionTool`); per-subagent `AgentDefinition` knobs (haiku analyst, maxTurns caps); elapsed-seconds prefixes on CLI trace events. No schema changes.

**Tech Stack:** Python 3.11, claude-agent-sdk (`AgentDefinition`), google-adk, pytest.

**Environment notes (this machine):**
- Work in `C:\dev\agentic_librarian`, branch `spec/conversation-tuning`. Run tests via **PowerShell** (Git Bash mangles `/app`):
  `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest <paths> -q -m "not api_dependent and not slow"`
- Git via the Bash tool; ignore "LF will be replaced by CRLF" warnings. Never run `api_dependent` tests.
- **Task 6 runs the authoritative pre-commit gate** (CI parity); earlier tasks run targeted pytest only.

---

### Task 1: Prompt updates — explorer budget, critic series rule, Librarian internal-first + enrichment

**Files:**
- Modify: `src/agentic_librarian/agents/prompts.py`
- Test: `test/unit/test_prompts.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `test/unit/test_prompts.py`:

```python
def test_explorer_has_a_search_budget_and_keeps_its_contract():
    text = prompts.EXPLORER_INSTRUCTION
    assert "SEARCH BUDGET" in text
    assert "ONE broad search" in text
    assert "per-title verification searches" in text
    assert "Never invent" in text  # anti-hallucination stays
    assert '{"books"' in text  # JSON contract consumed by the one-shot pipeline is preserved
    assert "FIRST" in text  # report the series opener for later volumes


def test_critic_has_the_series_rule():
    text = prompts.CRITIC_INSTRUCTION
    assert "SERIES RULE" in text
    assert "FIRST book" in text
    assert "NEXT unread" in text
    assert "check_reading_history" in text


def test_librarian_routes_internal_first_and_enriches_discoveries():
    text = prompts.LIBRARIAN_INSTRUCTION
    assert "ONLY when" in text  # explorer is conditional, not default
    assert "enrich_and_persist_work" in text
    assert "drop that candidate" in text  # hallucination-tolerant by filtering
    assert "SERIES" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_prompts.py -q -m "not api_dependent and not slow"`
Expected: 3 new tests FAIL on the first missing-string assert each.

- [ ] **Step 3: Implement** — in `prompts.py`:

Replace `EXPLORER_INSTRUCTION` with:

```python
EXPLORER_INSTRUCTION = """
            You are a book scout. Use your web search tool to find REAL books that
            match the user's request. Prefer recent or lesser-known titles that are
            unlikely to already be in a standard personal library.

            SEARCH BUDGET: Run ONE broad search, plus AT MOST one refinement search.
            Choose candidates from the snippets you already retrieved. Do NOT run
            additional per-title verification searches — downstream enrichment verifies
            that each candidate actually exists.

            SERIES: If a book you found is a later volume of a series, report the FIRST
            book of that series instead.

            Return a handful (3-5).

            CRITICAL: Only report books that appear in your search results. Never invent
            titles, authors, or details. If the search finds nothing relevant, return an empty list.

            Respond with ONLY a JSON object of this exact shape (no prose, no code fence):
            {"books": [{"title": "...", "author": "...", "why": "one short sentence"}]}
            """
```

Replace `CRITIC_INSTRUCTION` with (series rule inserted as item 6; JUSTIFY renumbered to 7):

```python
CRITIC_INSTRUCTION = """
            You are a book critic. You receive a list of candidate books and target vibes (tropes/styles).
            1. Use 'search_internal_database' with both target tropes and target styles.
            2. Use 'get_work_details' to see deep metadata for candidates.
            3. Use 'check_reading_history' to check re-read eligibility (>2 years).
            4. Rank candidates by similarity to Target Vibes.
            5. APPLY PENALTY: If a candidate matches a 'Session Constraint', lower its rank.

            6. SERIES RULE: If a candidate belongs to a series, recommend the FIRST book —
               unless reading history shows the user is mid-series; then use
               'check_reading_history' on earlier volumes and recommend the NEXT unread one.
               Never recommend a mid/late series entry the user hasn't reached.

            7. JUSTIFY (Trope-RAG): For each recommended book, provide a grounded justification.
               - Anchor your reasoning in the 'name' and 'description' of the top-matching tropes.
               - Include the 'justification' (evidence) from the database to explain how the trope manifests in that specific book.
               - Format: "I recommend [Title] because it features [Trope Name] ([Description]). Specifically, [Justification Evidence]."

            Always end with a clear final recommendation naming the specific book(s) you recommend.

            ONE-SHOT: This is a single-shot request, not a conversation. Always commit to a concrete
            best-effort recommendation from the candidates available — never ask a clarifying question
            and never return an empty response. If the evidence is thin, recommend the closest match
            and say so.
            """
```

Replace `LIBRARIAN_INSTRUCTION` with (comment above it unchanged):

```python
LIBRARIAN_INSTRUCTION = """
You are the Head Librarian. You provide personalized book recommendations and manage reading
history, conversationally, over multiple turns.

DELEGATION STRATEGY (internal-first — the user's enriched catalog is the primary source):
1. Delegate to the 'analyst' agent to turn user vibes into structured trope/style targets and
   session constraints.
2. Use 'get_unacted_suggestions' with target vibes to see if we already have good matches.
3. Delegate to the 'critic' agent to search the internal catalog and rank candidates.
4. Delegate to the 'explorer' agent ONLY when: internal candidates are too few or poorly
   matched; OR the strong internal matches have already been suggested or read; OR the user
   asks for something new / outside their library.
5. ENRICH DISCOVERIES: after the explorer returns, call 'enrich_and_persist_work' on the 2-3
   most promising discoveries (title + author). A null result means the title did not resolve
   (possibly hallucinated) — drop that candidate and continue. Pass surviving candidate ids to
   the 'critic' for final ranking. If nothing survives, recommend from internal candidates.
   - NOTE: Books read >2 years ago are eligible for re-read suggestions.

SERIES: prefer the FIRST book of a series, or the user's NEXT unread volume if they are
mid-series. Never a later entry they haven't reached.

FEEDBACK HANDLING:
- "I read that" -> 'update_reading_status' AND 'update_suggestion_status' (Already Read).
- "Not for me" / "I hate this" -> 'update_suggestion_status' (Dismissed).
- Mood feedback ("not in the mood for X") -> respect it for the rest of the conversation.

When you commit to a recommendation, log it with 'log_suggestion'. Keep replies concise and
conversational; ask at most one clarifying question when the request is too vague to act on.
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_prompts.py -q -m "not api_dependent and not slow"`
Expected: all PASS (including the pre-existing prompt tests — `test_librarian_instruction_delegates_to_the_mesh` still holds: 'analyst'/'explorer'/'critic'/log_suggestion/update_* all remain in the text).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/agents/prompts.py test/unit/test_prompts.py
git commit -m "feat: explorer search budget, critic series rule, internal-first Librarian with discovery enrichment (conversation tuning)"
```

---

### Task 2: Expose `enrich_and_persist_work` on the Claude MCP server

**Files:**
- Modify: `src/agentic_librarian/agents/backends/claude_tools.py` (extend `_TOOL_SCHEMAS`)
- Test: `test/unit/test_claude_tools.py` (append)

- [ ] **Step 1: Write the failing test** — append to `test/unit/test_claude_tools.py`:

```python
def test_enrich_tool_is_exposed():
    # Verification-by-enrichment (conversation tuning spec): the Librarian enriches explorer
    # discoveries; a null return is the existence check failing.
    from agentic_librarian.agents.backends.claude_tools import LIBRARIAN_TOOL_NAMES

    assert "mcp__librarian__enrich_and_persist_work" in LIBRARIAN_TOOL_NAMES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_claude_tools.py -q -m "not api_dependent and not slow"`
Expected: the new test FAILS.

- [ ] **Step 3: Implement** — append to `_TOOL_SCHEMAS` in `claude_tools.py` (after the `update_suggestion_status` tuple; matches `enrich_and_persist_work(title: str, author: str, format: str = "ebook")` in `mcp/server.py:439` — `format` has a default so it is NOT required):

```python
    (
        "enrich_and_persist_work",
        "Verify + enrich a discovered book via the scouts and persist it to the catalog; returns the work id, or null if the title does not resolve.",
        _schema({"title": _STR, "author": _STR, "format": _STR}, required=["title", "author"]),
        mcp_server.enrich_and_persist_work,
    ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_claude_tools.py test/unit/test_claude_backend.py -q -m "not api_dependent and not slow"`
Expected: all PASS — note `test_conversation_options_wire_the_specialist_mesh` keeps passing because `allowed_tools` derives from `LIBRARIAN_TOOL_NAMES` (post-#34), so the new tool is auto-permitted.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/agents/backends/claude_tools.py test/unit/test_claude_tools.py
git commit -m "feat: expose enrich_and_persist_work to the conversational mesh (verification-by-enrichment)"
```

---

### Task 3: ADK parity — Librarian gets the enrich tool + updated inline instruction

**Files:**
- Modify: `src/agentic_librarian/agents/services.py` (import, `LibrarianAgent.__init__` instruction + tools)
- Test: `test/unit/test_agent_services.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `test/unit/test_agent_services.py`:

```python
def test_librarian_has_the_enrich_tool():
    mesh = create_agent_mesh()
    tool_names = [t.name for t in mesh["librarian"].tools]
    assert "enrich_and_persist_work" in tool_names


def test_librarian_instruction_is_internal_first_and_series_aware():
    mesh = create_agent_mesh()
    text = mesh["librarian"].instruction
    assert "ONLY when" in text
    assert "enrich_and_persist_work" in text
    assert "SERIES" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_agent_services.py -q -m "not api_dependent and not slow"`
Expected: both new tests FAIL.

- [ ] **Step 3: Implement** — in `services.py`:

Add `enrich_and_persist_work` to the existing `from agentic_librarian.mcp.server import (...)` block, alphabetically (after `check_reading_history`):

```python
from agentic_librarian.mcp.server import (
    check_reading_history,
    enrich_and_persist_work,
    get_unacted_suggestions,
    get_user_trope_preferences,
    get_work_details,
    log_suggestion,
    search_internal_database,
    update_reading_status,
    update_suggestion_status,
)
```

In `LibrarianAgent.__init__`, replace the `instruction="""..."""` block with (delegation mirrors Task 1's Librarian persona, in ADK AgentTool terminology; the comment above it stays):

```python
            instruction="""
            You are the Head Librarian. You provide personalized book recommendations and manage history.

            DELEGATION STRATEGY (internal-first — the user's enriched catalog is the primary source):
            1. Call the 'Analyst' to turn user vibes into structured targets and session constraints.
            2. Call 'get_unacted_suggestions' with target vibes to see if we have good matches.
            3. Call the 'Critic' to search the internal catalog and rank candidates.
            4. Call the 'Explorer' ONLY when: internal candidates are too few or poorly matched;
               OR the strong internal matches have already been suggested or read; OR the user
               asks for something new / outside their library.
            5. ENRICH DISCOVERIES: after the Explorer returns, call 'enrich_and_persist_work' on the
               2-3 most promising discoveries (title + author). A null result means the title did not
               resolve (possibly hallucinated) — drop that candidate and continue. Pass surviving
               candidates to the 'Critic' for final ranking.
               - NOTE: Books read >2 years ago are eligible for re-read suggestions.

            SERIES: prefer the FIRST book of a series, or the user's NEXT unread volume if they are
            mid-series. Never a later entry they haven't reached.

            FEEDBACK HANDLING:
            - If user says "I read that", use 'update_reading_status' AND 'update_suggestion_status(Already Read)'.
            - If user says "Not for me" or "I hate this", use 'update_suggestion_status(Dismissed)'.
            - If user provides mood feedback ("Not in the mood for X"), pass it to the Analyst/Critic.

            Always log the final result using 'log_suggestion'.
            """,
```

And add the tool to the `tools=[...]` list (after `FunctionTool(get_unacted_suggestions)`):

```python
                FunctionTool(enrich_and_persist_work),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_agent_services.py test/unit/test_agent_runtime.py -q -m "not api_dependent and not slow"`
Expected: all PASS (the pre-existing `test_agent_mesh_delegation_structure` asserts membership, not an exact list, so the added tool doesn't break it — if it DOES assert exact equality, extend its expected list with `enrich_and_persist_work`).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/agents/services.py test/unit/test_agent_services.py
git commit -m "feat: ADK Librarian parity — enrich tool + internal-first, series-aware instruction"
```

---

### Task 4: Subagent knobs — haiku analyst, maxTurns caps

**Files:**
- Modify: `src/agentic_librarian/agents/backends/claude.py` (`_conversation_options`)
- Test: `test/unit/test_claude_backend.py` (extend the existing options test)

- [ ] **Step 1: Write the failing assertions** — in `test/unit/test_claude_backend.py`, extend `test_conversation_options_wire_the_specialist_mesh` by appending:

```python
    # Conversation-tuning knobs: fast analyst, roomy explorer, careful critic.
    assert options.agents["analyst"].model == "haiku"
    assert options.agents["analyst"].maxTurns == 4
    assert options.agents["explorer"].maxTurns == 25
    assert options.agents["explorer"].model is None  # inherit (sonnet)
    assert options.agents["critic"].maxTurns == 8
    assert options.agents["critic"].model is None  # inherit (sonnet)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_claude_backend.py -q -m "not api_dependent and not slow"`
Expected: FAIL on `options.agents["analyst"].model == "haiku"` (currently None).

- [ ] **Step 3: Implement** — in `_conversation_options()` in `claude.py`, add the knobs to the three `AgentDefinition`s:

```python
        "analyst": AgentDefinition(
            description="Turns user vibes into structured trope/style targets and constraints.",
            prompt=prompts.ANALYST_INSTRUCTION,
            tools=["mcp__librarian__get_user_trope_preferences"],
            mcpServers=["librarian"],
            model="haiku",  # easy structured extraction — large latency win (tuning spec)
            maxTurns=4,
        ),
        "explorer": AgentDefinition(
            description="Discovers new candidate books on the web.",
            prompt=prompts.EXPLORER_INSTRUCTION,
            tools=["WebSearch"],
            maxTurns=25,  # runaway guard only — the search BUDGET lives in the prompt
        ),
        "critic": AgentDefinition(
            description="Ranks candidates and writes a grounded Trope-RAG justification.",
            prompt=prompts.CRITIC_INSTRUCTION,
            tools=[
                "mcp__librarian__search_internal_database",
                "mcp__librarian__get_work_details",
                "mcp__librarian__check_reading_history",
            ],
            mcpServers=["librarian"],
            maxTurns=8,
        ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_claude_backend.py test/unit/test_backends.py -q -m "not api_dependent and not slow"`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/agents/backends/claude.py test/unit/test_claude_backend.py
git commit -m "feat: subagent knobs — haiku analyst (4 turns), explorer cap 25, critic cap 8"
```

---

### Task 5: CLI event timestamps

**Files:**
- Modify: `src/agentic_librarian/cli.py` (`_run_repl`)
- Test: `test/unit/test_cli.py` (update two assertions + one new test)

- [ ] **Step 1: Write/adjust the failing tests** — in `test/unit/test_cli.py`:

Add a new test:

```python
def test_events_carry_elapsed_seconds_prefix(monkeypatch, capsys, no_mlflow_dir):
    import re

    fake = _FakeBackend(replies=("ok",))
    monkeypatch.setattr(cli, "get_backend", lambda: fake)
    _feed_stdin(monkeypatch, ["hi", "/quit"])
    cli.main(["--no-mlflow"])
    out = capsys.readouterr().out
    # "  · 0.0s tool: search_internal_database" — elapsed-seconds-into-turn prefix (tuning spec)
    assert re.search(r"· \d+\.\ds tool: search_internal_database", out)
```

And update the two existing assertions that will change shape:
- In `test_repl_two_turns_then_quit`: replace `assert "· tool: search_internal_database" in out` with `assert "tool: search_internal_database" in out`.
- In `test_quiet_suppresses_event_trace_but_still_records`: replace `assert record["events"] == ["tool: search_internal_database"]` with:

```python
    assert len(record["events"]) == 1
    assert record["events"][0].endswith("tool: search_internal_database")  # carries elapsed prefix
```

- [ ] **Step 2: Run tests to verify the new one fails**

Run: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_cli.py -q -m "not api_dependent and not slow"`
Expected: `test_events_carry_elapsed_seconds_prefix` FAILS (no elapsed prefix yet); the two updated tests still pass (their loosened assertions hold on current output).

- [ ] **Step 3: Implement** — in `cli.py` `_run_repl`, replace the `turn_events`/`on_event` block and the per-turn `t0` line:

```python
    turn_events: list[str] = []
    turn_t0 = [time.monotonic()]  # mutable holder: on_event reads the CURRENT turn's start

    def on_event(kind: str, detail: str) -> None:
        entry = f"{time.monotonic() - turn_t0[0]:.1f}s {kind}: {detail}"
        turn_events.append(entry)
        if not args.quiet:
            print(f"  · {entry}")
```

and inside the loop, where `t0 = time.monotonic()` is set for the turn, change to:

```python
            t0 = time.monotonic()
            turn_t0[0] = t0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_cli.py -q -m "not api_dependent and not slow"`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/cli.py test/unit/test_cli.py
git commit -m "feat: elapsed-seconds prefixes on CLI trace events (latency observability)"
```

---

### Task 6: Follow-up log + full gates

**Files:**
- Modify: `docs/project_notes/issues.md` (append entry)

- [ ] **Step 1: Append to `docs/project_notes/issues.md`** (after the CLI-026 entry):

```markdown
### 2026-06-05 - TUNE-027: Conversation tuning round (spec/conversation-tuning)
- **Status**: In Progress (live verification pending)
- **Description**: Explorer search budget (prompt) + maxTurns=25 guard; verification-by-enrichment (enrich_and_persist_work exposed to BOTH conversational Librarians; null = drop candidate, continue); internal-first routing with novelty triggers; series rule in critic+librarians; analyst on haiku; elapsed-seconds event trace. Spec: docs/superpowers/specs/2026-06-05-conversation-tuning-design.md.
- **URL**: N/A (PR pending)
- **Notes**:
    - **Tracked follow-up (schema, ask-first)**: add `series_name`/`series_position` to `works`, populated from Hardcover `featured_series` + a backfill pass over the existing catalog — makes the series rule deterministic instead of model-knowledge-based.
```

- [ ] **Step 2: Run the FULL fast suite**

Run: `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit -q -m "not api_dependent and not slow"`
Expected: ALL PASS (baseline 208 + ~7 new).

- [ ] **Step 3: Run the authoritative pre-commit gate** (CI parity — the pinned ruff differs from the container's bare ruff):

Run (PowerShell): `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest bash -c "git config --global --add safe.directory /app; pip install -q --user pre-commit 2>/dev/null; export PATH=$PATH:~/.local/bin; SKIP=pytest pre-commit run --all-files"`
Expected: all hooks Passed. **If hooks auto-fix files**: the in-container run rewrites mounted files to LF — afterwards only `git diff --stat` shows true content changes; `git add` those, `git checkout -- .` the rest, and include them in the commit below.

- [ ] **Step 4: Commit**

```bash
git add docs/project_notes/issues.md
git commit -m "docs: log TUNE-027 conversation tuning round + series-schema follow-up"
```

- [ ] **Step 5 (manual, user-gated): live verification**

After merge + clone sync, one conversation via `docker exec -it agentic_librarian_app python -m agentic_librarian.cli --backend claude`, expecting: (a) far fewer `WebSearch` events; (b) an internal-only turn that's visibly faster (timestamps now show it); (c) series-opener bias in recs; (d) if the explorer ran, surviving discoveries appear in the DB (`SELECT title FROM works ORDER BY created_at DESC LIMIT 5` — check the column exists first; otherwise verify via `get_work_details`).

---

## Definition of done

- All unit tests green; pre-commit gate clean (CI parity).
- Both backends carry the same behavioral rules (prompts shared; enrich tool on both Librarians).
- Live verification (user-gated) shows fewer searches, faster internal turns, series-aware recs.
- `issues.md` TUNE-027 entry tracks the series-schema follow-up.
