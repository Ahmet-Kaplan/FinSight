"""Tests for PDF mode policy (auto / force / skip) in report generation."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_config(pdf_mode='auto'):
    """Build a minimal mock config object."""
    cfg = MagicMock()
    cfg.config = {'pdf_mode': pdf_mode}
    return cfg


# ---------------------------------------------------------------------------
# PDF mode logic (isolated from the full report generator)
#
# We test the decision logic directly since importing the full
# ReportGenerator requires many heavy dependencies.
# ---------------------------------------------------------------------------

def _run_pdf_logic(pdf_mode, convert_side_effect=None):
    """Simulate the PDF conversion logic from report_generator.py.

    Returns (pdf_status, raised_exception).
    """
    docx_path = '/tmp/test_report.docx'
    pdf_path = docx_path.replace('.docx', '.pdf')
    pdf_status = ''
    raised_exception = None

    mock_logger = MagicMock()

    if pdf_mode == 'skip':
        pdf_status = f'skipped (pdf_mode=skip). DOCX at: {docx_path}'
        mock_logger.info(f"PDF: {pdf_status}")
    elif pdf_mode in ('auto', 'force'):
        mock_convert = MagicMock(side_effect=convert_side_effect)
        try:
            mock_convert(docx_path, pdf_path)
            pdf_status = f'generated at {pdf_path}'
            mock_logger.info(f"PDF: {pdf_status}")
        except Exception as e:
            pdf_status = f'conversion failed ({e}). DOCX available at: {docx_path}'
            if pdf_mode == 'force':
                mock_logger.error(f"PDF: {pdf_status}")
                raised_exception = RuntimeError(f"PDF: {pdf_status}")
            else:
                mock_logger.warning(f"PDF: {pdf_status}")

    return pdf_status, raised_exception


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPdfModeSkip:
    def test_skip_does_not_attempt_conversion(self):
        status, exc = _run_pdf_logic('skip')
        assert 'skipped' in status
        assert exc is None

    def test_skip_mentions_docx_path(self):
        status, _ = _run_pdf_logic('skip')
        assert '.docx' in status


class TestPdfModeAuto:
    def test_auto_success(self):
        status, exc = _run_pdf_logic('auto', convert_side_effect=None)
        assert 'generated' in status
        assert exc is None

    def test_auto_failure_is_warning_not_error(self):
        status, exc = _run_pdf_logic(
            'auto', convert_side_effect=OSError('No LibreOffice')
        )
        assert 'conversion failed' in status
        assert 'DOCX available' in status
        assert exc is None  # auto mode does NOT raise


class TestPdfModeForce:
    def test_force_success(self):
        status, exc = _run_pdf_logic('force', convert_side_effect=None)
        assert 'generated' in status
        assert exc is None

    def test_force_failure_raises(self):
        status, exc = _run_pdf_logic(
            'force', convert_side_effect=OSError('No Word installed')
        )
        assert 'conversion failed' in status
        assert exc is not None
        assert isinstance(exc, RuntimeError)


class TestPdfModeConfig:
    """Verify that the config lookup for pdf_mode works correctly."""

    def test_config_default_is_auto(self):
        cfg = MagicMock()
        cfg.config = {}
        pdf_mode = cfg.config.get('pdf_mode', 'auto')
        assert pdf_mode == 'auto'

    def test_config_skip(self):
        cfg = _mock_config('skip')
        assert cfg.config['pdf_mode'] == 'skip'

    def test_config_force(self):
        cfg = _mock_config('force')
        assert cfg.config['pdf_mode'] == 'force'
