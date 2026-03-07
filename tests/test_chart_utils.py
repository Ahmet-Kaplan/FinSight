"""Tests for src.utils.chart_utils"""
from src.utils.chart_utils import detect_available_font, sanitize_chart_filename, can_render_cjk

def test_detect_available_font_with_fallback():
    # DejaVu Sans should always be available with matplotlib
    result = detect_available_font(['NonExistentFont', 'DejaVu Sans'])
    assert result == 'DejaVu Sans'

def test_detect_available_font_none():
    result = detect_available_font(['NonExistentFont1', 'NonExistentFont2'])
    assert result is None

def test_sanitize_chart_filename_ascii():
    assert sanitize_chart_filename('revenue_chart.png') == 'revenue_chart.png'

def test_sanitize_chart_filename_chinese():
    result = sanitize_chart_filename('ASML收入趋势图.png')
    assert result  # non-empty
    assert len(result) <= 60

def test_sanitize_chart_filename_empty():
    assert sanitize_chart_filename('') == 'chart'
    assert sanitize_chart_filename('!!!') == 'chart'

def test_sanitize_chart_filename_length():
    long_name = 'a' * 100 + '.png'
    result = sanitize_chart_filename(long_name, max_length=20)
    assert len(result) <= 20
