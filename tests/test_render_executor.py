"""Tests for src.executors.render_executor — Render API client with mocked httpx."""

import base64
import sys
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# We need to mock httpx at import time since render_executor guards it
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _ensure_httpx():
    """Ensure httpx is importable; skip if not available."""
    pytest.importorskip('httpx')


def _make_executor(api_key='rnd_test123', service_id='srv-test456'):
    from src.executors.render_executor import RenderExecutor
    return RenderExecutor(api_key=api_key, service_id=service_id)


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_valid_construction(self):
        ex = _make_executor()
        assert ex.api_key == 'rnd_test123'
        assert ex.service_id == 'srv-test456'

    def test_missing_api_key_raises(self):
        from src.executors.render_executor import RenderExecutor
        with pytest.raises(ValueError, match='RENDER_API_KEY'):
            RenderExecutor(api_key='', service_id='srv-test')

    def test_missing_service_id_raises(self):
        from src.executors.render_executor import RenderExecutor
        with pytest.raises(ValueError, match='RENDER_SERVICE_ID'):
            RenderExecutor(api_key='rnd_test', service_id='')


# ---------------------------------------------------------------------------
# Start command building
# ---------------------------------------------------------------------------

class TestBuildStartCommand:
    def test_base64_config_in_command(self):
        ex = _make_executor()
        yaml_content = 'target_name: ASML\nlanguage: en\n'
        cmd = ex._build_start_command(yaml_content, [])
        b64_expected = base64.b64encode(yaml_content.encode()).decode()
        assert b64_expected in cmd
        assert 'python run_report.py --executor local' in cmd

    def test_cli_args_forwarded(self):
        ex = _make_executor()
        cmd = ex._build_start_command('config: test', ['--verbose', '--pdf-mode', 'skip'])
        assert '--verbose' in cmd
        assert '--pdf-mode skip' in cmd

    def test_empty_cli_args(self):
        ex = _make_executor()
        cmd = ex._build_start_command('config: test', [])
        assert cmd.endswith('python run_report.py --executor local ')


# ---------------------------------------------------------------------------
# HTTP helpers with retry (using mocked client)
# ---------------------------------------------------------------------------

class TestHttpHelpers:
    def test_api_get_success(self):
        ex = _make_executor()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'id': 'job-123', 'status': 'running'}
        mock_resp.raise_for_status = MagicMock()
        ex._client.request = MagicMock(return_value=mock_resp)

        result = ex._api_get('/services/srv-test/jobs/job-123')
        assert result == {'id': 'job-123', 'status': 'running'}

    def test_api_post_success(self):
        ex = _make_executor()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'id': 'job-new'}
        mock_resp.raise_for_status = MagicMock()
        ex._client.request = MagicMock(return_value=mock_resp)

        result = ex._api_post('/services/srv-test/jobs', json={'startCommand': 'echo hi'})
        assert result == {'id': 'job-new'}

    def test_auth_error_returns_none(self):
        ex = _make_executor()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        ex._client.request = MagicMock(return_value=mock_resp)

        result = ex._api_get('/test')
        assert result is None

    def test_forbidden_returns_none(self):
        ex = _make_executor()
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        ex._client.request = MagicMock(return_value=mock_resp)

        result = ex._api_get('/test')
        assert result is None

    @patch('time.sleep')  # don't actually wait during retries
    def test_retry_on_500(self, mock_sleep):
        ex = _make_executor()
        fail_resp = MagicMock()
        fail_resp.status_code = 500

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {'ok': True}
        ok_resp.raise_for_status = MagicMock()

        ex._client.request = MagicMock(side_effect=[fail_resp, ok_resp])
        result = ex._api_get('/test')
        assert result == {'ok': True}
        assert ex._client.request.call_count == 2

    @patch('time.sleep')
    def test_retry_on_429(self, mock_sleep):
        ex = _make_executor()
        rate_resp = MagicMock()
        rate_resp.status_code = 429

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {'recovered': True}
        ok_resp.raise_for_status = MagicMock()

        ex._client.request = MagicMock(side_effect=[rate_resp, ok_resp])
        result = ex._api_get('/test')
        assert result == {'recovered': True}

    @patch('time.sleep')
    def test_all_retries_exhausted_returns_none(self, mock_sleep):
        ex = _make_executor()
        fail_resp = MagicMock()
        fail_resp.status_code = 500
        ex._client.request = MagicMock(return_value=fail_resp)

        result = ex._api_get('/test')
        assert result is None
        assert ex._client.request.call_count == ex.MAX_RETRIES


# ---------------------------------------------------------------------------
# Log fetching
# ---------------------------------------------------------------------------

class TestLogFetching:
    def test_fetch_logs_with_list_response(self):
        ex = _make_executor()
        ex._job_id = 'job-123'
        mock_entries = [
            {'id': 'log1', 'message': 'Starting pipeline...'},
            {'id': 'log2', 'message': 'Collecting data...'},
        ]
        ex._api_get = MagicMock(return_value=mock_entries)

        new_cursor = ex._fetch_and_print_logs(None)
        assert new_cursor == 'log2'

    def test_fetch_logs_with_dict_response(self):
        ex = _make_executor()
        ex._job_id = 'job-123'
        mock_resp = {
            'logs': [
                {'id': 'log_a', 'message': 'Hello'},
            ]
        }
        ex._api_get = MagicMock(return_value=mock_resp)

        new_cursor = ex._fetch_and_print_logs(None)
        assert new_cursor == 'log_a'

    def test_fetch_logs_none_response(self):
        ex = _make_executor()
        ex._job_id = 'job-123'
        ex._api_get = MagicMock(return_value=None)

        cursor = ex._fetch_and_print_logs('prev_cursor')
        assert cursor == 'prev_cursor'  # unchanged

    def test_fetch_logs_passes_cursor(self):
        ex = _make_executor()
        ex._job_id = 'job-123'
        ex._api_get = MagicMock(return_value=[])

        ex._fetch_and_print_logs('cursor_abc')
        ex._api_get.assert_called_once()
        call_args = ex._api_get.call_args
        assert call_args[1].get('params', {}).get('cursor') == 'cursor_abc'


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

class TestSignalHandling:
    def test_sigint_cancels_job(self):
        ex = _make_executor()
        ex._job_id = 'job-to-cancel'
        ex._api_post = MagicMock(return_value={'status': 'canceled'})

        with pytest.raises(SystemExit) as exc_info:
            ex._handle_sigint()

        assert exc_info.value.code == 130
        ex._api_post.assert_called_once()
        call_path = ex._api_post.call_args[0][0]
        assert 'cancel' in call_path

    def test_sigint_without_job_id(self):
        ex = _make_executor()
        ex._job_id = None  # no job started

        with pytest.raises(SystemExit) as exc_info:
            ex._handle_sigint()

        assert exc_info.value.code == 130
