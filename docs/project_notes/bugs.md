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
