"""DB-free unit tests for the dedup apply gate (Spec 2026-07-12 follow-up to #95):
plan_id_set/plan_delta round-trip, and the report file's '== PLAN IDS ==' block parses back
into the same shape it was written from. No Postgres needed — DedupPlan is a plain dataclass
and the report format is pure text.

Review finding this hardens: `--dedup-for-constraints --apply --yes` is a SEPARATE invocation
from the reviewed dry-run and RE-PLANS from scratch, so a new duplicate group appearing in the
gap (live traffic) would be applied without the operator ever reviewing it. plan_delta is the
pure comparison at the heart of the fix."""

import importlib.util
import uuid
from pathlib import Path

from agentic_librarian.etl import dedup_backfill as db_
from agentic_librarian.etl.dedup_backfill import (
    ContributorMergeGroup,
    DedupPlan,
    KeepDeleteGroup,
)

spec = importlib.util.spec_from_file_location(
    "clean_catalog", Path(__file__).resolve().parents[2] / "scripts" / "clean_catalog.py"
)
clean_catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clean_catalog)


def _uuid():
    return uuid.uuid4()


def _sample_plan() -> DedupPlan:
    survivor = _uuid()
    loser = _uuid()
    rh_survivor = _uuid()
    rh_loser = _uuid()
    return DedupPlan(
        duplicate_authors=[
            ContributorMergeGroup(
                survivor_id=survivor,
                survivor_name="Ann Leckie",
                loser_ids=[loser],
                loser_names=["ann leckie"],
            )
        ],
        duplicate_reading_history=[KeepDeleteGroup(survivor_id=rh_survivor, loser_ids=[rh_loser], detail="dup")],
    )


def test_plan_id_set_includes_survivor_and_loser_ids():
    plan = _sample_plan()
    ids = db_.plan_id_set(plan)

    group = plan.duplicate_authors[0]
    assert str(group.survivor_id) in ids["duplicate_authors"]
    assert str(group.loser_ids[0]) in ids["duplicate_authors"]

    rh_group = plan.duplicate_reading_history[0]
    assert str(rh_group.survivor_id) in ids["duplicate_reading_history"]
    assert str(rh_group.loser_ids[0]) in ids["duplicate_reading_history"]

    # every declared class key is always present, even when empty
    assert set(ids) == set(db_.PLAN_ID_SET_CLASSES)
    assert ids["duplicate_narrators"] == set()


def test_plan_delta_empty_when_fresh_is_subset_of_reviewed():
    plan = _sample_plan()
    reviewed = db_.plan_id_set(plan)

    delta = db_.plan_delta(reviewed, plan)

    assert all(len(v) == 0 for v in delta.values())


def test_plan_delta_empty_when_reviewed_has_extra_stale_ids():
    """Reviewed ids that vanished from the fresh plan (rows deleted/changed since review) do
    NOT show up as a delta — plan_delta only reports NEW ids, never missing ones."""
    plan = _sample_plan()
    reviewed = db_.plan_id_set(plan)
    reviewed["duplicate_authors"].add(str(uuid.uuid4()))  # an id that no longer appears

    delta = db_.plan_delta(reviewed, plan)

    assert all(len(v) == 0 for v in delta.values())


def test_plan_delta_flags_new_group_id():
    """A brand-new duplicate-author group (simulating live traffic in the review gap) shows up
    as a delta for its class only."""
    reviewed_plan = _sample_plan()
    reviewed = db_.plan_id_set(reviewed_plan)

    fresh_plan = _sample_plan()
    fresh_plan.duplicate_authors = [*reviewed_plan.duplicate_authors, *_sample_plan().duplicate_authors]
    fresh_plan.duplicate_reading_history = reviewed_plan.duplicate_reading_history

    delta = db_.plan_delta(reviewed, fresh_plan)

    assert delta["duplicate_authors"]  # the new group's survivor/loser ids
    assert delta["duplicate_reading_history"] == set()


def test_plan_delta_flags_new_loser_appended_to_existing_group():
    """A group whose loser set grew (an extra live-traffic dup merged into an already-reviewed
    group) is also caught — not just brand-new groups."""
    survivor = _uuid()
    reviewed_loser = _uuid()
    new_loser = _uuid()
    reviewed_plan = DedupPlan(
        duplicate_authors=[
            ContributorMergeGroup(
                survivor_id=survivor, survivor_name="A", loser_ids=[reviewed_loser], loser_names=["a"]
            )
        ]
    )
    reviewed = db_.plan_id_set(reviewed_plan)

    fresh_plan = DedupPlan(
        duplicate_authors=[
            ContributorMergeGroup(
                survivor_id=survivor,
                survivor_name="A",
                loser_ids=[reviewed_loser, new_loser],
                loser_names=["a", "a2"],
            )
        ]
    )

    delta = db_.plan_delta(reviewed, fresh_plan)

    assert delta["duplicate_authors"] == {str(new_loser)}


def test_report_plan_ids_block_round_trips(tmp_path, monkeypatch):
    """_write_dedup_report's '== PLAN IDS ==' section parses back (via clean_catalog's
    _parse_plan_ids) into the exact same per-class id set dedup_backfill.plan_id_set computed
    from the plan it was written from."""
    monkeypatch.chdir(tmp_path)
    plan = _sample_plan()

    report_path = clean_catalog._write_dedup_report(plan)
    parsed = clean_catalog._parse_plan_ids(report_path.read_text(encoding="utf-8"))

    assert parsed == db_.plan_id_set(plan)


