# Lift 1 — Multi-User Foundation

**Date:** 2026-06-06
**Status:** Approved (brainstormed with user)
**Parent:** Product roadmap (ADR-046, `2026-06-05-product-roadmap-design.md`), Lift 1 row
**Builds on:** Lift 0 walking skeleton (ADR-047, live at `librarian-api` on Cloud Run)

Lift 1 delivers the multi-user *machinery* end to end — schema, identity, isolation,
metering — enforced in production. Nobody but the operator can exercise it until Lift 2
ships the front end; that is by design (the invasive schema work happens while the
surface is small).

## Decisions (locked with user)

| Decision | Choice | Why |
|---|---|---|
| Schema migrations | **Adopt Alembic now** | First-ever ALTER against a live prod DB; every later lift needs more. Pay the setup cost once while the schema is small. |
| Scoping seam | **Context-bound (`contextvars`), never a tool parameter** | SEC-001: agent-supplied arguments are untrusted. The LLM never sees or supplies `user_id`, so prompt injection cannot switch users. |
| Auth verification | **firebase-admin SDK in a FastAPI dependency** | Google maintains signature/expiry/audience checks; ~30 lines of ours; offline verification (no per-request Google calls, no extra IAM). |
| Provisioning | **`SIGNUP_MODE` policy toggle: `invite` \| `open`** | User wants to flip invite-only → open signup easily. One env var on Cloud Run; Lift 3's open signup is flipping it. Default `invite`. |
| Usage granularity | **One row per LLM call** | Lift 3 quotas/billing/BYOK attribution can aggregate any way they want; per-model detail survives multi-vendor conversations; write volume trivial. |
| Cloud Run IAM gate | **Stays on in Lift 1** | No browser needs the service yet. Firebase auth runs beneath it as a second layer; Lift 2 opens the IAM gate when the FE needs direct browser access. |

## Architecture

```
Browser/curl ──Bearer ID token──▶ Cloud Run (librarian-api, IAM gate still on)
                                    │
                                    ▼
                          FastAPI auth dependency (api/auth.py)
                          firebase-admin verify_id_token →
                          user lookup / claim-by-email / SIGNUP_MODE policy
                                    │
                          sets current_user contextvar
                                    │
                ┌───────────────────┼──────────────────────┐
                ▼                   ▼                      ▼
        API endpoints        MCP user-data tools     UsageRecorder
        (/history = mine)    (read context,          (reads same context,
                              fail closed)            best-effort writes)
                                    │
                                    ▼
                          Cloud SQL (user_id columns)
```

Five work streams: (1) Alembic adoption, (2) identity layer, (3) scoping seam,
(4) usage metering, (5) placeholders + docs (`user_credentials`, security.md).

## 1. Schema & migrations (Alembic)

**Setup:** `alembic init`; `env.py` wired to the existing `Base.metadata` and
`DatabaseManager` URL resolution (honors `DATABASE_URL` exactly like the app).

**Migration 0 — baseline:** generates the *current* schema. Fresh databases (CI, new
dev envs) build entirely via `alembic upgrade head`; existing databases (dev, prod) get
`alembic stamp` at baseline once.

**Migration 1 — multi-user** (single transaction):
1. Create `users`; insert the default user with the fixed constant `DEFAULT_USER_ID`
   (checked into code), email `jaydee829@gmail.com`, `firebase_uid` NULL
2. Add `user_id` (nullable) to `reading_history` and `suggestions`
3. Backfill: `UPDATE … SET user_id = DEFAULT_USER_ID` (the 331 history rows + all
   suggestions)
4. Tighten to NOT NULL + FK to `users.id` + index on `user_id`
5. Create `usage` and `user_credentials`

Downgrade is defined for both migrations.

### `users`

| column | type | notes |
|---|---|---|
| id | UUID PK | |
| email | String, unique, NOT NULL | lowercased; the invite key |
| firebase_uid | String, unique, nullable | NULL = invited but never signed in |
| display_name | String, nullable | |
| created_at | DateTime (UTC), NOT NULL | |

Minimal on purpose (YAGNI): no role/billing columns until Lift 3 makes them real.
Nullable `firebase_uid` is what makes invites work — a row can exist before its owner
ever signs in.

### `usage`

| column | type | notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK users.id, NOT NULL | |
| key_source | String, NOT NULL, default `'app'` | `'app'` \| `'byok'`; hard-coded `'app'` until Lift 3 |
| vendor | String, NOT NULL | e.g. `'gemini'`, `'anthropic'` |
| model | String, NOT NULL | |
| input_tokens | Integer, NOT NULL | |
| output_tokens | Integer, NOT NULL | |
| conversation_id | UUID, nullable | |
| created_at | DateTime (UTC), NOT NULL | |

### `user_credentials` (placeholder — BYOK-ready, feature in Lift 3)

