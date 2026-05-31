import json
from pathlib import Path

import numpy as np

FIXTURE = Path(__file__).parent.parent / "data" / "trope_embeddings.json"
ROMANCE = ["enemies to lovers", "slow burn romance"]
GRIMDARK = ["grimdark war", "brutal military strategy"]


def _cos(a, b):
    a, b = np.array(a), np.array(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def test_fixture_has_all_strings_at_1536d():
    data = json.loads(FIXTURE.read_text())
    for s in ROMANCE + GRIMDARK:
        assert s in data, f"missing {s!r}"
        assert len(data[s]) == 1536


def test_fixture_clusters_are_separable():
    data = json.loads(FIXTURE.read_text())
    within = min(_cos(data[ROMANCE[0]], data[ROMANCE[1]]), _cos(data[GRIMDARK[0]], data[GRIMDARK[1]]))
    cross = max(_cos(data[r], data[g]) for r in ROMANCE for g in GRIMDARK)
    assert within > cross, f"within-cluster {within:.4f} must exceed cross-cluster {cross:.4f}"
