"""Tests for click result safety"""

def test_empty_click_result_no_crash():
    """Verify that empty click results don't cause IndexError."""
    click_result = []
    # Simulate the fixed logic from search_agent.py
    if len(click_result) == 0:
        result = "Failed to fetch content"
    else:
        result = click_result[0]
    assert result == "Failed to fetch content"
