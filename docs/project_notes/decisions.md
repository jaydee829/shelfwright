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

### ADR-001: Model Context Protocol (MCP) for Data Access (2026-01-27)
**Context:**
- Need a standardized way to expose database and internal utilities to specialized agents.
**Decision:**
- Adopt Model Context Protocol (MCP) as the communication layer for data tools.
**Consequences:**
- Pros: Seamless integration with MCP-compliant agents, unified tool interface.

### ADR-002: A2A Protocol for Agent Collaboration (2026-01-27)
**Context:**
- Need robust multi-agent coordination for search, filtering, and ranking.
**Decision:**
- Use the A2A Protocol (Linux Foundation standard).
**Consequences:**
- Pros: Standardized discovery, secure messaging, structured delegation.

### ADR-003: Postgres + pgvector for Storage (2026-01-27)
**Context:**
- Need to store both relational metadata and semantic embeddings (tropes, styles).
**Decision:**
- PostgreSQL with `pgvector` extension.
**Consequences:**
- Pros: Single database for relational and vector data, solid ecosystem.

### ADR-004: MLOps Stack Selection (2026-01-27)
**Context:**
- Need orchestration, versioning, and experiment tracking.
**Decision:**
- Dagster (Orchestration), DVC (Data Versioning), MLFlow (Experiment Tracking).
**Consequences:**
- Pros: Industry-standard tools for reproducibility and monitoring.

### ADR-005: Standardized Testing Strategy (2026-01-28)
**Context:**
- Need to ensure all agents and contributors follow consistent testing practices (TDD, coverage, use-case driven).
**Decision:**
- Adopt a formal testing strategy documented in `docs/testing_strategy.md`, enforced via agent protocols and a standardized `.agent/workflows/test.md` workflow.
**Consequences:**
- Pros: Higher code quality, better maintainability, consistent verification across different agents.
- Cons: Slightly higher initial overhead for new contributors.
