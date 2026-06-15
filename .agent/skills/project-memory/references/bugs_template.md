# Bug Log

This file tracks project bugs, their root causes, solutions, and prevention strategies.

## Templates

### YYYY-MM-DD - Brief Bug Description
- **Issue**: What went wrong (the observable symptom)
- **Root Cause**: Why it happened (the underlying cause, not the symptom)
- **Solution**: How it was fixed (specific change, file, or approach)
- **Prevention**: How to avoid it in the future

## Log

<!--
Add new entries below in reverse-chronological order (newest first).

Example:

### 2026-01-15 - Connection Refused from Database
- **Issue**: App could not reach Postgres; `psql: connection refused` on startup.
- **Root Cause**: Container reached the DB on `localhost` instead of the compose service host.
- **Solution**: Point `DATABASE_URL` at the `db` service host inside the container network.
- **Prevention**: Document container vs. host DB hosts in key_facts.md; assert connectivity in a startup health check.
-->

## Usage Tips

- Log bugs that are **recurring or instructive**, not every trivial typo.
- Keep each field to 1-3 lines. Link to the PR or commit that fixed it when useful.
- Search this file before debugging a familiar-feeling error
  (`Grep(pattern="connection refused", path="docs/project_notes/bugs.md", -i=true)`).
- Always lead with a date so entries stay temporally ordered.
