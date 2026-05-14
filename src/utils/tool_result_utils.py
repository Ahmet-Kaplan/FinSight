"""
Tool Result Safety Helpers

Utilities that normalise tool return values so that downstream code never
crashes on ``None``, non-list, or empty results.
"""

from __future__ import annotations

from typing import Any, List, Optional


def safe_tool_results(results: Any) -> list:
    """Ensure *results* is always a ``list``.

    Handles ``None``, a single non-list object, and already-correct lists.

    Args:
        results: Raw return value from ``tool.api_function()``.

    Returns:
        A (possibly empty) list.
    """
    if results is None:
        return []
    if not isinstance(results, list):
        return [results]
    return results


def safe_first_result(results: Any, default: Any = None) -> Any:
    """Safely retrieve the ``.data`` attribute of the first ``ToolResult``.

    Args:
        results: Raw return value from a tool.
        default: Value to return when the list is empty or the first item
            has no ``.data`` attribute.

    Returns:
        The first result's data, or *default*.
    """
    items = safe_tool_results(results)
    if not items:
        return default
    return getattr(items[0], 'data', default)


def safe_data_preview(data: Any, max_rows: int = 5) -> str:
    """Return a short, human-readable preview of *data*.

    Works with ``DataFrame``, ``dict``, ``list``, ``str``, and ``None``.

    Args:
        data: The data to preview.
        max_rows: Maximum rows to show for DataFrames.

    Returns:
        A string preview (never raises).
    """
    if data is None:
        return "<None>"
    try:
        # pandas DataFrame
        if hasattr(data, 'head') and callable(data.head):
            return str(data.head(max_rows))
        # dict
        if isinstance(data, dict):
            items = list(data.items())[:max_rows]
            return str(dict(items))
        # list
        if isinstance(data, list):
            return str(data[:max_rows])
        # generic
        preview = str(data)
        return preview[:500] if len(preview) > 500 else preview
    except Exception:
        return f"<{type(data).__name__} — preview unavailable>"
