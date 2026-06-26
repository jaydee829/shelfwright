"""Pure URL builders for 'where to get this book'. No I/O — never calls a network. The
Libby link is per saved library; the rest are single catalog/retail search links. Order
is free → local → retail: Libby, Hoopla, Bookshop.org, Amazon."""

from __future__ import annotations

from urllib.parse import quote_plus


def _retail_query(title: str, author: str) -> str:
    return quote_plus(f"{title} {author}".strip())


def _libby_url(slug: str, title: str) -> str:
    # Libby web-app search scoped to one library. NOTE: confirm the exact path segments
    # against a live libbyapp.com session in review; this is the documented format.
    return f"https://libbyapp.com/search/{slug}/search/query-{quote_plus(title)}/page-1"


def build_links(title: str, author: str, *, libraries: list[dict]) -> list[dict]:
    """libraries: [{"slug","name"}] in the user's priority order. Returns ordered link dicts
    {kind,label,url}."""
    links: list[dict] = []
    for lib in libraries:
        links.append(
            {
                "kind": "libby",
                "label": f"{lib['name']} on Libby",
                "url": _libby_url(lib["slug"], title),
            }
        )
    links.append(
        {
            "kind": "hoopla",
            "label": "Search Hoopla",
            "url": f"https://www.hoopladigital.com/search?q={quote_plus(title)}",
        }
    )
    links.append(
        {
            "kind": "bookshop",
            "label": "Bookshop.org",
            "url": f"https://bookshop.org/search?keywords={_retail_query(title, author)}",
        }
    )
    links.append(
        {
            "kind": "amazon",
            "label": "Amazon",
            "url": f"https://www.amazon.com/s?k={_retail_query(title, author)}",
        }
    )
    return links
