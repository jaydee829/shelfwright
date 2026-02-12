---
description: how to run tests and verify coverage
---
// turbo-all
1. Check for new tests in `test/` directory.
2. Run all tests to ensure baseline stability:
   ```powershell
   pytest
   ```
3. Run tests with coverage reporting:
   ```powershell
   pytest --cov=src --cov-report=term-missing
   ```
4. Verify that coverage meets the 80% threshold for new or modified code.
5. If tests fail or coverage is insufficient, identify missing cases or bugs and iterate.