| column | type | notes |
|---|---|---|
| user_id | UUID FK users.id, PK part | |
| vendor | String, PK part | |
| encrypted_key | LargeBinary, NOT NULL | KMS ciphertext ONLY — never plaintext |
| kms_key_name | String, NOT NULL | key resource/version used |
| created_at / updated_at | DateTime (UTC), NOT NULL | |

**Zero code paths in Lift 1** — nothing reads or writes this table. The handling
contract lands in `security.md` now: keys are KMS-encrypted before they exist anywhere,
never plaintext at rest, never logged, decrypted only at point of use. Deliberately
**no KMS key ring yet** — provisioning Cloud KMS is a Lift 3 task next to the code that
uses it. Lift 1's guarantee: the schema won't need migration when BYOK arrives.

### Operator tooling

`librarian user invite <email>` — CLI command inserting a `users` row (lowercased
email, NULL `firebase_uid`). Adding a friend is a command, not a psql session. Invites
are CLI-only; no admin endpoints in Lift 1.

## 2. Identity layer (Firebase Auth on FastAPI)

**Firebase project setup** (manual, runbook-documented): Firebase is a console layer
over the *existing* GCP project — add Firebase to `agentic-librarian-prod` (no new
project/billing), enable the **Google** sign-in provider. Email/Password is a console
toggle deferred to Lift 2 with the real sign-in UX.

**Verification dependency** — new module `src/agentic_librarian/api/auth.py`:

```python
async def get_current_user(...) -> User:
    # 1. read Authorization: Bearer; firebase-admin verify_id_token
    #      missing/invalid/expired → 401
    # 2. lookup users by firebase_uid → found: set context, return
    # 3. claim-by-email: token email matches a row with firebase_uid IS NULL
    #      → REQUIRE token's email_verified claim; link uid, set context, return
    # 4. unknown identity → SIGNUP_MODE policy:
    #      invite → 403 "not invited"
    #      open   → create user row, set context, return
```

