# Testing Strategy

This document outlines the mandatory testing standards for all agents and contributors working on the `agentic-librarian` project.

## Core Principles

1.  **Test-Driven Development (TDD)**:
    -   Write tests *before* implementing functionality.
    -   Follow the Red-Green-Refactor cycle.
2.  **Coverage Requirements**:
    -   Maintain a minimum of **80% code coverage** for all new features.
    -   Critical core logic should target **100% coverage**.
3.  **Use Case Driven**:
    -   Tests should be derived from specific use cases defined in [use_cases.md](use_cases.md).
4.  **The Testing Pyramid**:
    -   **Unit Tests (70-80%)**: Fast, isolated logic tests.
    -   **Integration Tests (15-20%)**: Testing interactions between components (DB, Scouts, LLM providers).
    -   **E2E Tests (5%)**: Full system flows from prompt to recommendation.
5.  **Anti-Hardcoding & Robustness**:
    -   **Avoid Shortcuts**: Never implement logic that handles only specific test inputs.
    -   **Parameterization**: Use `pytest.mark.parametrize` to test logic against diverse data distributions.
    -   **Generalization**: Logic must pass tests for "expected" cases AND fail gracefully/correctly for edge cases.
6.  **Dual-Verification Pattern**:
    -   **Mock Test**: Verification of core logic using simulated data (runs in CI).
    -   **Integration Test**: Marked `@pytest.mark.db_integration` to verify database/SQL behavior (runs when DB is available).
    -   **Parity via Shared Fixtures**: To prevent drift, use shared JSON files in `test/data/` (e.g., `standard_books.json`).
        -   Mocks should load this JSON to populate `return_value` / `side_effect`.
        -   Integration tests should load this JSON to perform `INSERT` operations before verification.

## Technical Debt Management

1.  **Documentation**: Log assumed debt (missing live tests, edge cases) in `docs/project_notes/bugs.md` or Phase plans.
2.  **Parity Requirement**: If a feature is developed using mocks due to environment constraints, it MUST have a corresponding `db_integration` test drafted simultaneously.
3.  **Buy-down**: Resolve and verify logged debt before moving to subsequent development phases when the appropriate environment is available.

## Continuous Integration (CI)

On every push and pull request, the GitHub CI workflow automatically:
- Runs all unit tests.
- Excludes tests marked as `api_dependent` or `slow` (using `pytest -m "not api_dependent and not slow"`) to ensure fast feedback and avoid external dependency failures.

## Testing Framework

-   **Framework**: [pytest](https://docs.pytest.org/)
-   **Structure**:
    -   `test/unit/`: Logic that can be tested in isolation.
    -   `test/integration/`: Interactions between components (e.g., DB, APIs).
    -   `test/e2e/`: Full system flows.

## Mandatory Workflow for Agents

Agents MUST follow this process when implementing work:

1.  **Analyze**: Review `spec.md` and `schema.md` for requirements.
2.  **Define Tests**: Create test files in `test/` reflecting the requirements.
3.  **Verify Failure**: Run `pytest` to ensure tests fail as expected.
4.  **Implement**: Write the minimum code required to make tests pass.
5.  **Refactor**: Improve code quality while keeping tests passing.
6.  **Coverage Check**: Run coverage analysis (e.g., `pytest --cov=src`) and ensure requirements are met.

## Tools & Commands

-   Run all tests: `pytest`
-   Run tests with coverage: `pytest --cov=src`
-   Run a specific test file: `pytest test/path/to/test_file.py`
