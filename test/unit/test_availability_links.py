from agentic_librarian.availability.links import build_links


def test_links_order_and_libby_per_library():
    libs = [
        {"slug": "kcls", "name": "King County LS"},
        {"slug": "spl", "name": "Seattle PL"},
    ]
    links = build_links("Project Hail Mary", "Andy Weir", libraries=libs)
    kinds = [link["kind"] for link in links]
    assert kinds == ["libby", "libby", "hoopla", "bookshop", "amazon"]
    assert links[0]["label"] == "King County LS on Libby"
    assert "kcls" in links[0]["url"]
    assert "spl" in links[1]["url"]


def test_links_with_no_libraries_still_has_retail_and_hoopla():
    links = build_links("Dune", "Frank Herbert", libraries=[])
    assert [link["kind"] for link in links] == ["hoopla", "bookshop", "amazon"]


def test_links_url_encode_special_characters():
    links = build_links("Cat & Mouse", "A. B", libraries=[])
    amazon = next(link for link in links if link["kind"] == "amazon")
    assert " " not in amazon["url"]
    assert "%26" in amazon["url"] or "+" in amazon["url"]  # '&' encoded
