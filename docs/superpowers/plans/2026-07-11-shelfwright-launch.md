# Shelfwright Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Shelfwright rebrand: apex `shelfwright.app` on Cloud Run, GitHub repo renamed to `shelfwright` (with the WIF deploy-auth fix), the `shelfwright` PyPI name claimed with a minimal 0.0.1 stub, and Shelfwright product branding in the UI.

**Architecture:** No serving-topology change — the domain maps onto the existing single-origin FastAPI Cloud Run service (`librarian-api`, project `agentic-librarian-prod`, `us-central1`); the #78 auth proxy and SSE carry forward unchanged. Code changes are additive: a self-contained stub package under `packaging/pypi-stub/`, three string-level frontend edits, and an ADR.

**Tech Stack:** gcloud (domain mapping, WIF), gh CLI, Cloudflare DNS, uv/hatchling/twine (PyPI), React+vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-07-11-shelfwright-launch-design.md`
**Operator runbook (companion):** `docs/runbooks/shelfwright-launch.md` — Tasks 4–6 are operator-gated; the runbook explains each console step's what/why.

## Global Constraints

- Canonical host is the **apex** `shelfwright.app`; `www` 301s to it via Cloudflare (never reaches Cloud Run).
- Cloudflare records for the Cloud Run mapping MUST be **DNS only / grey-cloud**; the `www` placeholder is the only proxied (orange-cloud) record.
- Keep both `run.app` OAuth registrations through the transition; add, never remove.
- Product name = **Shelfwright**; chat persona stays **"the Librarian"** — do NOT touch `ChatView.tsx` placeholder, `activityLabels.ts`, `client.ts` error copy, or `RecommendationsView.tsx` hint.
- Code-level Python package rename (`agentic_librarian` → `shelfwright`) is OUT OF SCOPE.
- PyPI stub: name `shelfwright`, version `0.0.1`, no license field (repo has no LICENSE file — don't invent one).
- Ops tasks (4–6) run **interactively with the operator**, not via subagent — they need the user's browser/accounts and are gated on external wait states.
- Repo: commits on the current branch `docs/custom-domain-79` (renamed to `feat/shelfwright-launch` in Task 3); squash-merge PR per repo convention; never push to main.

---

### Task 1: PyPI stub package

**Files:**
- Create: `packaging/pypi-stub/pyproject.toml`
- Create: `packaging/pypi-stub/src/shelfwright/__init__.py`
- Create: `packaging/pypi-stub/README.md`
- Create: `packaging/pypi-stub/.gitignore`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: a built sdist+wheel in `packaging/pypi-stub/dist/` that Task 6 (operator) uploads with twine. Distribution name `shelfwright`, import name `shelfwright`, `shelfwright.__version__ == "0.0.1"`.

- [ ] **Step 1: Create the package source**

`packaging/pypi-stub/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "shelfwright"
version = "0.0.1"
description = "Shelfwright — an agentic reading companion. Placeholder release; the project lives at https://shelfwright.app"
readme = "README.md"
requires-python = ">=3.11"

[project.urls]
Homepage = "https://shelfwright.app"
Repository = "https://github.com/jaydee829/shelfwright"
```

`packaging/pypi-stub/src/shelfwright/__init__.py`:

```python
"""Shelfwright — an agentic reading companion.

This is a placeholder release reserving the package name for the
Shelfwright project (https://shelfwright.app). The application currently
ships as a hosted web app, not an installable library.
"""

__version__ = "0.0.1"
```

`packaging/pypi-stub/README.md`:

```markdown
# Shelfwright

An agentic reading companion — personal book tracking, analysis, and
recommendations powered by an agent mesh.

This 0.0.1 release is a placeholder reserving the package name.
Shelfwright currently ships as a hosted web app:

