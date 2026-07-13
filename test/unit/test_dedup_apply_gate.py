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

    # tokens are tagged with their operation (`merge:` for survivor+loser group identity) —
    # see test_plan_id_set_tags_tokens_by_operation for the full tagging-scheme assertions.
    group = plan.duplicate_authors[0]
    assert f"merge:{group.survivor_id}" in ids["duplicate_authors"]
    assert f"merge:{group.loser_ids[0]}" in ids["duplicate_authors"]

    rh_group = plan.duplicate_reading_history[0]
    assert f"merge:{rh_group.survivor_id}" in ids["duplicate_reading_history"]
    assert f"delete:{rh_group.loser_ids[0]}" in ids["duplicate_reading_history"]

    # every declared class key is always present, even when empty
    assert set(ids) == set(db_.PLAN_ID_SET_CLASSES)
    assert ids["duplicate_narrators"] == set()


def test_plan_id_set_tags_tokens_by_operation():
    """Every token in plan_id_set is prefixed with the operation it belongs to (`merge:` for
    survivor+losers group identity, `repoint:` for a link/row being re-pointed onto the
    survivor, `delete:` for a link/row being deleted outright, `report:` for the never-applied
    duplicate_works_report_only class). This is the mechanism the operation-flip regression
    test (test_plan_delta_flags_operation_flip_on_same_id) depends on."""
    survivor = _uuid()
    loser = _uuid()
    work_id = _uuid()
    repoint_pk = (loser, (work_id, loser, "Author"))
    plan = DedupPlan(
        duplicate_authors=[
            ContributorMergeGroup(
                survivor_id=survivor,
                survivor_name="Ann Leckie",
                loser_ids=[loser],
                loser_names=["ann leckie"],
                repoint_links=[repoint_pk],
            )
        ],
        orphan_authors=[_uuid()],
    )
    ids = db_.plan_id_set(plan)

    assert f"merge:{survivor}" in ids["duplicate_authors"]
    assert f"merge:{loser}" in ids["duplicate_authors"]
    assert f"repoint:{repoint_pk}" in ids["duplicate_authors"]
    assert all(tok.startswith(("merge:", "repoint:", "delete:")) for tok in ids["duplicate_authors"])
    assert all(tok.startswith("delete:") for tok in ids["orphan_authors"])


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

    assert delta["duplicate_authors"] == {f"merge:{new_loser}"}


def test_plan_delta_flags_operation_flip_on_same_id():
    """THE core regression this task hardens: row X was reviewed as a REPOINT (its
    work_contributors link would be moved onto the survivor), but a concurrent write between
    the reviewed dry-run and the fresh apply-time re-plan means the fresh plan now treats X as
    a DELETE instead (e.g. the survivor gained a link with the same (work_id, role) key in the
    gap, so X's link now collides and must be deleted rather than repointed). The row id is
    identical in both plans — only the OPERATION changed. An untagged id-set diff would see no
    difference (X is "in both") and let the flip through unreviewed. With tagged tokens,
    `repoint:X` is only in `reviewed` and `delete:X` is only in `fresh`, so plan_delta must be
    non-empty (delete:X is a token fresh has that reviewed does not) and the apply gate refuses."""
    survivor = _uuid()
    loser = _uuid()
    work_id = _uuid()
    pk = (work_id, loser, "Author")
    link_token = (loser, pk)

    reviewed_plan = DedupPlan(
        duplicate_authors=[
            ContributorMergeGroup(
                survivor_id=survivor,
                survivor_name="Ann Leckie",
                loser_ids=[loser],
                loser_names=["ann leckie"],
                repoint_links=[link_token],
            )
        ]
    )
    reviewed = db_.plan_id_set(reviewed_plan)

    fresh_plan = DedupPlan(
        duplicate_authors=[
            ContributorMergeGroup(
                survivor_id=survivor,
                survivor_name="Ann Leckie",
                loser_ids=[loser],
                loser_names=["ann leckie"],
                delete_links=[link_token],  # same (loser_id, pk) — different operation
            )
        ]
    )

    delta = db_.plan_delta(reviewed, fresh_plan)

    assert delta["duplicate_authors"], "operation flip on the same id must surface as a delta"
    assert f"delete:{link_token}" in delta["duplicate_authors"]
    # the survivor/loser group identity itself didn't change, so it must NOT be part of the delta
    assert f"merge:{survivor}" not in delta["duplicate_authors"]
    assert f"merge:{loser}" not in delta["duplicate_authors"]


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


def test_parse_plan_ids_raises_on_missing_end_terminator():
    """A truncated report (write got cut off, or someone hand-edited it) that has the start
    marker but never reaches '== END PLAN IDS ==' must fail closed with a ValueError — not
    silently parse whatever partial content came after the start marker as the full reviewed
    set. Fail-closed here is an explicit tested contract, not an accident of subtraction
    semantics: a truncated (too-small) reviewed set would otherwise make plan_delta look like
    everything in the fresh plan is "new," or in the pathological case where the truncation
    itself happens after some ids, other ids missing from the truncated file due to write
    failure could silently vanish from the comparison instead of refusing outright."""
    import pytest

    text = "\n".join(
        [
            "Dedup plan report — truncated",
            "== PLAN IDS ==",
            "[duplicate_authors] 1",
            "merge:11111111-1111-1111-1111-111111111111",
            # no "== END PLAN IDS ==" — write was cut off
        ]
    )

    with pytest.raises(ValueError, match="END PLAN IDS"):
        clean_catalog._parse_plan_ids(text)


def test_parse_plan_ids_raises_on_malformed_class_header():
    """A line starting with '[' inside the PLAN IDS block that never closes with ']' (a
    malformed/corrupted class header) must raise, not be silently absorbed as a plain id token
    under whatever the previous class header was — that would hide real ids under the wrong
    class (or under no class at all if none has been seen yet), corrupting the per-class diff
    the apply gate depends on."""
    import pytest

    text = "\n".join(
        [
            "Dedup plan report — malformed",
            "== PLAN IDS ==",
            "[duplicate_authors",  # missing closing ']'
            "merge:11111111-1111-1111-1111-111111111111",
            "== END PLAN IDS ==",
        ]
    )

    with pytest.raises(ValueError, match="malformed class-header"):
        clean_catalog._parse_plan_ids(text)


class _Sess:
    """Minimal fake session — plan_dedup itself is monkeypatched below, so the fake session
    only needs to satisfy main()'s top-of-loop recency probe (column-explicit
    session.query(func.count(...)).scalar(), never an entity-load .count() — the probe must
    work on the pre-migration prod schema, GH #95)."""

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

    def scalar(self):
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
