"""Availability orchestration: normalize → read-through the availability_cache → on miss
fetch from Thunder → shape per-format → write through. Pure helpers (_normalize, _shape_formats,
_best_match) are unit-tested; availability_for() needs a Session (integration-tested).

Title-matching policy: a Thunder item matches only on normalized-title equality. Author is a
SOFT confirm (preferred when several items share a title) — we under-claim rather than show a
wrong 'available now'. Each format (ebook/audiobook) is matched independently."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from agentic_librarian.availability import overdrive
from agentic_librarian.availability.overdrive import ThunderError
from agentic_librarian.db.models import AvailabilityCache

logger = logging.getLogger(__name__)

_PROVIDER = "libby"
_FORMATS = (("ebook", "eBook"), ("audiobook", "Audiobook"))


def _ttl() -> timedelta:
    return timedelta(seconds=int(os.environ.get("AVAILABILITY_TTL_SECONDS", "14400")))  # 4h


def _normalize(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _item_author(item: dict) -> str:
    if item.get("firstCreatorName"):
        return item["firstCreatorName"]
    creators = item.get("creators") or []
    return creators[0].get("name", "") if creators else ""


def _best_match(items: list[dict], title: str, author: str) -> dict | None:
    nt = _normalize(title)
    cands = [it for it in items if _normalize(it.get("title", "")) == nt]
    if not cands:
        return None
    if author:
        na = set(_normalize(author).split())
        for it in cands:
            if na & set(_normalize(_item_author(it)).split()):
                return it
    return cands[0]


def _shape_formats(items: list[dict], title: str, author: str) -> list[dict]:
    """One entry per format that has a confident title match."""
    out: list[dict] = []
    for fmt_id, fmt_label in _FORMATS:
        fmt_items = [it for it in items if (it.get("type") or {}).get("id") == fmt_id]
        match = _best_match(fmt_items, title, author)
        if match is None:
            continue
        out.append(
            {
                "format": fmt_label,
                "available": bool(match.get("isAvailable")),
                "copies_owned": match.get("ownedCopies"),
                "copies_available": match.get("availableCopies"),
                "holds_ratio": match.get("holdsRatio"),
                "wait_days": match.get("estimatedWaitDays"),
            }
        )
    return out


def availability_for(session: Session, library: dict, title: str, author: str) -> list[dict] | None:
    """Single-lookup read-through cache (request paths use batch_availability, GH #94). Read-
    through cache for ONE (library, title, author). Returns the per-format list, or None if
    Thunder failed (caller degrades to links-only). A fresh cache row → zero upstream calls.
    Empty list (matched nothing) is a real, cacheable result."""
    nt, na = _normalize(title), _normalize(author)
    slug = library["slug"]
    now = datetime.now(UTC)
    row = session.get(AvailabilityCache, (_PROVIDER, slug, nt, na))
    if row is not None and (now - row.fetched_at.replace(tzinfo=UTC)) < _ttl():
        return row.payload.get("formats", [])

    try:
        # raw title sent to Thunder (better search relevance); the cache is keyed on nt.
        items = overdrive.fetch_media(slug, title)
    except ThunderError:
        return None  # degrade: no badge, links unaffected

    formats = _shape_formats(items, title, author)
    payload = {"formats": formats}
    if row is None:
        session.add(
            AvailabilityCache(
                provider=_PROVIDER,
                library_slug=slug,
                norm_title=nt,
                norm_author=na,
                payload=payload,
                fetched_at=now,
            )
        )
    else:
        row.payload = payload
        row.fetched_at = now
    session.flush()
    return formats


def batch_availability(db_manager, libs: list[dict], items: list[tuple[str, str]]) -> dict:
    """Batch read-through cache in THREE phases (GH #94): (1) one short session reads
    every fresh cache row; (2) Thunder fetches for the misses run with NO session held
    (previously each miss pinned the request's connection idle-in-transaction);
    (3) one short session writes the fetched payloads back. Returns
    {(slug, title, author): formats-list | None} — None means Thunder failed for that
    lookup (caller degrades to links-only; the ALWAYS-200 contract is unchanged)."""
    now = datetime.now(UTC)
    results: dict = {}
    misses: list[tuple[dict, str, str]] = []

    with db_manager.get_session() as session:
        for lib in libs:
            for title, author in items:
                nt, na = _normalize(title), _normalize(author)
                row = session.get(AvailabilityCache, (_PROVIDER, lib["slug"], nt, na))
                if row is not None and (now - row.fetched_at.replace(tzinfo=UTC)) < _ttl():
                    results[(lib["slug"], title, author)] = row.payload.get("formats", [])
                else:
                    misses.append((lib, title, author))

    fetched: dict = {}
    for lib, title, author in misses:
        try:
            items_raw = overdrive.fetch_media(lib["slug"], title)  # raw title: better relevance
        except ThunderError as exc:
            logger.warning("availability fetch failed for %r at %r: %s", title, lib["slug"], exc)
            results[(lib["slug"], title, author)] = None  # degrade: no badge
            continue
        fetched[(lib["slug"], title, author)] = _shape_formats(items_raw, title, author)

    if fetched:
        try:
            with db_manager.get_session() as session:
                for (slug, title, author), formats in fetched.items():
                    nt, na = _normalize(title), _normalize(author)
                    row = session.get(AvailabilityCache, (_PROVIDER, slug, nt, na))
                    payload = {"formats": formats}
                    if row is None:
                        session.add(
                            AvailabilityCache(
                                provider=_PROVIDER,
                                library_slug=slug,
                                norm_title=nt,
                                norm_author=na,
                                payload=payload,
                                fetched_at=now,
                            )
                        )
                    else:
                        row.payload = payload
                        row.fetched_at = now
                    session.flush()
        except Exception as exc:  # noqa: BLE001 - cache write-back is best-effort (ALWAYS-200)
            # GH #110 covers the durable upsert; until then write-back is best-effort
            logger.warning("availability cache write-back failed (concurrent insert?): %s", exc)
        results.update(fetched)
    return results
