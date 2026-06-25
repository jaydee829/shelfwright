"""Fetch the full OverDrive/Libby library directory and write it as a static snapshot.

WHY: Thunder's `/v2/libraries` endpoint ignores its `query` param entirely — it always returns
the same full ~13k-library list (confirmed: `query=zzz` returns the same `totalItems`). Libby's
real autocomplete host is gated (403). So the library picker can't search live; instead we ship a
committed `{slug, name}` snapshot and filter it server-side (see `availability/directory.py`).

This script regenerates that snapshot. It's near-static data — re-run occasionally (or from a
scheduled job) to pick up new libraries. One-time cost: ~130 paged requests to the unofficial
endpoint.

Usage:  uv run python scripts/fetch_library_directory.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests

_THUNDER = "https://thunder.api.overdrive.com"
_CLIENT = "dewey"
_PER_PAGE = 100  # Thunder caps perPage at 100 (200+ returns nothing)
_OUT = Path(__file__).resolve().parent.parent / "src" / "agentic_librarian" / "availability" / "library_directory.json"


def _page(page: int) -> dict:
    url = f"{_THUNDER}/v2/libraries?perPage={_PER_PAGE}&page={page}&x-client-id={quote_plus(_CLIENT)}"
    resp = requests.get(url, timeout=15, headers={"Accept": "application/json"})
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    first = _page(1)
    last_page = first.get("links", {}).get("last", {}).get("page", 1)
    total = first.get("totalItems")
    print(f"directory: {total} libraries across {last_page} pages (perPage={_PER_PAGE})")

    by_slug: dict[str, str] = {}

    def _collect(payload: dict) -> None:
        for item in payload.get("items", []):
            slug = (item.get("preferredKey") or item.get("advantageKey") or "").strip()
            name = (item.get("name") or "").strip()
            if slug and name:
                by_slug[slug] = name

    _collect(first)
    for page in range(2, last_page + 1):
        try:
            _collect(_page(page))
        except requests.RequestException as exc:
            print(f"  page {page} failed ({exc}); retrying once...", file=sys.stderr)
            time.sleep(1.0)
            _collect(_page(page))
        if page % 20 == 0:
            print(f"  ...page {page}/{last_page} ({len(by_slug)} unique so far)")
        time.sleep(0.05)  # be polite to the unofficial endpoint

    records = [{"slug": slug, "name": name} for slug, name in by_slug.items()]
    records.sort(key=lambda r: r["name"].lower())
    _OUT.write_text(json.dumps(records, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"wrote {len(records)} libraries -> {_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
