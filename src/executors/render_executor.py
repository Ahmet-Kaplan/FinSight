"""
Render Executor — Dispatch and monitor FinSight runs on Render Background Workers.

Requires:
    RENDER_API_KEY  — Render API key (starts with ``rnd_``)
    RENDER_SERVICE_ID — Render Background Worker service ID (``srv-XXXX``)
"""

from __future__ import annotations

import asyncio
import base64
import os
import signal
import sys
import time
from typing import Optional

try:
    import httpx
except ImportError:  # pragma: no cover – httpx is optional at import time
    httpx = None  # type: ignore[assignment]


class RenderExecutor:
    """Trigger and monitor FinSight pipeline runs on Render."""

    API_BASE = 'https://api.render.com/v1'
    POLL_INTERVAL = 5.0       # seconds between job-status polls
    LOG_INTERVAL = 2.0        # seconds between log fetches
    MAX_RETRIES = 3
    BACKOFF_BASE = 1.0        # exponential backoff base (seconds)
    BACKOFF_MAX = 30.0

    def __init__(self, api_key: str, service_id: str):
        if httpx is None:
            raise RuntimeError(
                'httpx is required for Render executor. Install with: pip install httpx'
            )
        if not api_key:
            raise ValueError(
                'RENDER_API_KEY is required. '
                'Get one from Render Dashboard → Account Settings → API Keys.'
            )
        if not service_id:
            raise ValueError(
                'RENDER_SERVICE_ID is required. '
                'Find it in Render Dashboard → your Background Worker → Settings.'
            )
        self.api_key = api_key
        self.service_id = service_id
        self._client = httpx.Client(
            base_url=self.API_BASE,
            headers={'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'},
            timeout=30.0,
        )
        self._job_id: Optional[str] = None

    # ----- public API -----

    async def run(
        self,
        config_yaml_content: str,
        cli_args: Optional[list[str]] = None,
    ) -> int:
        """Create a job, stream logs, and return an exit code (0 = success).

        Parameters
        ----------
        config_yaml_content : str
            Raw YAML config to be made available to the remote worker.
        cli_args : list[str], optional
            Extra CLI arguments forwarded to ``run_report.py``.

        Returns
        -------
        int
            0 on success, 1 on failure.
        """
        # Register Ctrl+C handler
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGINT, self._handle_sigint)

        start_cmd = self._build_start_command(config_yaml_content, cli_args or [])
        self._log(f'Creating job on service {self.service_id} …')
        job = self._api_post(
            f'/services/{self.service_id}/jobs',
            json={'startCommand': start_cmd},
        )
        if not job:
            self._log('ERROR: failed to create job.')
            return 1

        self._job_id = job.get('id')
        self._log(f'Job created: {self._job_id}')

        # Poll until terminal state
        last_log_cursor: Optional[str] = None
        while True:
            await asyncio.sleep(self.POLL_INTERVAL)
            status_resp = self._api_get(
                f'/services/{self.service_id}/jobs/{self._job_id}'
            )
            if not status_resp:
                continue

            status = status_resp.get('status', 'unknown')

            # Stream logs
            last_log_cursor = self._fetch_and_print_logs(last_log_cursor)

            if status in ('succeeded', 'failed', 'canceled'):
                icon = '✓' if status == 'succeeded' else '✗'
                self._log(f'{icon} Job {self._job_id} finished with status: {status}')
                return 0 if status == 'succeeded' else 1

    # ----- internals -----

    def _build_start_command(
        self, config_yaml: str, cli_args: list[str]
    ) -> str:
        """Build the remote ``startCommand``.

        The config YAML is base64-encoded into an env var that the remote
        entrypoint decodes and writes to ``my_config.yaml``.
        """
        b64 = base64.b64encode(config_yaml.encode()).decode()
        args_str = ' '.join(cli_args) if cli_args else ''
        return (
            f'echo {b64} | base64 -d > my_config.yaml && '
            f'python run_report.py --executor local {args_str}'
        )

    def _fetch_and_print_logs(self, cursor: Optional[str]) -> Optional[str]:
        """Fetch new log lines and print them to stderr."""
        params: dict = {}
        if cursor:
            params['cursor'] = cursor
        resp = self._api_get(
            f'/services/{self.service_id}/jobs/{self._job_id}/logs',
            params=params,
        )
        if not resp:
            return cursor
        entries = resp if isinstance(resp, list) else resp.get('logs', [])
        new_cursor = cursor
        for entry in entries:
            msg = entry.get('message', '') if isinstance(entry, dict) else str(entry)
            print(msg, file=sys.stderr, flush=True)
            if isinstance(entry, dict) and 'id' in entry:
                new_cursor = entry['id']
        return new_cursor

    def _handle_sigint(self) -> None:
        """Attempt to cancel the remote job on Ctrl+C."""
        if self._job_id:
            self._log(f'\nCtrl+C — cancelling job {self._job_id} …')
            try:
                self._api_post(
                    f'/services/{self.service_id}/jobs/{self._job_id}/cancel'
                )
                self._log('Cancel request sent.')
            except Exception:
                self._log('Failed to cancel remote job.')
        raise SystemExit(130)

    # ----- HTTP helpers with retry -----

    def _api_get(self, path: str, params: Optional[dict] = None):
        return self._request('GET', path, params=params)

    def _api_post(self, path: str, json: Optional[dict] = None):
        return self._request('POST', path, json=json)

    def _request(self, method: str, path: str, **kwargs):
        """Make an HTTP request with retry on transient errors."""
        delay = self.BACKOFF_BASE
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = self._client.request(method, path, **kwargs)
                if resp.status_code in (401, 403):
                    self._log(
                        f'ERROR: Auth failed ({resp.status_code}). '
                        'Check RENDER_API_KEY and RENDER_SERVICE_ID.'
                    )
                    return None
                if resp.status_code == 429 or resp.status_code >= 500:
                    self._log(f'Retryable error {resp.status_code}, retrying in {delay:.0f}s …')
                    time.sleep(delay)
                    delay = min(delay * 2, self.BACKOFF_MAX)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as exc:
                self._log(f'HTTP error: {exc}')
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, self.BACKOFF_MAX)
        return None

    @staticmethod
    def _log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)
