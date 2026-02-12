from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.orchestration import assets, jobs, sensors
from dagster import Definitions, load_assets_from_modules

all_assets = load_assets_from_modules([assets])

defs = Definitions(
    assets=all_assets,
    jobs=[jobs.enhance_job],
    sensors=[sensors.new_file_sensor],
    resources={
        "db_manager": DatabaseManager(),
    },
)
