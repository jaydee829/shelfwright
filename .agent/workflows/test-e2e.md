---
description: how to run full-system end-to-end (E2E) tests
---
// turbo-all
1. Ensure the development environment is correctly configured (e.g., Database and API keys).
2. Run all tests in the `test/e2e/` directory:
   ```powershell
   conda run -n agentic_librarian pytest test/e2e/
   ```
3. For specific E2E scenarios, run:
   ```powershell
   conda run -n agentic_librarian pytest test/e2e/test_name.py
   ```
4. Verify that the system handles diverse user inputs and real-world edge cases (e.g., network latency, missing external data).
5. Review the justifications produced by the agent to ensure they are grounded and not just "matching" test inputs.
