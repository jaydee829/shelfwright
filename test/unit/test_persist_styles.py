from agentic_librarian.etl.persist import _iter_style_items


def test_iter_style_items_keeps_strings_skips_nonstrings(capsys):
    data = {"perspective": "1st person", "blank": "", "bad": {"nested": "x"}, "missing": None}
    assert list(_iter_style_items(data, "Work 'X'")) == [("perspective", "1st person")]
    # Only the dict value warns; falsy values ("" / None) are dropped silently.
    out = capsys.readouterr().out
    assert "skipping non-string style 'bad'" in out
    assert "'blank'" not in out and "'missing'" not in out


def test_iter_style_items_handles_none():
    assert list(_iter_style_items(None, "Work 'X'")) == []
