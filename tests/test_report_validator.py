"""Tests for src.utils.report_validator"""
import os
import tempfile
from src.utils.report_validator import validate_report_language, validate_image_references, validate_report

def test_validate_english_no_chinese():
    content = "This is a report about Apple Inc. Revenue was $100B in 2024."
    warnings = validate_report_language(content, 'en')
    assert len(warnings) == 0

def test_validate_english_with_chinese():
    content = "This is a report. " + "中文内容" * 100
    warnings = validate_report_language(content, 'en')
    assert len(warnings) > 0
    assert "Chinese characters" in warnings[0]

def test_validate_chinese_report():
    content = "这是一份关于苹果公司的报告。" * 50
    warnings = validate_report_language(content, 'zh')
    assert len(warnings) == 0

def test_validate_missing_images():
    content = "![chart](nonexistent_chart.png)"
    with tempfile.TemporaryDirectory() as tmpdir:
        warnings = validate_image_references(content, tmpdir)
        assert len(warnings) > 0
        assert "not found" in warnings[0]

def test_validate_existing_images():
    with tempfile.TemporaryDirectory() as tmpdir:
        img_path = os.path.join(tmpdir, "chart.png")
        with open(img_path, 'w') as f:
            f.write("fake image")
        content = f"![chart]({img_path})"
        warnings = validate_image_references(content, tmpdir)
        assert len(warnings) == 0

def test_validate_report_empty():
    warnings = validate_report("", 'en', "/tmp")
    assert len(warnings) == 0
