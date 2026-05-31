"""One-time generator for the cached trope-embedding test fixture (api_dependent).

Run manually in an environment with GOOGLE_SEARCH_API_KEY set:
    python scripts/gen_trope_embedding_fixture.py

Embeds a curated set of trope strings spanning two semantic clusters with
gemini-embedding-001 at 1536-d and writes test/data/trope_embeddings.json. Prints the
pairwise cosine matrix so cluster separation can be sanity-checked before committing.
"""

import json
import os
from itertools import combinations
from pathlib import Path

import numpy as np
from agentic_librarian.scouts.utils import EMBEDDING_DIMENSIONS
from google import genai
from google.genai import types

STRINGS = [
    "enemies to lovers",
    "slow burn romance",
    "grimdark war",
    "brutal military strategy",
]
OUT = Path("test/data/trope_embeddings.json")


def _cos(a: list[float], b: list[float]) -> float:
    a, b = np.array(a), np.array(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def main() -> None:
    api_key = os.environ.get("GOOGLE_SEARCH_API_KEY")
    if not api_key:
        raise SystemExit("GOOGLE_SEARCH_API_KEY required to generate the fixture.")
    client = genai.Client(api_key=api_key)

    vectors: dict[str, list[float]] = {}
    for s in STRINGS:
        resp = client.models.embed_content(
            model="gemini-embedding-001",
            contents=s,
            config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSIONS),
        )
        vectors[s] = list(resp.embeddings[0].values)

    print("Pairwise cosine similarity:")
    for x, y in combinations(STRINGS, 2):
        print(f"  {x!r} vs {y!r}: {_cos(vectors[x], vectors[y]):.4f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(vectors, indent=2))
    print(f"Wrote {OUT} ({len(vectors)} vectors, dim={EMBEDDING_DIMENSIONS}).")


if __name__ == "__main__":
    main()
