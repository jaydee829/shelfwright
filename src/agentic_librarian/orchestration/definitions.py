from dagster import Definitions, load_assets_from_modules

from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.orchestration import assets, jobs, sensors
from agentic_librarian.scouts.metadata_scout import (
    AudiobookScout,
    DirectKnowledgeScout,
    GoogleBooksScout,
    HardcoverScout,
    LLMTropeScout,
    ScoutManager,
    StyleScout,
)

all_assets = load_assets_from_modules([assets])


def create_scout_manager() -> ScoutManager:
    """Initialize and configure the ScoutManager with active scouts."""
    manager = ScoutManager()
    manager.register_scout(HardcoverScout(), priority=1)
    manager.register_scout(GoogleBooksScout(), priority=2)
    manager.register_scout(AudiobookScout(), priority=3)
    manager.register_scout(DirectKnowledgeScout(), priority=4)
    # StyleScout runs after the audiobook scouts so narrator_names is populated
    # before it scouts narrator styles. LLMTropeScout extracts deep tropes.
    manager.register_scout(StyleScout(), priority=5)
    manager.register_scout(LLMTropeScout(), priority=6)
    return manager


defs = Definitions(
    assets=all_assets,
    jobs=[jobs.enhance_job],
    sensors=[sensors.new_file_sensor],
    resources={
        "db_manager": DatabaseManager(),
        "scout_manager": create_scout_manager(),
    },
)
