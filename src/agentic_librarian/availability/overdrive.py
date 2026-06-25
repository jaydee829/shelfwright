"""OverDrive 'Thunder' client — the SINGLE module touching OverDrive's unofficial public
API (the same endpoints libbyapp.com's frontend calls; no auth, x-client-id=dewey). It is
undocumented and NOT covered by OverDrive's developer agreement: isolated here so it can be
swapped for the official partner API later, and so every caller degrades on ThunderError."""

from __future__ import annotations

from urllib.parse import quote_plus

import requests

_THUNDER = "https://thunder.api.overdrive.com"
_CLIENT = "dewey"
_TIMEOUT = 8  # seconds — one slow library must not hang a request


class ThunderError(Exception):
    """Any failure talking to Thunder. Callers catch this and degrade to links-only."""


def _http_get_json(url: str) -> dict:
    """The one network seam (tests monkeypatch this)."""
    resp = requests.get(url, timeout=_TIMEOUT, headers={"Accept": "application/json"})
    resp.raise_for_status()
    return resp.json()


def search_libraries(query: str) -> list[dict]:
    """Public OverDrive directory search — powers the picker. Returns [{slug, name}]."""
    url = f"{_THUNDER}/v2/libraries?query={quote_plus(query)}&x-client-id={_CLIENT}"
    try:
        data = _http_get_json(url)
    except Exception as exc:  # noqa: BLE001 - normalize every failure to ThunderError
        raise ThunderError(str(exc)) from exc
    out = []
    for item in data.get("items", []):
        slug = item.get("preferredKey") or item.get("advantageKey")
        name = item.get("name")
        if slug and name:
            out.append({"slug": slug, "name": name})
    return out


def fetch_media(slug: str, title: str) -> list[dict]:
    """Per-library catalog search (ebook+audiobook) with availability inline. Returns the
    raw `items` list; matching/shaping is the service's job."""
    url = (
        f"{_THUNDER}/v2/libraries/{quote_plus(slug)}/media"
        f"?query={quote_plus(title)}&format=ebook-overdrive,audiobook-overdrive"
        f"&perPage=24&x-client-id={_CLIENT}"
    )
    try:
        data = _http_get_json(url)
    except Exception as exc:  # noqa: BLE001
        raise ThunderError(str(exc)) from exc
    return data.get("items", [])
