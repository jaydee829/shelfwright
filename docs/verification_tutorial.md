# Verification Tutorial: Phases 1, 2, & 3

This tutorial guides you through verifying the Infrastructure (Phase 1), the ETL Pipeline (Phase 2), and the Recommendation Engine (Phase 3) of the `agentic_librarian` project within a Docker-based development environment.

## 1. Environment Setup

### Prerequisites
- Docker and Docker Compose installed.
- Valid API Keys for enrichment (stored in `.env`):
  - `GOOGLE_SEARCH_API_KEY` (for Metadata/Audiobook scouting)
  - `SEARCH_ENGINE_ID` (for Google Custom Search)
  - `HARDCOVER_API_KEY` (optional, but recommended)

### Initialize Configuration
1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
2. Update `.env` with your actual API keys. Ensure `POSTGRES_USER` and `POSTGRES_PASSWORD` match your desired local settings.
3. **Install Pre-commit Hooks**:
   ```bash
   pre-commit install
   ```
   *This ensures that Ruff and Pytest run automatically before every commit.*

## 2. Infrastructure Setup (Phase 1)

### Start Services
Spin up the database (with `pgvector`) and MLFlow:
```bash
docker compose up -d
```

### Verify Service Health
1. **Postgres**: Ensure the database is reachable and `pgvector` is ready.
   ```bash
   docker exec -it agentic_librarian_db psql -U librarian -d agentic_librarian -c "SELECT * FROM pg_extension WHERE extname = 'vector';"
   ```
   *Expected Output: A row showing the `vector` extension version.*

2. **MLFlow**: Open your browser and navigate to `http://localhost:5000`. You should see the MLFlow UI.

## 3. ETL Pipeline Execution (Phase 2)

### Start the Orchestration Server
In your development container (or local environment with the `agentic_librarian` conda env):
```bash
# Install dependencies if not already present
uv pip install -e ".[dev]"

# Start Dagster
dagster dev -f src/agentic_librarian/orchestration/definitions.py
```

### Trigger Ingestion
1. **Place a Sample File**: Ensure `data/raw/test_sample.csv` exists (one was created during development).
2. **Sensor Activation**: In the Dagster UI (`http://localhost:3000`), navigate to **Overview -> Sensors** and ensure `new_file_sensor` is running. It will detect the CSV and trigger `enhance_job`.
3. **Manual Run (Alternative)**:
   - Go to **Jobs -> enhance_job**.
   - Click **Launchpad**.
   - Select the `test_sample` partition.
   - Click **Launch Run**.

## 4. Phase 3 Verification (Recommendation Engine)

### Start the MCP Server
In your development environment:
```bash
# Start the FastMCP server
python -m agentic_librarian.mcp.server
```

### Client Verification (Option A: CLI)
Use the `mcp-cli` or similar tool to verify tools are discoverable:
```bash
npx @modelcontextprotocol/inspector python -m agentic_librarian.mcp.server
```
*This will open a web-based inspector to test your tools.*

### Client Verification (Option B: Claude Desktop)
1. Open your Claude Desktop configuration (`%APPDATA%\Claude\claude_desktop_config.json`).
2. Add the librarian server:
```json
{
  "mcpServers": {
    "librarian": {
      "command": "conda",
      "args": ["run", "-n", "agentic_librarian", "python", "-m", "agentic_librarian.mcp.server"]
    }
  }
}
```
3. Restart Claude Desktop. You should see the "hammer" icon for the Librarian tools.

### Run Phase 3 Tests
Phase 3 uses the **Dual-Verification Pattern** (Mock + Live).

1. **Verify Core Logic (CI-Safe)**:
   ```bash
   pytest test/unit/test_recommendation_logic.py
   ```
2. **Verify Database Tools (Requires Docker)**:
   ```bash
   pytest -m "db_integration" test/integration/test_mcp_tools.py
   ```

## 5. Results Verification

### Verifying Relational Data
Check that the ETL process has populated the database tables correctly.
```bash
# Check for populated Works and Editions
docker exec -it agentic_librarian_db psql -U librarian -d agentic_librarian -c "SELECT title, original_publication_year FROM works; SELECT isbn_13, format, page_count FROM editions;"
```

### Verifying Trope Vectorization (ADR-012)
Verify that tropes are being standardized and embedded.
```bash
# Check the Tropes table
docker exec -it agentic_librarian_db psql -U librarian -d agentic_librarian -c "SELECT name, embedding FROM tropes LIMIT 5;"
```
*Note: The `embedding` column should contain a vector (e.g., `[0.1, 0.2, ...]`).*

### Verifying Enrichment (MLFlow)
1. Navigate to the MLFlow UI (`http://localhost:5000`).
2. Look for the following experiments:
   - **`audiobook_scouting_comparison`**: Compare Pathway A (Scraping) vs Pathway B (Direct LLM).
   - **`metadata_enrichment`**: General metrics for the ingestion run.
3. Review the logged parameters like `pathway_a_minutes` and `pathway_b_minutes` to verify the accuracy of the scouting logic.

## 5. Automated Testing

Run the full test suite to ensure regression-free implementation:
```bash
# Run Unit Tests (Fast, no DB/API)
pytest test/unit

# Run Integration Tests (Requires DB/Mocks)
pytest test/integration/test_etl_pipeline.py
```

## Troubleshooting
- **Database Connection**: If MLFlow cannot connect, ensure the `DATABASE_URL` in `.env` uses `db` as the hostname for Docker-to-Docker communication, or `localhost` if running outside Docker.
- **API Limits**: If scouting fails, check the Dagster logs for `429` errors or missing API keys.
