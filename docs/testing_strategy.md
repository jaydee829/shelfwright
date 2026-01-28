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
    -   Tests should be derived from specific use cases defined in [use_cases.md](file:///c:/Users/Justin.Merrick/Python_Code/Projects/agentic_librarian/agentic_librarian/docs/use_cases.md).
4.  **The Testing Pyramid**:
    -   **Unit Tests (70-80%)**: Fast, isolated logic tests.
    -   **Integration Tests (15-20%)**: Testing interactions between components (DB, Scouts, LLM providers).
    -   **E2E Tests (5%)**: Full system flows from prompt to recommendation.
5.  **Anti-Hardcoding & Robustness**:
    -   **Avoid Shortcuts**: Never implement logic that handles only specific test inputs.
    -   **Parameterization**: Use `pytest.mark.parametrize` to test logic against diverse data distributions.
    -   **Generalization**: Logic must pass tests for "expected" cases AND fail gracefully/correctly for edge cases.

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
