import os
import shutil
from pathlib import Path

import pytest
from agentic_librarian.db.models import Author, Edition, ReadingHistory, Trope, Work, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.orchestration import assets
from agentic_librarian.orchestration.definitions import create_scout_manager
from dagster import DagsterInstance, materialize

PARTITION_KEY = "20200107"
SMOKE_CSV = Path(__file__).parent.parent / "data" / "etl_smoke" / f"{PARTITION_KEY}.csv"


@pytest.fixture
def staged_csv():
    # raw_history reads a hardcoded data/raw/{partition_key}.csv; stage the fixture there and
    # remove it afterward so the real data/raw/ is never polluted.
    dest = Path("data/raw") / f"{PARTITION_KEY}.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SMOKE_CSV, dest)
    yield dest
    dest.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def local_mlflow_tracking(tmp_path):
    """Override MLFLOW_TRACKING_URI to a local temp dir for tests.

    The compose MLflow server rejects requests from internal hostnames
    (DNS-rebinding protection). Tests use a local file store to avoid
    that 403 and to stay isolated from the shared tracking server.
    """
    mlruns_dir = tmp_path / "mlruns"
    mlruns_dir.mkdir()
    old_uri = os.environ.get("MLFLOW_TRACKING_URI")
    os.environ["MLFLOW_TRACKING_URI"] = str(mlruns_dir)
    yield
    if old_uri is None:
        os.environ.pop("MLFLOW_TRACKING_URI", None)
    else:
        os.environ["MLFLOW_TRACKING_URI"] = old_uri


@pytest.mark.api_dependent
@pytest.mark.db_integration
def test_flow1_etl_populates_db(db_url, staged_csv):
    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(assets.csv_partitions.name, [PARTITION_KEY])
    test_db_manager = DatabaseManager(db_url)

    result = materialize(
        [assets.raw_history, assets.enriched_metadata, assets.vectorized_tropes],
        partition_key=PARTITION_KEY,
        instance=instance,
        resources={"db_manager": test_db_manager, "scout_manager": create_scout_manager()},
    )
    assert result.success

    with test_db_manager.get_session() as session:
        work = session.query(Work).filter(Work.title == "The Way of Kings").first()
        assert work is not None, "Work was not created"
        assert session.query(Author).filter(Author.name == "Brandon Sanderson").first() is not None
        assert session.query(Edition).filter(Edition.work_id == work.id).first() is not None
        wt = session.query(WorkTrope).filter(WorkTrope.work_id == work.id).first()
        assert wt is not None, "no trope linked to the work"
        trope = session.query(Trope).filter(Trope.id == wt.trope_id).first()
        assert trope.embedding is not None, "trope embedding not populated"
        assert (
            session.query(ReadingHistory).join(Edition).filter(Edition.work_id == work.id).first() is not None
        ), "no reading history recorded"
