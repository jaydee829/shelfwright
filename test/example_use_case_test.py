import pytest

"""
Refactored Example Test Case based on UC3.1: Comparative Exclusion.
Demonstrates the ANTI-HARDCODING mandate using parameterization.
"""

def apply_uc3_1_filters(results, excluded_author, required_style, series_only):
    """
    Generalized logic that should be tested against multiple data sets.
    """
    filtered = [
        r for r in results 
        if r["author"] != excluded_author 
        and any(s.strip() in r["style"] for s in required_style.split(","))
        and (not series_only or r["series_start"])
    ]
    return filtered

@pytest.mark.parametrize("mock_data, excluded_author, required_attribute, is_series_start, expected_titles", [
    # Case 1: Standard Epic Fantasy filter
    (
        [
            {"title": "Words of Radiance", "author": "Brandon Sanderson", "series_start": False, "style": "epic"},
            {"title": "The Blade Itself", "author": "Joe Abercrombie", "series_start": True, "style": "gritty, epic"},
            {"title": "Gardens of the Moon", "author": "Steven Erikson", "series_start": True, "style": "gritty, dense"}
        ],
        "Brandon Sanderson", "gritty", True, ["The Blade Itself", "Gardens of the Moon"]
    ),
    # Case 2: Different author and style
    (
        [
            {"title": "Project Hail Mary", "author": "Andy Weir", "series_start": False, "style": "sci-fi, witty"},
            {"title": "The Martian", "author": "Andy Weir", "series_start": False, "style": "sci-fi, technical"},
            {"title": "Neuromancer", "author": "William Gibson", "series_start": True, "style": "cyberpunk, noir"}
        ],
        "Andy Weir", "cyberpunk", False, ["Neuromancer"]
    ),
    # Case 3: Empty results (ensure no crashes)
    (
        [
            {"title": "Pride and Prejudice", "author": "Jane Austen", "series_start": False, "style": "romance"}
        ],
        "Jane Austen", "gritty", True, []
    )
])
def test_uc3_1_robust_logic(mock_data, excluded_author, required_attribute, is_series_start, expected_titles):
    """
    This test ensures the logic is robust by checking it against multiple 
    scenarios, preventing hardcoded 'if input == x, return y' shortcuts.
    """
    final_recommendations = apply_uc3_1_filters(mock_data, excluded_author, required_attribute, is_series_start)
    
    # Assertions
    assert len(final_recommendations) == len(expected_titles)
    
    actual_titles = [r["title"] for r in final_recommendations]
    for expected_title in expected_titles:
        assert expected_title in actual_titles

    # Verify constraints are actually met
    for rec in final_recommendations:
        assert rec["author"] != excluded_author
        if is_series_start:
            assert rec["series_start"] is True
