# Architectural Decision Records

This file documents key architectural decisions, their context, and trade-offs.

## Templates

### ADR-XXX: Decision Title (YYYY-MM-DD)

**Context:**
- Why the decision was needed
- What problem it solves

**Decision:**
- What was chosen

**Alternatives Considered:**
- Option 1 -> Why rejected
- Option 2 -> Why rejected

**Consequences:**
- Benefits
- Trade-offs

## Decisions

<!--
Add new ADRs below. Number them sequentially (ADR-001, ADR-002, ...).
Never delete an ADR — if a decision changes, add a revision note with the new date
and, if needed, a superseding ADR that references the old one.

Example:

### ADR-001: Use Alembic for Database Migrations (2026-01-20)
**Context:**
- Need version-controlled, reversible schema changes across environments.
**Decision:**
- Adopt Alembic, with one revision per schema change committed alongside the code.
**Alternatives Considered:**
- Hand-written SQL migrations -> No autogeneration, error-prone ordering.
- ORM auto-sync (create_all) -> No history, unsafe for production.
**Consequences:**
- Pros: Reproducible, reviewable migrations; safe forward/backward moves.
- Trade-offs: Autogenerate diffs still require manual review.
-->

## Usage Tips

- Check this file **before** proposing an architectural change. If the proposal
  conflicts with an existing ADR, acknowledge the prior decision and explain why
  revisiting it is warranted.
- ADRs are lightweight and historical — keep all of them.
- Find decisions about a topic with
  `Grep(pattern="^### ADR-", path="docs/project_notes/decisions.md")` or a keyword search.
