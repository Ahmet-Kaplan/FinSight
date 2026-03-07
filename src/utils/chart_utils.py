"""
Chart Utility Helpers

Font detection, safe chart styling, and filename sanitization for FinSight charts.
"""

import re
import matplotlib.font_manager as fm


def detect_available_font(preferred_fonts: list) -> str | None:
    """Return first available font from preferred list, or None.

    Args:
        preferred_fonts: Ordered list of font family names to check.

    Returns:
        The first font name that is installed, or None if none are available.
    """
    available = {f.name for f in fm.fontManager.ttflist}
    for font in preferred_fonts:
        if font in available:
            return font
    return None


def can_render_cjk() -> bool:
    """Check if any CJK-capable font is available on this system."""
    cjk_fonts = [
        'SimHei', 'KaiTi', 'WenQuanYi Micro Hei', 'Noto Sans CJK SC',
        'Noto Sans CJK', 'Arial Unicode MS', 'Microsoft YaHei',
        'PingFang SC', 'Hiragino Sans GB',
    ]
    return detect_available_font(cjk_fonts) is not None


def get_safe_chart_style(lang_code: str) -> dict:
    """Return safe matplotlib rcParams dict for the given language.

    Args:
        lang_code: 'en' or 'zh'.

    Returns:
        Dict suitable for ``matplotlib.rcParams.update()``.
    """
    font = None
    if lang_code == 'zh':
        font = detect_available_font([
            'SimHei', 'KaiTi', 'WenQuanYi Micro Hei',
            'Noto Sans CJK SC', 'PingFang SC', 'Arial Unicode MS',
        ])
    if not font:
        font = 'DejaVu Sans'
    return {'font.family': font, 'axes.unicode_minus': False}


def sanitize_chart_filename(name: str, max_length: int = 60) -> str:
    """Make a filename safe for all operating systems.

    Strips non-ASCII characters, replaces whitespace with underscores, and
    truncates to *max_length*.

    Args:
        name: Raw filename (may contain CJK, special chars, etc.).
        max_length: Maximum character length for the returned name.

    Returns:
        A safe, non-empty filename string.
    """
    # Remove everything except word chars, whitespace, hyphens, and dots
    safe = re.sub(r'[^\w\s\-.]', '', name)
    safe = re.sub(r'\s+', '_', safe.strip())
    result = safe[:max_length] if safe else 'chart'
    # Ensure we don't return an empty string after truncation
    return result if result else 'chart'
