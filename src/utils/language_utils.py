"""
Language Utility Helpers

Centralised language resolution, display names, font selection, and prompt
instructions for multilingual FinSight reports and charts.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Language resolution
# ---------------------------------------------------------------------------

def resolve_output_language(config) -> str:
    """Return ``'en'`` or ``'zh'`` from a ``Config`` object, with safe default.

    Args:
        config: A ``src.config.Config`` instance (has ``.config`` dict).

    Returns:
        Language code string.
    """
    return config.config.get('language', 'en')


def get_language_display_name(lang_code: str) -> str:
    """Convert a language code to a human-readable display name.

    >>> get_language_display_name('en')
    'English'
    >>> get_language_display_name('zh')
    'Chinese (中文)'
    """
    mapping = {
        'zh': 'Chinese (中文)',
        'en': 'English',
    }
    return mapping.get(lang_code, lang_code)


# ---------------------------------------------------------------------------
# Chart font helpers
# ---------------------------------------------------------------------------

def get_chart_font_for_language(lang_code: str) -> str:
    """Return a safe font family name for chart rendering.

    For Chinese reports, attempts to find an installed CJK font; falls back
    to ``DejaVu Sans`` (universally available with matplotlib).

    Args:
        lang_code: ``'en'`` or ``'zh'``.

    Returns:
        Font family name string.
    """
    if lang_code == 'zh':
        from src.utils.chart_utils import detect_available_font
        font = detect_available_font([
            'SimHei', 'KaiTi', 'WenQuanYi Micro Hei',
            'Noto Sans CJK SC', 'PingFang SC', 'Arial Unicode MS',
        ])
        return font or 'DejaVu Sans'
    return 'DejaVu Sans'


def get_chart_label_language_instruction(lang_code: str) -> str:
    """Return a prompt instruction string for chart label language.

    This is injected into the ``draw_chart`` prompt so that the LLM generates
    chart code with labels in the correct language.

    Args:
        lang_code: ``'en'`` or ``'zh'``.

    Returns:
        Instruction string.
    """
    if lang_code == 'zh':
        return "All chart labels, axis titles, and legends must be in Chinese (中文)."
    return "All chart labels, axis titles, and legends must be in English."