- Verification is **offline** (JWT signature vs Google's published certs): no extra IAM
  roles, no per-request Google API calls, identical behavior in dev.
- `SIGNUP_MODE` env var on Cloud Run; code default `invite` (fail toward closed).
- The dependency sets the `current_user` contextvar before any endpoint code runs.
- **Endpoint coverage:** every endpoint requires it except `/health` (pure liveness).
  `/health/db` is protected — it leaks DB error strings.

**Two-token collision:** the Cloud Run IAM gate and Firebase both want `Authorization`.
Cloud Run's documented escape hatch: when `X-Serverless-Authorization` is present, the
IAM gate checks that header and passes `Authorization` through to the app:

```bash
curl -H "X-Serverless-Authorization: Bearer $(gcloud auth print-identity-token)" \
     -H "Authorization: Bearer ${FIREBASE_ID_TOKEN}" \
     https://librarian-api-....run.app/history
```

The wrinkle disappears in Lift 2 when the IAM gate opens for browsers.

**Token helper:** a small script (`infra/get_firebase_token.py`, next to
`verify_restore.py`) that obtains a real
Firebase ID token for the operator via Firebase's REST API — used by the runbook's live
verification and the `live`-marked test.

### Real-Firebase risk posture (discussed with user)

Unit tests fake `verify_id_token` — they cover the logic that is *ours*. The residual
risk is integration seams (project config, `aud` claim, ADC on Cloud Run, real claim
shapes). Two mitigations are **part of acceptance**:

1. **Live verification in the rollout runbook** — real token, real prod: 401 / 403 /
   200 checks (Section 6).
2. **A `live`-marked pytest** running real `verify_id_token` against a helper-script
   token — operator-run, excluded from CI like other live tests.

**Logged upgrade path (deferred, not forgotten):** the Firebase Auth **Emulator** can
run SDK-path integration tests in CI if live-auth regressions ever bite. Not adopted
now — it adds a Node-based service to dev container + CI for small marginal coverage
over the two mitigations above.

## 3. Scoping seam (context-bound `current_user`)

New module `src/agentic_librarian/core/user_context.py`:

```python
current_user_id: ContextVar[UUID | None] = ContextVar("current_user_id", default=None)

def get_required_user_id() -> UUID:   # raises if unset — fail CLOSED
def as_user(user_id: UUID): ...       # context manager for entrypoints & tests
```

`contextvars` is Python's standard per-execution-context storage: each FastAPI request
and async task gets an isolated value, so concurrent users cannot bleed into each
other, and MCP tool *signatures do not change*.

**Trusted setters (the only places that set it):**
- the FastAPI auth dependency (after token verification)
- CLI chat / dev conversation entrypoints → `DEFAULT_USER_ID`
- Dagster bulk ingest → gains a target-user knob defaulting to `DEFAULT_USER_ID`
  (covers DEBT-001's "friends send the operator a CSV")

**Scoped (7 user-data tools):** `check_reading_history`, `add_book_to_history`,
`update_reading_status`, `log_suggestion`, `update_suggestion_status`,
`get_unacted_suggestions`, `get_user_trope_preferences` — each calls
`get_required_user_id()` and filters/stamps `user_id`. Context unset → immediate error;
no fall-through to "all rows."

**Untouched (3 catalog tools):** `search_internal_database`, `get_work_details`,
`enrich_and_persist_work` — the shared catalog stays communal (one user's enrichment
grows everyone's library, per the roadmap).

**Security property (extends SEC-001):** tool signatures gain no new parameters, so the
LLM never sees, supplies, or can be injected into choosing a user. Identity rides a
channel the model doesn't touch.

**API endpoints:** `/history` returns the current user's history only. `/works` stays
unscoped (sign-in required; shared catalog).

## 4. Usage metering

Both backends already receive token counts per LLM response (ADK: `usage_metadata`;
Claude Agent SDK: `input_tokens`/`output_tokens`). New component
`src/agentic_librarian/core/usage.py`:

```python
def record_llm_call(vendor: str, model: str, input_tokens: int,
                    output_tokens: int, conversation_id: UUID | None) -> None
```

- Reads `user_id` from the **same context seam** as the tools — one identity channel.
- `key_source='app'` constant until Lift 3 BYOK routing.
- Called from the two existing per-response handling spots (ADK + Claude backends).
- **Best-effort in Lift 1:** a metering failure logs a warning; the conversation
  continues. Hardening to billing-grade is a Lift 3 decision.
- Timing honesty: prod writes no usage rows until Lift 2 deploys the mesh, but every
  dev conversation meters immediately — the pipeline is validated before any friend's
  tokens are on the line.

## 5. Testing

- **Unit:** auth policy vs faked `verify_id_token` (known user; claim-by-email;
  unverified email rejected; invite → 403; open → auto-create); context seam
  (fail-closed; per-tool isolation); usage recorder (records; swallows DB failure with
  a warning); invite CLI.
- **Integration (`db_integration`):** conftest schema setup switches from `create_all`
  to **`alembic upgrade head`** — every CI run proves migrations build a correct schema
  from scratch. A dedicated backfill test seeds pre-migration-shaped data and asserts
  Migration 1 lands rows on `DEFAULT_USER_ID`. Two-real-users isolation tests against
  the real DB. A regression test asserts no agent-exposed tool schema contains a
  `user_id` parameter.
- **`live` marker:** real-Firebase verification (Section 2).
- The existing fast suite stays green: dev/test entrypoints run under
  `as_user(DEFAULT_USER_ID)`.

## 6. Prod rollout (runbook)

Ordering matters: merge auto-deploys (Lift 0 CD), and the new code needs the new
schema — so migrate first. Safe because Lift 0's deployed API is read-only GETs and
keeps working on the migrated schema.

1. **Console:** add Firebase to `agentic-librarian-prod`, enable Google sign-in
2. **Migrate:** from WSL via the Cloud SQL Auth Proxy, `alembic stamp` baseline then
   `alembic upgrade head`, run from the feature branch
3. **Merge** → CD deploys the new code
4. **Verify live:** no token → 401; non-invited account → 403; operator token → 200,
   claim-by-email links the operator to `DEFAULT_USER_ID`, `/history` shows the 331
   events
5. Set `SIGNUP_MODE=invite` explicitly in the Cloud Run env (also the code default)

**No pre-migration backup for Lift 1** (decided with user): prod is byte-identical to
`agentic_librarian_FINAL_20260605_014912.sql.gz` (the Lift 0 API is read-only), and
that dump exists in three places (WSL clone, Windows clone, and
`gs://agentic-librarian-prod-backups` via `06-restore.sh`'s upload). Worst case is
re-running the verified Lift 0 restore. **This reasoning expires at prod's first
write** — from Lift 2 onward, export before every migration; the runbook says so.

## Error-handling summary

- 401 = missing/invalid/expired token; 403 = valid identity, not invited
- Scoped tools fail closed when context is unset
- Metering is best-effort (warn + continue)
- Migration 1 is transactional — failure rolls back fully
- Claim-by-email requires `email_verified` — no claiming invites via unverified emails

## Out of scope (Lift 1)

Front end (Lift 2) · mesh deployment (Lift 2) · quota enforcement, Stripe, BYOK
feature, KMS provisioning (Lift 3) · rate limiting (Lift 3) · Email/Password provider
(Lift 2) · admin endpoints · Firebase Auth Emulator (logged upgrade path) · opening the
Cloud Run IAM gate (Lift 2).

## Docs shipped with this lift

ADR-048 (this design) · `security.md` updates (trust boundary: identity channel;
`user_credentials` handling contract; single-user assumptions begin formally expiring)
· rollout runbook · `key_facts.md` Production section update.