- **App:** https://shelfwright.app
- **Source:** https://github.com/jaydee829/shelfwright
```

`packaging/pypi-stub/.gitignore`:

```
dist/
```

- [ ] **Step 2: Build**

Run (repo root, PowerShell or bash):
```bash
uv build packaging/pypi-stub --out-dir packaging/pypi-stub/dist
```
Expected: `Successfully built packaging/pypi-stub/dist/shelfwright-0.0.1.tar.gz` and `...shelfwright-0.0.1-py3-none-any.whl`

- [ ] **Step 3: Validate metadata**

```bash
uvx twine check packaging/pypi-stub/dist/*
```
Expected: `PASSED` for both files (warnings about missing license are acceptable; errors are not).

- [ ] **Step 4: Verify the wheel installs and imports**

```bash
uv run --isolated --no-project --with packaging/pypi-stub/dist/shelfwright-0.0.1-py3-none-any.whl \
  python -c "import shelfwright; print(shelfwright.__version__)"
```
Expected output: `0.0.1`

- [ ] **Step 5: Commit**

```bash
git add packaging/pypi-stub/
git commit -m "feat(packaging): minimal shelfwright 0.0.1 stub for PyPI name claim (#79)"
```

---

### Task 2: UI branding — Shelfwright product, Librarian persona

**Files:**
- Modify: `frontend/index.html:7` (`<title>`)
- Modify: `frontend/src/components/TopBar.tsx:18`
- Modify: `frontend/src/components/SignIn.tsx:9-11`
- Test: `frontend/src/components/TopBar.test.tsx` (extend)
- Test: `frontend/src/components/SignIn.test.tsx` (create)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: user-visible strings only; no exported API changes.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/components/TopBar.test.tsx` (inside no describe block — top level, after the existing describe):

```tsx
describe('TopBar branding', () => {
  it('shows the Shelfwright product name', () => {
    render(<TopBar />)
    expect(screen.getByText('Shelfwright')).toBeInTheDocument()
  })
})
```

Create `frontend/src/components/SignIn.test.tsx` (mock pattern copied from TopBar.test.tsx — `vi.mock` MUST come before the component import):

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({ signIn: vi.fn() }),
}))

import SignIn from './SignIn'

describe('SignIn branding', () => {
  it('shows Shelfwright as the product name with the reading-companion subtitle', () => {
    render(<SignIn />)
    expect(screen.getByRole('heading', { name: /Shelfwright/ })).toBeInTheDocument()
    expect(screen.getByText('Your personal reading companion.')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/TopBar.test.tsx src/components/SignIn.test.tsx`
Expected: the two new branding tests FAIL (`Unable to find an element with the text: Shelfwright`); the existing theme-toggle test still PASSES.

- [ ] **Step 3: Make the edits**

`frontend/index.html` line 7: `<title>The Librarian</title>` → `<title>Shelfwright</title>`

`frontend/src/components/TopBar.tsx` line 18:
```tsx
      <span className="topbar-title">Shelfwright</span>
```

`frontend/src/components/SignIn.tsx` lines 9–11 (keep the ✦ gilt marker and the subtitle):
```tsx
        <h1 className="signin-title">
          <span className="signin-gilt" aria-hidden="true">✦</span> Shelfwright
        </h1>
```

Do NOT change: `ChatView.tsx` ("Ask the Librarian…"), `activityLabels.ts`, `api/client.ts` error copy, `RecommendationsView.tsx` — persona strings stay.

- [ ] **Step 4: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: all tests PASS (App.test.tsx mocks every view, so no collateral).

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/src/components/TopBar.tsx frontend/src/components/SignIn.tsx frontend/src/components/TopBar.test.tsx frontend/src/components/SignIn.test.tsx
git commit -m "feat(frontend): Shelfwright product branding, Librarian persona retained (#79)"
```

---

### Task 3: ADR-056 + branch rename + PR

**Files:**
- Modify: `docs/project_notes/decisions.md` (append after ADR-055)

**Interfaces:**
- Consumes: nothing; documents decisions from the spec.
- Produces: the PR that carries Tasks 1–3 + the already-committed spec/runbook.

- [ ] **Step 1: Append ADR-056** (match the existing `### ADR-0NN: Title (date)` / **Context:** / **Decision:** / **Consequences:** format used by ADR-054/055):

```markdown
### ADR-056: Canonical host via Cloud Run domain mapping on apex shelfwright.app (2026-07-11)
**Context:**
- #78 (ADR-055) set `authDomain = window.location.host`, so every serving hostname must be
  registered on the Web OAuth client or new users hit `redirect_uri_mismatch` (prod incident
  2026-07-06, bugs.md). Cloud Run exposes ≥2 hostnames; the durable fix is ONE canonical host.
- The product is now named Shelfwright; the operator registered `shelfwright.app` (Cloudflare).
- Options costed in the 2026-07-07 #79 spec: Firebase Hosting (~$1/mo, but splits the
  single-origin app and routes SSE through Hosting rewrites), Cloud Run domain mapping
  (~$1/mo, least change), LB + serverless NEG (~$19–20/mo floor).
**Decision:**
- **Cloud Run domain mapping on the apex `shelfwright.app`** (service `librarian-api`,
  `us-central1`). Single origin preserved — the #78 auth proxy and SSE `/chat` are untouched;
  `authDomain` simply resolves to the canonical host. `www` 301s to the apex at Cloudflare
  (proxied record + redirect rule; never reaches Cloud Run). Apex records are grey-cloud
  (DNS only) — orange-cloud blocks Google's managed-cert issuance.
- `run.app` hosts stay registered and serving through the transition; only
  `shelfwright.app` is handed out.
- **LB + serverless NEG documented as the trigger-based upgrade path** (SLA / unsupported
  region / CDN / custom TLS policy), additive swap — see the #79 spec.
**Consequences:**
- New-user OAuth registration churn ends: one canonical host, registered once
  (Firebase Authorized domains + Web OAuth redirect URI/JS origin).
- Accepts Cloud Run domain mapping's "Preview" status at friends-and-family scale.
- Rollback = delete the mapping; `run.app` never stops serving.
```

- [ ] **Step 2: Commit**

```bash
git add docs/project_notes/decisions.md
git commit -m "docs(adr): ADR-056 — canonical host via Cloud Run apex domain mapping (#79)"
```

- [ ] **Step 3: Rename the branch and push**

```bash
git branch -m docs/custom-domain-79 feat/shelfwright-launch
git push -u origin feat/shelfwright-launch
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "feat: Shelfwright launch — PyPI stub, UI branding, ADR-056 + domain spec/runbook (#79)" --body "..."
```
Body: summarize the four workstreams, link the spec + runbook, note that domain/rename/upload steps are operator ops tracked in the runbook, and that persona strings are intentionally unchanged. End with the standard generated-with footer. Expected: PR opens against `main`; Gemini review follows per repo convention (reply with commit hashes, squash-merge).

---

### Task 4: Domain mapping ops — **operator-gated, run interactively** ⚠️ start FIRST (24h wait state)

No repo files. Follow runbook Workstream 1 (`docs/runbooks/shelfwright-launch.md`) with the user; this task lists only the exact commands and gates.

- [ ] **Step 1 [You]:** `gcloud domains verify shelfwright.app` → Search Console → TXT record in Cloudflare → Verify. Gate: Search Console shows verified.
- [ ] **Step 2:** create the mapping:
```bash
gcloud beta run domain-mappings create --service librarian-api --domain shelfwright.app --region us-central1 --project agentic-librarian-prod
```
Capture the printed A/AAAA records.
- [ ] **Step 3 [You]:** add the 4 A + 4 AAAA records in Cloudflare DNS, Name `@`, **grey-cloud each one**.
- [ ] **Step 4 [wait]:** poll until cert ACTIVE:
```bash
gcloud beta run domain-mappings describe --domain shelfwright.app --region us-central1 --project agentic-librarian-prod
```
Gate: `CertificateProvisioned: True`; `https://shelfwright.app` loads (sign-in still broken — expected until Step 6).
- [ ] **Step 5 [You]:** Cloudflare `www`: AAAA `www` → `100::` **proxied**, + Redirect Rules template "Redirect from WWW to Root". Gate: `curl -sI https://www.shelfwright.app/library` → `301` + `location: https://shelfwright.app/library`.
- [ ] **Step 6 [You]:** register the host (runbook 1.6–1.7): Firebase → Authentication → Settings → Authorized domains + `shelfwright.app`; Web OAuth client + redirect URI `https://shelfwright.app/__/auth/handler` + JS origin `https://shelfwright.app`. Keep both `run.app` entries.
- [ ] **Step 7 [You]:** verify per runbook 1.8: fresh-incognito never-authorized Google account signs in on `shelfwright.app`; chat streams (SSE); old `run.app` URL still works; `curl -sI https://shelfwright.app/__/auth/iframe.js` → `200` JS content-type.

---

### Task 5: Repo rename + WIF fix — **operator-gated, run interactively**

Follow runbook Workstream 2. Exact commands:

- [ ] **Step 1:** confirm the placeholder is empty, then the user deletes it (web UI, runbook 2.1):
```bash
gh api repos/jaydee829/shelfwright --jq '{size, default_branch}'
```
Expected: `"size": 0`. Gate: user confirms deletion.
- [ ] **Step 2:** rename:
```bash
gh repo rename shelfwright --repo jaydee829/agentic_librarian --yes
```
- [ ] **Step 3:** find every GCP WIF reference to the old repo path — check BOTH the provider condition and SA bindings:
```bash
gcloud iam workload-identity-pools list --location global --project agentic-librarian-prod
gcloud iam workload-identity-pools providers list --workload-identity-pool <POOL> --location global --project agentic-librarian-prod
gcloud iam workload-identity-pools providers describe <PROVIDER> --workload-identity-pool <POOL> --location global --project agentic-librarian-prod
gcloud iam service-accounts get-iam-policy <DEPLOY_SA_EMAIL> --project agentic-librarian-prod
```
Look for `agentic_librarian` in `attributeCondition` and in any `principalSet://…/attribute.repository/…` member.
- [ ] **Step 4:** update each hit to `jaydee829/shelfwright` — e.g. condition:
```bash
gcloud iam workload-identity-pools providers update-oidc <PROVIDER> --workload-identity-pool <POOL> --location global --project agentic-librarian-prod \
  --attribute-condition="assertion.repository == 'jaydee829/shelfwright'"
```
(mirror whatever the existing condition's exact shape is — change ONLY the repo string) and/or re-add the SA binding with the new `principalSet` member and remove the old one.
- [ ] **Step 5 [You]:** manual deploy gate: GitHub → Actions → deploy workflow → Run workflow. Gate: run green incl. smoke test. (`gh run watch` to follow.)
- [ ] **Step 6:** update local remotes — Windows clone and WSL clone:
```bash
git remote set-url origin https://github.com/jaydee829/shelfwright.git
```
- [ ] **Step 7:** sweep repo docs for hard `jaydee829/agentic_librarian` URLs (`grep -r "jaydee829/agentic_librarian" --include="*.md" .`) and update in a follow-up commit if any.

---

### Task 6: PyPI upload — **operator-gated** (after Task 1 merges or from the branch)

Follow runbook Workstream 3.

- [ ] **Step 1 [You]:** pypi.org account + 2FA + API token (scope: entire account — first upload of a new name can't use a project-scoped token). Token is a secret: never in chat/commits.
- [ ] **Step 2 [You]:** upload:
```bash
uvx twine upload packaging/pypi-stub/dist/*
```
(username `__token__`, password = token, entered at the interactive prompt).
- [ ] **Step 3:** verify from a clean env:
```bash
uv run --isolated --no-project --with shelfwright python -c "import shelfwright; print(shelfwright.__version__)"
```
Expected: `0.0.1`. Gate: https://pypi.org/project/shelfwright/ live.
- [ ] **Step 4 [You] (recommended):** replace the account-wide token with a `shelfwright`-scoped one.

---

## Final acceptance (mirrors spec + runbook checklist)

- [ ] `https://shelfwright.app` serves with valid cert; new-user incognito sign-in OK; SSE streams; `run.app` still serves
- [ ] `https://www.shelfwright.app` → 301 → apex
- [ ] Repo `jaydee829/shelfwright`; post-rename `workflow_dispatch` deploy green
- [ ] `pip install shelfwright` → 0.0.1
- [ ] UI: Shelfwright product name, Librarian persona intact; ADR-056 in decisions.md; PR squash-merged
