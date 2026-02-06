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
