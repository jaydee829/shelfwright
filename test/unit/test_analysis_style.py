from dataclasses import dataclass

import pytest

from agentic_librarian.api import analysis_style as m


@pytest.fixture(autouse=True)
def clear_anchor_cache():
    m._anchor_cache.clear()
    yield
    m._anchor_cache.clear()


def test_score_axis_low_anchor_is_zero():
    low = [0.0, 0.0]
    high = [1.0, 0.0]
    assert m.score_axis(low, low, high) == 0.0


def test_score_axis_high_anchor_is_one():
    low = [0.0, 0.0]
    high = [1.0, 0.0]
    assert m.score_axis(high, low, high) == 1.0


def test_score_axis_midpoint_is_half():
    low = [0.0, 0.0]
    high = [2.0, 0.0]
    assert m.score_axis([1.0, 0.0], low, high) == 0.5


def test_score_axis_clamps_below_zero_and_above_one():
    low = [0.0, 0.0]
    high = [1.0, 0.0]
    assert m.score_axis([-3.0, 0.0], low, high) == 0.0
    assert m.score_axis([5.0, 0.0], low, high) == 1.0


def test_score_axis_degenerate_anchor_returns_none():
    assert m.score_axis([1.0, 1.0], [0.5, 0.5], [0.5, 0.5]) is None


@dataclass
class _Style:
    name: str
    embedding: list[float] | None


def _fake_embed_factory():
    """Embed anchors so 'high'/'more' phrases map near [1,0] and 'low' near [0,0]."""

    def embed(text: str) -> list[float]:
        hot = any(
            w in text
            for w in (
                "fast",
                "dense",
                "heavy",
                "interior",
                "comedic",
                "intimate",
                "archaic",
                "immersive",
            )
        )
        return [1.0, 0.0] if hot else [0.0, 0.0]

    return embed


def test_aggregate_radar_averages_scored_axes():
    embed = _fake_embed_factory()
    fast = _Style("fast-paced", [1.0, 0.0])  # near high anchor -> ~1
    slow = _Style("slow-burn", [0.0, 0.0])  # near low anchor  -> ~0
    maps = [{"pacing": fast}, {"pacing": slow}]
    radar = m.aggregate_radar(maps, embed)
    assert radar["pace"] == 0.5  # mean of 1 and 0
    assert radar["humor"] is None  # no data on this axis


def test_aggregate_radar_no_embedder_is_all_none():
    fast = _Style("fast-paced", [1.0, 0.0])
    radar = m.aggregate_radar([{"pacing": fast}], None)
    assert set(radar) == set(m.AXES)
    assert all(v is None for v in radar.values())


def test_aggregate_radar_skips_styles_without_embedding():
    embed = _fake_embed_factory()
    radar = m.aggregate_radar([{"pacing": _Style("fast-paced", None)}], embed)
    assert radar["pace"] is None


def test_aggregate_cloud_counts_titlecased_nominal_styles():
    maps = [
        {"tone": _Style("atmospheric", None), "perspective": _Style("first person", None)},
        {"tone": _Style("ATMOSPHERIC", None)},  # merges case-insensitively after titlecase
        {"pacing": _Style("fast-paced", None)},  # radar attr -> NOT in cloud
    ]
    cloud = m.aggregate_cloud(maps)
    counts = {row["name"]: row["count"] for row in cloud}
    assert counts == {"Atmospheric": 2, "First Person": 1}
