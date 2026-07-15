"""H4: pure/compile-inspect unit tests for dedup_backfill.promote_detected_duplicate_pair — the
CLI helper function scripts/clean_catalog.py's --promote-pair mode calls per pair. Mirrors
test_detected_duplicates_upsert.py's compile-inspect idiom (house rule 4: pg-dialect statements
get compile-inspect locally, execute in CI/db_integration) — the real insert/existence-check
mechanics against a live Postgres session are covered by test/integration/test_works_merge.py."""

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from agentic_librarian.db.models import DetectedDuplicate
from agentic_librarian.etl import dedup_backfill as db_


def test_promote_pair_insert_conflict_target_is_the_composite_pk():
    """Standalone statement, same shape promote_detected_duplicate_pair issues — mirrors
    test_detected_duplicates_upsert.py's first test exactly, source='operator' instead of
    'deep_pass_redirect'."""
    work_id_a, work_id_b = uuid4(), uuid4()
    stmt = (
        pg_insert(DetectedDuplicate)
        .values(work_id_a=work_id_a, work_id_b=work_id_b, source="operator")
        .on_conflict_do_nothing(index_elements=["work_id_a", "work_id_b"])
    )

    compiled = str(stmt.compile(dialect=postgresql.dialect()))

    assert "INSERT INTO detected_duplicates" in compiled
    assert "ON CONFLICT (work_id_a, work_id_b) DO NOTHING" in compiled


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Captures the ACTUAL statement promote_detected_duplicate_pair executes, same idiom as
    test_detected_duplicates_upsert.py's test_two_phase_redirect_issues_the_same_upsert_shape."""

    def __init__(self, rows, *, existing: bool):
        self._rows = rows
        self._existing = existing
        self.executed = None

    def query(self, *entities):
        return _FakeQuery(self._rows)

    def get(self, model, pk):
        return object() if self._existing else None

    def execute(self, stmt):
        self.executed = stmt

    def flush(self):
        pass


def test_promote_pair_issues_the_same_upsert_shape_with_operator_source():
    work_id_a, work_id_b = uuid4(), uuid4()
    session = _FakeSession([(work_id_a, "Title A"), (work_id_b, "Title B")], existing=False)

    result = db_.promote_detected_duplicate_pair(session, work_id_a, work_id_b)

    assert result.already_existed is False
    assert result.title_a == "Title A"
    assert result.title_b == "Title B"
    compiled = str(session.executed.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "INSERT INTO detected_duplicates" in compiled
    assert "ON CONFLICT (work_id_a, work_id_b) DO NOTHING" in compiled
    assert "'operator'" in compiled
    assert str(work_id_a) in compiled
    assert str(work_id_b) in compiled


def test_promote_pair_already_existed_reflects_pre_insert_check():
    work_id_a, work_id_b = uuid4(), uuid4()
    session = _FakeSession([(work_id_a, "Title A"), (work_id_b, "Title B")], existing=True)

    result = db_.promote_detected_duplicate_pair(session, work_id_a, work_id_b)

    assert result.already_existed is True


def test_promote_pair_self_pair_rejected_names_the_id():
    work_id = uuid4()
    session = _FakeSession([(work_id, "Title")], existing=False)

    with pytest.raises(ValueError, match=str(work_id)):
        db_.promote_detected_duplicate_pair(session, work_id, work_id)


@pytest.mark.parametrize(
    "known_rows, missing_expected_index",
    [
        ([], "both"),
        ("only_a", "b"),
        ("only_b", "a"),
    ],
    ids=["both_missing", "b_missing", "a_missing"],
)
def test_promote_pair_missing_work_ids_raise_unknown_work_ids_error(known_rows, missing_expected_index):
    work_id_a, work_id_b = uuid4(), uuid4()
    if known_rows == "only_a":
        rows = [(work_id_a, "Title A")]
    elif known_rows == "only_b":
        rows = [(work_id_b, "Title B")]
    else:
        rows = []
    session = _FakeSession(rows, existing=False)

    with pytest.raises(db_.UnknownWorkIdsError) as exc_info:
        db_.promote_detected_duplicate_pair(session, work_id_a, work_id_b)

    if missing_expected_index == "both":
        assert set(exc_info.value.missing_ids) == {work_id_a, work_id_b}
    elif missing_expected_index == "a":
        assert exc_info.value.missing_ids == [work_id_a]
    else:
        assert exc_info.value.missing_ids == [work_id_b]