def test_parse_plan_ids_raises_on_missing_section():
    import pytest

    with pytest.raises(ValueError, match="PLAN IDS"):
        clean_catalog._parse_plan_ids("some report with no machine-readable section\n")


class _Sess:
    """Minimal fake session — plan_dedup itself is monkeypatched below, so the fake session
    only needs to satisfy main()'s top-of-loop recency probe (session.query(...).count())."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def query(self, *a, **k):
        return self

    def filter_by(self, *a, **k):
        return self

    def all(self):
        return []

    def count(self):
        return 0


def _patch_db(monkeypatch, plans):
    """plans: a list consumed in order across successive main() calls / plan_dedup calls within
    one call — main() calls plan_dedup exactly once per invocation, so pass one plan per call."""
    monkeypatch.setattr(clean_catalog, "resolve_database_url", lambda: "postgresql://u:p@prod-host/db")
    monkeypatch.setattr(
        clean_catalog, "DatabaseManager", lambda url: type("M", (), {"get_session": lambda s: _Sess()})()
    )
    it = iter(plans)
    monkeypatch.setattr(clean_catalog.dedup_backfill, "plan_dedup", lambda session: next(it))


def test_apply_succeeds_when_fresh_plan_matches_reviewed_report(monkeypatch, tmp_path, capsys):
    """(a) dry-run -> apply with an unchanged DB succeeds — no new ids, apply proceeds."""
    monkeypatch.chdir(tmp_path)
    plan = _sample_plan()
    _patch_db(monkeypatch, [plan, plan])  # one plan_dedup call for --dry-run, one for --apply
    monkeypatch.setattr(clean_catalog.dedup_backfill, "apply_dedup", lambda session, plan: {"duplicate_authors": 1})

    rc_dry = clean_catalog.main(["--dedup-for-constraints", "--dry-run"])
    assert rc_dry == 0

    rc_apply = clean_catalog.main(["--dedup-for-constraints", "--apply", "--yes"])
    out = capsys.readouterr().out
    assert rc_apply == 0
    assert "REFUSING" not in out
    assert "applied:" in out


def test_apply_refuses_when_fresh_plan_has_new_group(monkeypatch, tmp_path, capsys):
    """(b) dry-run -> seed a NEW duplicate group (simulated via the re-plan returning an extra
    group) -> apply REFUSES with the delta printed and a fresh report already on disk."""
    monkeypatch.chdir(tmp_path)
    reviewed_plan = _sample_plan()
    fresh_plan = _sample_plan()
    fresh_plan.duplicate_authors = [*reviewed_plan.duplicate_authors, *_sample_plan().duplicate_authors]
    fresh_plan.duplicate_reading_history = reviewed_plan.duplicate_reading_history
    _patch_db(monkeypatch, [reviewed_plan, fresh_plan])
    apply_called = []
    monkeypatch.setattr(
        clean_catalog.dedup_backfill, "apply_dedup", lambda session, plan: apply_called.append(plan) or {}
    )

    rc_dry = clean_catalog.main(["--dedup-for-constraints", "--dry-run"])
    assert rc_dry == 0
    reports_before = sorted((tmp_path / "data" / "reports").glob("dedup-*.txt"))
    assert len(reports_before) == 1

    rc_apply = clean_catalog.main(["--dedup-for-constraints", "--apply", "--yes"])
    out = capsys.readouterr().out

    assert rc_apply == 1
    assert "REFUSING --apply: plan changed since review" in out
    assert "duplicate_authors" in out
    assert apply_called == []  # apply_dedup never invoked
    reports_after = sorted((tmp_path / "data" / "reports").glob("dedup-*.txt"))
    assert len(reports_after) == 2  # the refusal's fresh plan was written as a new report too


def test_apply_succeeds_with_skipped_stale_when_reviewed_row_vanished(monkeypatch, tmp_path, capsys):
    """(c) dry-run -> a planned row is deleted before apply -> the FRESH re-plan simply no
    longer contains it (fresh subset of reviewed) -> apply succeeds; apply_dedup's own
    skipped_stale bookkeeping (tested at the dedup_backfill layer) is unaffected by this gate."""
    monkeypatch.chdir(tmp_path)
    reviewed_plan = _sample_plan()
    fresh_plan = DedupPlan()  # the reviewed group's row vanished -> fresh has nothing for it
    _patch_db(monkeypatch, [reviewed_plan, fresh_plan])
    monkeypatch.setattr(clean_catalog.dedup_backfill, "apply_dedup", lambda session, plan: {"skipped_stale": 0})

    rc_dry = clean_catalog.main(["--dedup-for-constraints", "--dry-run"])
    assert rc_dry == 0

    rc_apply = clean_catalog.main(["--dedup-for-constraints", "--apply", "--yes"])
    out = capsys.readouterr().out

    assert rc_apply == 0
    assert "REFUSING" not in out
    assert "applied:" in out


def test_apply_refuses_when_no_report_exists(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    _patch_db(monkeypatch, [_sample_plan()])

    rc = clean_catalog.main(["--dedup-for-constraints", "--apply", "--yes"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "no dedup report found" in out
