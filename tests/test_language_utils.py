"""Tests for src.utils.language_utils"""
from src.utils.language_utils import (
    get_language_display_name,
    get_chart_font_for_language,
    get_chart_label_language_instruction,
)

def test_get_language_display_name_en():
    assert get_language_display_name('en') == 'English'

def test_get_language_display_name_zh():
    assert '中文' in get_language_display_name('zh')

def test_get_language_display_name_unknown():
    assert get_language_display_name('fr') == 'fr'

def test_get_chart_font_for_language_en():
    assert get_chart_font_for_language('en') == 'DejaVu Sans'

def test_get_chart_label_instruction_en():
    result = get_chart_label_language_instruction('en')
    assert 'English' in result
    # Should not mention Chinese
    assert '中文' not in result

def test_get_chart_label_instruction_zh():
    result = get_chart_label_language_instruction('zh')
    assert '中文' in result
