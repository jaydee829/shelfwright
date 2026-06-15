from datetime import date, timedelta

from agentic_librarian.mcp.server import reread_eligibility


def test_reread_eligibility_just_over_two_years_is_candidate():
    completed = date.today() - timedelta(days=int(2.0 * 365.25) + 5)
    is_candidate, years = reread_eligibility(completed)
    assert is_candidate is True
    assert years > 2.0


def test_reread_eligibility_just_under_two_years_is_not_candidate():
    completed = date.today() - timedelta(days=int(2.0 * 365.25) - 5)
    is_candidate, years = reread_eligibility(completed)
    assert is_candidate is False
    assert years < 2.0
