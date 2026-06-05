# Conversation Tuning — Design

**Date:** 2026-06-05
**Status:** Approved (brainstormed with user after first live CLI sessions)
**Branch:** `spec/conversation-tuning`

## Problem (from live use of the `librarian` CLI, PR #33/#34)

Three observations from real conversations:
1. **The explorer burns an enormous number of WebSearch calls.** Its instruction
   ("only report books that appear in your search results") incentivizes one verification
   search per candidate on top of the broad query, with no stated budget and no cap.
2. **Series-blind recommendations.** A rec was book #4 of a series. The schema has no
   series fields, and no prompt mentions series order.
3. **Recommendations are slow.** Every request can fan out to web discovery (the slowest
   path), the analyst runs on sonnet for easy structured extraction, and there is no
   per-stage timing to show where seconds go.

Also: in the **conversational** mesh, explorer discoveries reach the critic as bare
title/author text — unlike the one-shot pipeline (ADR-040), no enrichment step exists, so
the critic ranks discoveries on model priors, not data.

## User decisions (brainstorm)

1. **Verification = enrichment** ("enrichment is the existence proof"): expose
   `enrich_and_persist_work` to the conversation; the flow must keep working when a
   title fails to resolve (hallucination-tolerant by filtering, not by trusting).
2. **Internal-first routing**, with an explicit novelty guarantee — new works must keep
   surfacing.
3. **Knobs**: explorer `maxTurns=25` (high — runaway guard only; the budget lives in the
   prompt), sonnet. Analyst `model="haiku"`, `maxTurns=4`. Critic sonnet, `maxTurns=8`.

## Design

### 1. Explorer search budget (`prompts.EXPLORER_INSTRUCTION` — shared by both backends)

- Explicit budget: ONE broad search, at most one refinement; choose candidates from the
  snippets already retrieved; do **not** run per-title verification searches —
  downstream enrichment verifies existence.
- Keep the anti-hallucination rule ("only report books that appear in your search
  results; never invent") — it shapes honesty; the system no longer *depends* on it.
- Series note: when a found book is a later volume of a series, report the series opener
  instead.
- Claude side: `AgentDefinition(maxTurns=25)`. (ADK explorer has no equivalent cap; the
  prompt budget applies to both.)

### 2. Verification-by-enrichment (conversation reaches pipeline parity)

- Add `enrich_and_persist_work` to `claude_tools._TOOL_SCHEMAS` (schema:
  `{title: str, author: str, format: str}`, required `["title", "author"]` — `format`
  defaults to "ebook"). Post-#34, `LIBRARIAN_TOOL_NAMES` membership auto-permits it.
- ADK parity: add `FunctionTool(enrich_and_persist_work)` to the ADK Librarian's tools
  (`services.py`).
- `LIBRARIAN_INSTRUCTION` (and the ADK inline instruction) gains an enrichment step:
  after the explorer returns, call `enrich_and_persist_work` on the **2–3 most
  promising** discoveries (not all — each enrichment includes LLM scouts, so cap the
  cost); a **null return means the title didn't resolve (possibly hallucinated): drop
  that candidate and continue**; pass surviving candidate ids to the critic. If nothing
  survives, recommend from internal candidates only.
- Side effect (desired): every surviving discovery persists with tropes/styles/
  embeddings — the catalog grows through conversation, and the critic ranks discoveries
  on real data.

### 3. Internal-first routing with novelty triggers (`LIBRARIAN_INSTRUCTION` + ADK inline)

Delegate to the explorer only when:
(a) internal candidates (unacted suggestions + the critic's catalog search) are too few
or poorly matched; **or**
(b) the strong internal matches have already been suggested or read — the suggestions
log makes this self-balancing: repeated chats exhaust obvious internal picks and tip
back toward discovery; **or**
(c) the user signals novelty ("something new / I haven't heard of").

### 4. Series rule (`CRITIC_INSTRUCTION` — shared, so one-shot + conversation, both backends)

> If a candidate belongs to a series, recommend the FIRST book — unless reading history
> shows the user is mid-series; then use `check_reading_history` on earlier volumes and
> recommend the NEXT unread one. Never recommend a mid/late series entry the user
> hasn't reached.

Echoed briefly in the Librarian instructions.

### 5. Subagent knobs (`claude.py _conversation_options`)

| Agent | model | maxTurns |
|---|---|---|
| analyst | `"haiku"` | 4 |
| explorer | inherit (sonnet) | 25 |
| critic | inherit (sonnet) | 8 |

### 6. Observability: event timestamps

`cli.py`'s `on_event` prefixes each event with elapsed seconds into the turn
(`"  · 12.3s agent: explorer"`), and the recorded transcript event strings carry the
same prefix — so future latency questions start from data. (Recorder schema unchanged:
events remain a list of strings.)

### 7. Tracked follow-up (NOT built now)

`series_name`/`series_position` columns on `works`, populated from Hardcover's
`featured_series` during enrichment + a backfill pass for the existing 326 works —
makes the series rule deterministic. Logged in `issues.md`; schema changes are
ask-first per project boundaries.

## Error handling

- Enrichment failure for a discovery: drop the candidate, continue (REC-023 posture);
  total failure → internal-only recommendation. Never crash the turn.
- All prompt changes preserve the explorer's JSON output contract
  (`{"books": [...]}`) consumed by the one-shot pipeline.

## Testing

1. Offline (TDD where behavior changes): options assertions (knobs per subagent, enrich
   tool exposure in `LIBRARIAN_TOOL_NAMES` and Claude `_conversation_options`; ADK
   Librarian tool list), prompt-content assertions (budget wording, series rule,
   enrichment step, internal-first), CLI event-timestamp format.
2. Existing suites stay green (explorer JSON contract untouched).
3. Live verification (user-gated): one conversation expecting (a) far fewer searches,
   (b) series-opener bias, (c) faster internal-only turn, (d) a surviving discovery
   visible in the DB afterward.

## Out of scope

- Series schema columns + backfill (tracked follow-up).
- `--solo` fast mode (revisit only if latency still hurts after this round).
- MLflow startup timeout, `--once` banner (known minor follow-ups from PR #33).
