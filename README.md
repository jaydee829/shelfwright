# agentic_librarian
An agentic book recommender system

## Development Environment

### Docker Setup

Build the Docker image:

```bash
docker build -t agentic-librarian .
```

Run the container:

```bash
docker run -it agentic-librarian
```

### Dependencies

This project uses [uv](https://github.com/astral-sh/uv) for fast dependency management. Dependencies are defined in `pyproject.toml`.

To install dependencies:

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install project dependencies
uv pip install -e .

# Install development dependencies
uv pip install -e ".[dev]"
```

The development environment includes the following packages:

- **ML/Data Science**: mlflow, pandas, scikit-learn, PyTorch
- **API/Web**: requests, google-api-python-client, firecrawl-py
- **Audio/Books**: audible
- **LLM**: openai, langchain, langchain-openai

### Code Quality and Testing

This project uses `pre-commit` to ensure code quality and consistency. The following checks are run on every commit:

- **Linting & Formatting**: Handled by [ruff](https://github.com/astral-sh/ruff) (fastest Python linter/formatter).
- **Static Analysis**: Standard `pre-commit-hooks` (whitespace, YAML, etc.).
- **Tests**: `pytest` is run on a subset of fast, non-API-dependent tests.

#### Local Setup

1. **Install pre-commit**:
   ```bash
   pip install pre-commit
   ```

2. **Install the git hooks**:
   ```bash
   pre-commit install
   ```

3. **(Optional) Run on all files**:
   ```bash
   pre-commit run --all-files
   ```

#### Testing Strategy

- **Fast Tests**: Run automatically on commit.
- **Slow/API Tests**: Skipped in `pre-commit` to maintain speed. Mark these in code using:
  ```python
  @pytest.mark.api_dependent
  def test_something_with_api():
      ...

  @pytest.mark.slow
  def test_something_slow():
      ...
  ```
- **Manual Test Execution**:
  ```bash
  # Run all tests
  pytest

  # Run only fast tests (what pre-commit runs)
  pytest -m "not api_dependent and not slow"
  ```
