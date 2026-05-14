"""Tests for download URL detection in web_crawler"""
from src.tools.web.web_crawler import Click

def test_is_download_url_pdf():
    assert Click._is_download_url("https://example.com/report.pdf") is True

def test_is_download_url_xlsx():
    assert Click._is_download_url("https://example.com/data.xlsx") is True

def test_is_download_url_csv():
    assert Click._is_download_url("https://example.com/export.csv") is True

def test_is_download_url_docx():
    assert Click._is_download_url("https://example.com/doc.docx") is True

def test_is_download_url_html():
    assert Click._is_download_url("https://example.com/page.html") is False

def test_is_download_url_no_extension():
    assert Click._is_download_url("https://example.com/page") is False

def test_is_download_hint_query():
    assert Click._is_download_hint("https://example.com/api?download=true") is True

def test_is_download_hint_format():
    assert Click._is_download_hint("https://example.com/api?format=xlsx") is True

def test_is_download_hint_normal():
    assert Click._is_download_hint("https://example.com/page?q=test") is False
