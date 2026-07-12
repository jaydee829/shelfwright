# Runbook — Shelfwright launch (GH #79 + rebrand)

**Spec:** [2026-07-11 Shelfwright launch design](../superpowers/specs/2026-07-11-shelfwright-launch-design.md)
**Audience:** the operator. Every step explains *what you're doing, why, how, and what
"done" looks like*. Steps marked **[Claude]** are done in-session; steps marked **[You]**
need your accounts/browser and can't be delegated.

## The big picture (read this first)

Today the app is served on two ugly Cloud Run hostnames, and because sign-in happens on
*whatever host the user loaded* (the #78 fix), **every** serving hostname must be
registered with Google's OAuth system or new users get `Error 400: redirect_uri_mismatch`
(this bit us in prod on 2026-07-06). This launch gives the app **one canonical address —
`https://shelfwright.app`** — which permanently ends that class of bug, and rebrands the
project (GitHub repo, PyPI name, app title) to match.

Four independent workstreams. The domain one has a wait of up to ~24h in the middle
(Google issuing the SSL certificate), so it starts first and the others run during the wait.

| # | Workstream | Wait states |
|---|---|---|
| 1 | Point `shelfwright.app` at the app | cert issuance, up to ~24h |
| 2 | Rename GitHub repo → `shelfwright` (+ fix deploy auth) | none |
| 3 | Claim `shelfwright` on PyPI | none |
| 4 | UI shows "Shelfwright" (Librarian stays as the chat persona) | normal PR/deploy |

---

## Workstream 1 — `shelfwright.app` → the app

### Step 1.1 — Prove to Google that you own the domain **[You]**

**What:** Google won't map a domain onto Cloud Run until the domain is verified as yours,
via Google Search Console.
**Why:** anti-hijack protection — otherwise anyone could point *your* domain's mapping at
*their* service.
**How:**

1. In a WSL shell with gcloud logged in: `gcloud domains verify shelfwright.app`
   — this opens Search Console in the browser.
2. Search Console gives you a **TXT record** (looks like `google-site-verification=...`).
3. In the **Cloudflare dashboard → shelfwright.app → DNS → Records**, add it:
   Type `TXT`, Name `@`, Content = the string Google gave you.
4. Back in Search Console, click **Verify**. (DNS can take a few minutes to propagate —
   retry if it fails the first time.)

**Done when:** Search Console shows shelfwright.app as a verified property of your
Google account (the same account gcloud/GCP uses).

### Step 1.2 — Create the Cloud Run domain mapping **[Claude or You]**

**What:** tell Cloud Run "requests for `shelfwright.app` go to the `librarian-api`
service", and get back the DNS records Google wants published.
**Why:** this is the actual wiring between the name and the app. It's additive — the
existing `run.app` URLs keep working the whole time, which is also the rollback story.
**How:**

```bash
gcloud beta run domain-mappings create \
  --service librarian-api \
  --domain shelfwright.app \
  --region us-central1 \
  --project agentic-librarian-prod
```

The command prints the DNS records to create — for an apex domain that's **4 `A` records
and 4 `AAAA` records** (Google's frontend IPs). Copy them exactly as printed.

**If it says "Domain mapping to [shelfwright.app] already exists in this region":** this
step is already done — the mapping was created (it's created once, ever). To re-print the
DNS records from the existing mapping, use `describe` instead:

```bash
gcloud beta run domain-mappings describe \
  --domain shelfwright.app --region us-central1 \
  --project agentic-librarian-prod \
  --format='table(status.resourceRecords.type, status.resourceRecords.rrdata)'
```

**Done when:** the command succeeds and you have the list of A/AAAA records in hand.

### Step 1.3 — Publish the DNS records in Cloudflare **[You]**

**What:** add the A/AAAA records from step 1.2 in **Cloudflare → DNS → Records**, each
with Name `@`.
**Why:** this makes `shelfwright.app` resolve to Google's servers, and it's what Google
watches to confirm it can issue the SSL certificate.
**⚠️ The one rule that matters:** set every one of these records to **DNS only (grey
cloud)** — click the orange cloud icon off. Cloudflare's orange-cloud proxy answers with
Cloudflare's own IPs, which blocks Google's certificate issuance and the whole mapping
stalls. (The `www` record in step 1.5 is the deliberate exception.)

**Done when:** 4 A + 4 AAAA records exist for `@`, all grey-cloud.

### Step 1.4 — Wait for the SSL certificate (up to ~24h, usually much faster) **[wait]**

**What:** Google now verifies the DNS and issues a free managed certificate.
**Why the wait:** `.app` domains are HTTPS-only by browser policy (HSTS-preloaded), so
the site literally cannot load until the cert is ACTIVE — don't share the URL yet.
**How to check:**

```bash
gcloud beta run domain-mappings describe \
  --domain shelfwright.app --region us-central1 \
  --project agentic-librarian-prod
```

**Done when:** the describe output shows `CertificateProvisioned: True` (and
`https://shelfwright.app` loads the app in a browser). Sign-in will still fail until
Workstream 1b below — that's expected.

### Step 1.5 — `www` redirect **[You]**

**What:** make `www.shelfwright.app` redirect to `shelfwright.app` (right now `www`
simply doesn't exist — visitors typing it would get a browser error).
**Why Cloudflare serves this, not Cloud Run:** the redirect never has to reach the app,
and doing it at Cloudflare means no second Google certificate or mapping. This is why
this one record IS orange-clouded — Cloudflare must intercept it to answer with the
redirect, and Cloudflare's own certificate covers `www`.
**How:** in the Cloudflare dashboard → shelfwright.app:

1. **DNS → Records:** add `AAAA`, Name `www`, Content `100::`, **Proxied (orange
   cloud)**. (`100::` is a standard "discard" placeholder — the record only exists so
   Cloudflare has something to proxy.)
2. **Rules → Redirect Rules:** use the template **"Redirect from WWW to Root"** (301,
   preserves path/query). Save and deploy.

**Done when:** `curl -sI https://www.shelfwright.app/library` returns `301` with
`location: https://shelfwright.app/library`.

## Workstream 1b — register the new host with sign-in

Steps 1.6 and 1.7 are pure allowlist entries with no dependency on the certificate — they
can be done at any point (e.g. during the cert wait). Only step 1.8, the live sign-in
test, requires the cert to be ACTIVE.

### Step 1.6 — Firebase Authorized domains **[You]**

**What:** Firebase Console (project `agentic-librarian-prod`) → **Authentication →
Settings → Authorized domains** → **Add domain** → `shelfwright.app`.
**Why:** the Firebase SDK refuses to even *start* a sign-in from a domain not on this
list. This is setting 1 of 2 — it is **not** the same as the OAuth client (the #116
lesson: these are two different allowlists and both are required).

### Step 1.7 — Google OAuth client **[You]**

**What:** GCP Console → **APIs & Services → Credentials** → the **Web** OAuth 2.0 client →
add:

- **Authorized redirect URIs:** `https://shelfwright.app/__/auth/handler`
- **Authorized JavaScript origins:** `https://shelfwright.app`

**Why:** when a user signs in, Google redirects the browser back to
`https://<serving-host>/__/auth/handler`. Google rejects any destination not on this
exact list — this is the setting whose absence caused the 2026-07-06 prod incident.
**Keep the two `run.app` entries** that are already there: existing users' bookmarks
still serve from those hosts during the transition.
**Done when:** saved; takes effect within minutes, no redeploy needed.

### Step 1.8 — Verify sign-in end to end **[You]**

**What:** in a **fresh incognito window**, with a Google account that has **never**
authorized this app, sign in at `https://shelfwright.app`.
**Why incognito + never-authorized:** an account that already granted access skips the
Google consent redirect entirely and would falsely pass — this exact blind spot hid the
July incident from smoke tests.
**Also check:** chat streams responses on the new domain (exercises SSE), and the old
`run.app` URL still works.
**Done when:** consent screen → app loads, no `Error 400: redirect_uri_mismatch`.

---

## Workstream 2 — GitHub repo rename

### Step 2.1 — Delete the empty placeholder repo **[You]**

**What:** github.com/jaydee829/shelfwright → **Settings → Danger Zone → Delete this
repository** (GitHub makes you type the name to confirm).
**Why:** the name must be free before the real repo can take it. Double-check it's the
empty one — the deletion is permanent.

### Step 2.2 — Rename the real repo **[Claude or You]**

**What:** `gh repo rename shelfwright --repo jaydee829/agentic_librarian` (or GitHub →
Settings → rename).
**Why it's safe:** GitHub preserves issues/PRs/stars/history and **redirects** all old
URLs and `git` remotes, so nothing breaks on the GitHub side... with one big exception:

### Step 2.3 — Fix the deploy authentication (WIF) **[Claude, verified by You]** ⚠️

**What:** update the GCP Workload Identity Federation config that lets GitHub Actions
deploy — wherever it pins `jaydee829/agentic_librarian` (the WIF provider's **attribute
condition** and/or the deploy service account's `principalSet://…/attribute.repository/…`
IAM binding) must become `jaydee829/shelfwright`.
**Why GitHub's redirect does NOT save us here:** after the rename, the identity token
GitHub sends to Google carries the **new** repo name. Google compares it against the
stored condition string — mismatch means every deploy fails with an auth error until this
is updated. This is the one genuinely breakable step in the rename.
**Done when:** step 2.4 passes.

### Step 2.4 — Verify with a manual deploy **[You]**

**What:** GitHub → Actions → the deploy workflow → **Run workflow** (`workflow_dispatch`),
watch it go green including the smoke test.
**Why:** proves the WIF fix end-to-end before any real change needs to ship.

### Step 2.5 — Update local clones **[Claude / You]**

`git remote set-url origin https://github.com/jaydee829/shelfwright.git` in the Windows
clone (`C:\dev`) — and the same in the **WSL clone** (redirects would keep it working,
but pointing at the real name avoids surprises).

---

## Workstream 3 — claim `shelfwright` on PyPI

### Step 3.1 — PyPI account + token **[You]**

**What:** create an account at pypi.org (2FA is mandatory now), then **Account settings →
API tokens → Add API token**, scope **"Entire account"**.
**Why account-wide scope:** project-scoped tokens can only be created for projects that
already exist — the first-ever upload of a new name needs an account token. (After the
upload you can create a `shelfwright`-scoped token and delete this one.)
**Handle it like a password:** paste it only into the `twine` prompt; don't commit it or
drop it in chat.

### Step 3.2 — Build and upload the stub package **[Claude builds, You upload]**

**What:** the repo gains `packaging/pypi-stub/` — a tiny but real, installable
`shelfwright` 0.0.1 that says what the project is and points at shelfwright.app. Build is
verified with `twine check`; then you run `twine upload dist/*` (username `__token__`,
password = the API token).
**Why a real package instead of an empty reservation:** PyPI's policy (PEP 541) prohibits
squatting on names with placeholder junk; a minimal genuine package with real project
intent is the accepted way to hold a name.
**Done when:** `pip install shelfwright` works from a clean environment.

---

## Workstream 4 — UI branding

Normal PR, no operator steps: tab title / top bar / sign-in screen become **Shelfwright**;
the chat character stays **"the Librarian"** ("Ask the Librarian…", activity phrases).
Ships through the usual review → merge → deploy.

---

## Final acceptance checklist

- [ ] `https://shelfwright.app` loads the app, padlock valid
- [ ] `https://www.shelfwright.app` → 301 → apex
- [ ] Fresh-incognito **new-user** Google sign-in completes on shelfwright.app
- [ ] Chat streams on shelfwright.app; old `run.app` URL still serves
- [ ] `curl -s -o /dev/null -w "%{http_code} %{content_type}" https://shelfwright.app/__/auth/iframe.js` → `200 text/javascript...` (note: must be a GET — the app returns 405 to HEAD/`curl -I`)
- [ ] Repo is `jaydee829/shelfwright`; manual `workflow_dispatch` deploy green
- [ ] `pip install shelfwright` installs the 0.0.1 stub
- [ ] UI shows Shelfwright; ADR-057 recorded
- [ ] Hand out `https://shelfwright.app` 🎉

## Rollback

- **Domain:** `gcloud beta run domain-mappings delete --domain shelfwright.app --region
  us-central1` — instant, `run.app` URLs never stopped working. DNS/OAuth entries can
  stay harmlessly.
- **Repo:** rename back; restore the old string in the WIF condition.
- **PyPI:** nothing to roll back — holding the name is the point.
- **Branding:** revert the PR.
