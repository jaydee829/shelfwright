# Bug Log

This file tracks project bugs, their root causes, solutions, and prevention strategies.

## Templates

### YYYY-MM-DD - Brief Bug Description
- **Issue**: What went wrong
- **Root Cause**: Why it happened
- **Solution**: How it was fixed
- **Prevention**: How to avoid it in the future

## Log

### 2026-02-06 - Fragile Year Extraction in Metadata Scout
- **Issue**: `original_publication_year` extraction failed for common date formats (e.g., "January 2023", "2023/01/01"), defaulting to `None`.
- **Root Cause**: Manual string splitting `split("-")[0]` only handled "YYYY-MM-DD" format.
- **Solution**: Implemented regex-based `_extract_year` helper in `MultiSourceScout` to find the first 4-digit sequence in the input string.
- **Prevention**: Use robust parsing (regex) for external API data and maintain unit tests covering multiple date formats.

### 2026-02-17 - Ruff E722 Bare Except Clauses
- **Issue**: Bare `except:` clauses in `search_strategies.py` triggered Ruff E722 and violated the project's "No Broad Except-Pass" mandate.
- **Root Cause**: Generic exception handling used for JSON parsing failures.
- **Solution**: Replaced bare `except:` with `except json.JSONDecodeError as e:` and added warning prints for visibility into failures.
- **Prevention**: Use specific exception types when possible and always include error logging/printing in catch blocks to maintain visibility of failures.

### 2026-02-17 - Environment/Syntax Mismatch (Python 3.9 vs 3.12)
- **Issue**: `TypeError` on union type hints (`|`) and `ImportError` on `datetime.UTC`.
- **Root Cause**: Tooling was defaulting to system Python 3.9 instead of the project's Conda environment (Python 3.12).
- **Solution**: Explicitly targeted the environment binary (`.../.conda/envs/agentic_librarian/python.exe`) for all test runs and verified 3.12 compatibility.
- **Prevention**: Always use the full path to the environment's python executable or ensure `conda run -n` is correctly resolving the local binary.

### 2026-02-17 - Module-Level DB Initialization Crash in CI
- **Issue**: Github CI failed during test collection with `ValueError: Database credentials not found`.
- **Root Cause**: `DatabaseManager` was validating credentials in `__init__`, and `mcp/server.py` was instantiating a global manager at the module level. This caused crashes on import in any environment without a live DB.
- **Solution**: Implemented lazy initialization in `DatabaseManager`. Engine and SessionFactory creation are now deferred until the first session request.
- **Prevention**: Avoid heavy side effects (network, FS, cred validation) in `__init__` for global service managers.
