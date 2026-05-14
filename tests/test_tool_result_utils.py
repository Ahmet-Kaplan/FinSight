"""Tests for src.utils.tool_result_utils"""
from src.utils.tool_result_utils import safe_tool_results, safe_first_result, safe_data_preview
from src.tools.base import ToolResult

def test_safe_tool_results_none():
    assert safe_tool_results(None) == []

def test_safe_tool_results_non_list():
    tr = ToolResult(name="test", description="desc", data="data", source="src")
    result = safe_tool_results(tr)
    assert isinstance(result, list)
    assert len(result) == 1

def test_safe_tool_results_list():
    items = [ToolResult(name="a", description="", data=1, source="")]
    assert safe_tool_results(items) == items

def test_safe_first_result_empty():
    assert safe_first_result(None) is None
    assert safe_first_result([]) is None
    assert safe_first_result(None, default="fallback") == "fallback"

def test_safe_data_preview_none():
    assert "<None>" in safe_data_preview(None)

def test_safe_data_preview_dict():
    result = safe_data_preview({"a": 1, "b": 2})
    assert "a" in result

def test_safe_data_preview_list():
    result = safe_data_preview([1, 2, 3, 4, 5, 6], max_rows=3)
    assert "1" in result
