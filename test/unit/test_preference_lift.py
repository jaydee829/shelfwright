"""Unit tests for `_lift_ranked`, the pure trope-preference ranking helper (#125 #70).

Raw link frequency let fallback-pollution attractors (ubiquitous across the catalog)
bury a user's real, over-indexed taste. Lift = (user share) / (catalog share), so a
trope the user reads far more often than the catalog baseline ranks above one that is
merely common everywhere."""

from __future__ import annotations

import pytest

from agentic_librarian.mcp.server import _lift_ranked


@pytest.mark.parametrize(
    ("user_counts", "catalog_counts", "user_works", "catalog_works", "limit", "expected"),
    [
        pytest.param(
            {"Attractor": 6, "Specific": 4},
            {"Attractor": 60, "Specific": 5},
            10,
            100,
            10,
            ["Specific", "Attractor"],
            id="ubiquity_deflation",
        ),
        pytest.param(
            {"Found Family": 8, "Attractor": 6},
            {"Found Family": 36, "Attractor": 60},
            10,
            100,
            10,
            ["Found Family", "Attractor"],
            id="genuine_love_retention",
        ),
        pytest.param(
            {"Alpha": 4, "Beta": 4},
            {"Alpha": 10, "Beta": 10},
            10,
            100,
            10,
            ["Alpha", "Beta"],
            id="tie_break_equal_lift_and_count_falls_back_to_name",
        ),
        pytest.param(
            {"HighCount": 6, "LowCount": 3},
            {"HighCount": 21, "LowCount": 10},
            10,
            100,
            10,
            ["HighCount", "LowCount"],
            id="tie_break_equal_lift_prefers_higher_raw_user_count",
        ),
        pytest.param(
            {"Anything": 5},
            {},
            0,
            100,
            10,
            [],
            id="degenerate_zero_user_works_returns_empty",
        ),
        pytest.param(
            {"Anything": 5},
            {},
            10,
            0,
            10,
            [],
            id="degenerate_zero_catalog_works_returns_empty",
        ),
        pytest.param(
            {"NoCatalogEntry": 3},
            {},
            10,
            100,
            10,
            ["NoCatalogEntry"],
            id="trope_absent_from_catalog_counts_uses_smoothing_no_error",
        ),
        pytest.param(
            {"A": 5, "B": 4, "C": 3},
            {"A": 5, "B": 4, "C": 3},
            10,
            10,
            2,
            ["A", "B"],
            id="limit_respected",
        ),
    ],
)
def test_lift_ranked(user_counts, catalog_counts, user_works, catalog_works, limit, expected):
    assert _lift_ranked(user_counts, catalog_counts, user_works, catalog_works, limit) == expected
