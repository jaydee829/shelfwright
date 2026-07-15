"""CLI-level tests for --promote-pair (H4) — mirrors test_clean_catalog_merge_works_apply_cli.py's
structure (same --yes / prod-url guard shape). The domain logic itself (self-pair rejection,
both-ids-exist check, ON CONFLICT DO NOTHING insert) lives in
dedup_backfill.promote_detected_duplicate_pair and is covered by
test/integration/test_works_merge.py (db_integration) plus test_detected_duplicates_upsert.py's
compile-inspect sibling for the ON CONFLICT shape; this file is about the CLI's own UUID-parsing,
guard, and print/dispatch wiring, with the insert seam mocked."""

import importlib.util
from pathlib import Path
from uuid import UUID, uuid4

import pytest

spec = importlib.util.spec_from_file_location(
    "clean_catalog", Path(__file__).resolve().parents[2] / "scripts" / "clean_catalog.py"
)
clean_catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clean_catalog)


class _Sess:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_db(monkeypatch, *, url="postgresql://u:p@prod-host/db"):
    monkeypatch.setattr(clean_catalog, "resolve_database_url", lambda: url)
    monkeypatch.setattr(
        clean_catalog, "DatabaseManager", lambda db_url: type("M", (), {"get_session": lambda s: _Sess()})()
    )


WORK_A = str(uuid4())
WORK_B = str(uuid4())


@pytest.mark.parametrize(
    "argv, expected_rc, expected_message",
    [
        (
            ["--promote-pair", WORK_A, WORK_B],
            2,
            "REFUSING --promote-pair without --yes",
        ),
        (
            ["--promote-pair", "not-a-uuid", WORK_B, "--yes"],
            1,
            "REFUSING --promote-pair: 'not-a-uuid' is not a valid UUID.",
        ),
        (
            ["--promote-pair", WORK_A, "also-not-a-uuid", "--yes"],
            1,
            "REFUSING --promote-pair: 'also-not-a-uuid' is not a valid UUID.",
        ),
    ],
    ids=["missing_yes", "non_uuid_a", "non_uuid_b"],
)
def test_promote_pair_cli_guards(monkeypatch, capsys, argv, expected_rc, expected_message):
    _patch_db(monkeypatch)
    rc = clean_catalog.main(argv)
    assert rc == expected_rc
    assert expected_message in capsys.readouterr().out


def test_promote_pair_refused_on_non_prod_url(monkeypatch, capsys):
    _patch_db(monkeypatch, url="postgresql://u:p@localhost/db")
    rc = clean_catalog.main(["--promote-pair", WORK_A, WORK_B, "--yes"])
    assert rc == 2
    assert "REFUSING --promote-pair: 'localhost/db' is not a live prod DB" in capsys.readouterr().out


def test_promote_pair_self_pair_refused(monkeypatch, capsys):
    """A==B is rejected by dedup_backfill.promote_detected_duplicate_pair (a ValueError naming
    the id) — the CLI catches it and refuses with exit 1."""
    _patch_db(monkeypatch)

    def _raise_self_pair(session, work_id_a, work_id_b):
        raise ValueError(f"cannot promote {work_id_a} as a duplicate of itself")

    monkeypatch.setattr(clean_catalog.dedup_backfill, "promote_detected_duplicate_pair", _raise_self_pair)

    rc = clean_catalog.main(["--promote-pair", WORK_A, WORK_A, "--yes"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "REFUSING --promote-pair" in out
    assert WORK_A in out


def test_promote_pair_missing_work_id_refused(monkeypatch, capsys):
    """UnknownWorkIdsError (a ValueError subclass) is caught the same way and lists the
    missing id(s)."""
    _patch_db(monkeypatch)
    missing_id = UUID(WORK_B)

    def _raise_missing(session, work_id_a, work_id_b):
        raise clean_catalog.dedup_backfill.UnknownWorkIdsError([missing_id])

    monkeypatch.setattr(clean_catalog.dedup_backfill, "promote_detected_duplicate_pair", _raise_missing)

    rc = clean_catalog.main(["--promote-pair", WORK_A, WORK_B, "--yes"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "REFUSING --promote-pair" in out
    assert WORK_B in out


def test_promote_pair_happy_path_prints_and_reminds(monkeypatch, capsys):
    """Insert seam mocked — asserts the CLI's own print/dispatch wiring: per-pair 'promoted:'
    line plus the final --merge-works reminder."""
    _patch_db(monkeypatch)

    def _fake_promote(session, work_id_a, work_id_b):
        return clean_catalog.dedup_backfill.PromoteDuplicatePairResult(
            work_id_a=work_id_a,
            work_id_b=work_id_b,
            title_a="Title A",
            title_b="Title B",
            already_existed=False,
        )

    monkeypatch.setattr(clean_catalog.dedup_backfill, "promote_detected_duplicate_pair", _fake_promote)

    rc = clean_catalog.main(["--promote-pair", WORK_A, WORK_B, "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"promoted: Title A {WORK_A} + Title B {WORK_B}" in out
    assert "re-run --merge-works for a fresh gated report." in out


def test_promote_pair_already_promoted_prints_already_promoted(monkeypatch, capsys):
    _patch_db(monkeypatch)

    def _fake_promote(session, work_id_a, work_id_b):
        return clean_catalog.dedup_backfill.PromoteDuplicatePairResult(
            work_id_a=work_id_a,
            work_id_b=work_id_b,
            title_a="Title A",
            title_b="Title B",
            already_existed=True,
        )

    monkeypatch.setattr(clean_catalog.dedup_backfill, "promote_detected_duplicate_pair", _fake_promote)

    rc = clean_catalog.main(["--promote-pair", WORK_A, WORK_B, "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"already promoted: Title A {WORK_A} + Title B {WORK_B}" in out


def test_promote_pair_repeatable_flag_promotes_two_pairs(monkeypatch, capsys):
    """--promote-pair passed twice (argparse action='append') promotes both pairs in one
    invocation — the insert seam is called once per pair."""
    _patch_db(monkeypatch)
    work_c, work_d = str(uuid4()), str(uuid4())
    calls = []

    def _fake_promote(session, work_id_a, work_id_b):
        calls.append((work_id_a, work_id_b))
        return clean_catalog.dedup_backfill.PromoteDuplicatePairResult(
            work_id_a=work_id_a,
            work_id_b=work_id_b,
            title_a="Title",
            title_b="Title",
            already_existed=False,
        )

    monkeypatch.setattr(clean_catalog.dedup_backfill, "promote_detected_duplicate_pair", _fake_promote)

    rc = clean_catalog.main(["--promote-pair", WORK_A, WORK_B, "--promote-pair", work_c, work_d, "--yes"])
    assert rc == 0
    assert calls == [(UUID(WORK_A), UUID(WORK_B)), (UUID(work_c), UUID(work_d))]
    out = capsys.readouterr().out
    assert out.count("promoted: Title") == 2
