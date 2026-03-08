"""
Progress Tracker — Real-time CLI progress display for FinSight pipeline runs.

Prints periodic one-line status updates to *stderr* so they don't pollute
stdout-piped output.  Also provides a rich end-of-run summary table.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


# Stage weights for overall percentage — reflects typical wall-clock share.
STAGE_WEIGHTS: Dict[str, float] = {
    'collect': 0.30,
    'analyze': 0.50,
    'report': 0.20,
}


def _fmt_duration(seconds: float) -> str:
    """Format *seconds* as ``Xm YYs`` or ``Xs``."""
    if seconds < 0:
        return '?'
    m, s = divmod(int(seconds), 60)
    if m:
        return f'{m}m{s:02d}s'
    return f'{s}s'


def _fmt_size(path: str) -> str:
    """Return human-readable file size for *path*, or ``-``."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return '-'
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024:
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'


class ProgressTracker:
    """Real-time CLI progress display.

    Usage::

        tracker = ProgressTracker(run_id='abc', stages=['collect','analyze','report'],
                                  total_tasks={'collect': 8, 'analyze': 5, 'report': 1})
        tracker.start_stage('collect')
        tracker.complete_task('collect', 'agent_abc')
        ...
        tracker.finish_stage('collect')
        tracker.print_summary(artifacts=[...], manifest_path='...')
    """

    def __init__(
        self,
        run_id: str,
        stages: List[str],
        total_tasks: Dict[str, int],
        estimated_sec: Optional[float] = None,
        executor: str = 'local',
        target_name: str = '',
    ):
        self.run_id = run_id
        self.stages = stages
        self.total_tasks = total_tasks
        self.estimated_sec = estimated_sec
        self.executor = executor
        self.target_name = target_name

        self._start_time = time.monotonic()
        self._current_stage: Optional[str] = None
        self._completed: Dict[str, int] = {s: 0 for s in stages}
        self._running: Dict[str, int] = {s: 0 for s in stages}
        self._failed: Dict[str, int] = {s: 0 for s in stages}
        self._stage_started: Dict[str, float] = {}
        self._stage_finished: Dict[str, float] = {}
        self._stage_status: Dict[str, str] = {s: 'pending' for s in stages}
        self._stage_detail: Dict[str, str] = {s: '' for s in stages}
        self._stage_detail_progress: Dict[str, tuple[int, int] | None] = {s: None for s in stages}
        self._last_detail_emit_at: float = 0.0
        self._last_detail_signature: str = ""
        self._periodic_task: Optional[asyncio.Task] = None
        self._master_cycles: int = 0
        self._master_pending_queue: int = 0
        self._master_last_confidence: float = 0.0
        self._master_mutations: Dict[str, int] = {}

        # Print initial banner
        est_str = f'ETA: ~{_fmt_duration(estimated_sec)}' if estimated_sec else 'ETA: unknown'
        self._print(
            f'FinSight — run_id: {run_id} | executor: {executor} | {est_str}'
        )

    # ----- stage / task lifecycle -----

    def start_stage(self, stage: str) -> None:
        self._current_stage = stage
        self._stage_started[stage] = time.monotonic()
        self._stage_status[stage] = 'running'
        self.print_status()

    def task_started(self, stage: str, agent_id: str = '') -> None:
        self._running[stage] = self._running.get(stage, 0) + 1

    def complete_task(self, stage: str, agent_id: str = '') -> None:
        self._completed[stage] = self._completed.get(stage, 0) + 1
        self._running[stage] = max(0, self._running.get(stage, 0) - 1)
        self.print_status()

    def fail_task(self, stage: str, agent_id: str = '', error: str = '') -> None:
        self._failed[stage] = self._failed.get(stage, 0) + 1
        self._running[stage] = max(0, self._running.get(stage, 0) - 1)
        self.print_status()

    def finish_stage(self, stage: str) -> None:
        self._stage_finished[stage] = time.monotonic()
        self._stage_status[stage] = 'done'
        self._running[stage] = 0
        self._stage_detail[stage] = ''
        self._stage_detail_progress[stage] = None
        self.print_status()

    def adjust_total_tasks(self, stage: str, delta: int) -> None:
        self.total_tasks[stage] = max(0, int(self.total_tasks.get(stage, 0) + int(delta)))

    def set_stage_detail(
        self,
        stage: str,
        detail: str,
        current: Optional[int] = None,
        total: Optional[int] = None,
        emit: bool = False,
    ) -> None:
        detail = str(detail or "").strip()
        progress = None
        if current is not None and total is not None:
            try:
                c = max(0, int(current))
                t = max(0, int(total))
                progress = (c, t)
            except Exception:
                progress = None
        changed = (
            self._stage_detail.get(stage, "") != detail
            or self._stage_detail_progress.get(stage) != progress
        )
        self._stage_detail[stage] = detail
        self._stage_detail_progress[stage] = progress
        if emit and changed and self._current_stage == stage:
            signature = f"{stage}|{detail}|{progress}"
            now = time.monotonic()
            if signature != self._last_detail_signature and now - self._last_detail_emit_at >= 5.0:
                self._last_detail_emit_at = now
                self._last_detail_signature = signature
                self.print_status()

    def clear_stage_detail(self, stage: str, emit: bool = False) -> None:
        had_detail = bool(self._stage_detail.get(stage, "")) or self._stage_detail_progress.get(stage) is not None
        self._stage_detail[stage] = ''
        self._stage_detail_progress[stage] = None
        if emit and had_detail and self._current_stage == stage:
            self.print_status()

    def update_master_metrics(
        self,
        *,
        master_cycles: Optional[int] = None,
        pending_queue_size: Optional[int] = None,
        last_decision_confidence: Optional[float] = None,
        mutations_applied: Optional[Dict[str, int]] = None,
    ) -> None:
        if master_cycles is not None:
            self._master_cycles = int(master_cycles)
        if pending_queue_size is not None:
            self._master_pending_queue = int(pending_queue_size)
        if last_decision_confidence is not None:
            self._master_last_confidence = float(last_decision_confidence)
        if mutations_applied is not None:
            self._master_mutations = dict(mutations_applied)

    # ----- percentage / ETA -----

    def _overall_pct(self) -> float:
        """Weighted percentage across all stages."""
        total_weight = sum(STAGE_WEIGHTS.get(s, 0.0) for s in self.stages)
        if total_weight == 0:
            return 0.0
        done_weight = 0.0
        for s in self.stages:
            w = STAGE_WEIGHTS.get(s, 0.0)
            total = self.total_tasks.get(s, 1) or 1
            completed = self._completed.get(s, 0)
            done_weight += w * min(completed / total, 1.0)
        return round(done_weight / total_weight * 100, 0)

    def _eta_sec(self) -> Optional[float]:
        """Estimate remaining seconds from elapsed time and completion %."""
        pct = self._overall_pct()
        elapsed = time.monotonic() - self._start_time
        if pct <= 0 or elapsed < 2:
            return self.estimated_sec  # fall back to history estimate
        raw_eta = elapsed / (pct / 100.0) * (1 - pct / 100.0)
        # Blend with history estimate if available
        if self.estimated_sec:
            remaining_from_hist = max(0, self.estimated_sec - elapsed)
            return 0.6 * raw_eta + 0.4 * remaining_from_hist
        return raw_eta

    # ----- output -----

    def print_status(self) -> None:
        """Print a single status line to stderr."""
        stage = self._current_stage or '???'
        done = self._completed.get(stage, 0)
        total = self.total_tasks.get(stage, 0)
        running = self._running.get(stage, 0)
        pct = self._overall_pct()
        elapsed = time.monotonic() - self._start_time
        eta = self._eta_sec()

        stage_display = stage.ljust(10)
        eta_str = f'ETA ~{_fmt_duration(eta)}' if eta is not None else 'ETA ?'
        status = self._stage_status.get(stage, '')
        if status == 'done':
            eta_str = '✓ done'

        line = (
            f'[{datetime.now().strftime("%H:%M:%S")}] '
            f'▶ {stage_display} | {done:>2}/{total} tasks | '
            f'{running} running | {pct:3.0f}% | '
            f'elapsed {_fmt_duration(elapsed)} | {eta_str} | '
            f'run {self.run_id} | '
            f'master cycles={self._master_cycles} pending={self._master_pending_queue}'
        )
        self._print(line)
        detail = str(self._stage_detail.get(stage, "") or "").strip()
        progress = self._stage_detail_progress.get(stage)
        if detail:
            detail_suffix = ""
            progress_bar = ""
            if progress is not None:
                current, total_count = progress
                if total_count > 0:
                    detail_pct = int(round((current / total_count) * 100))
                    ratio = max(0.0, min(1.0, current / total_count))
                    bar_width = 20
                    filled = int(round(ratio * bar_width))
                    progress_bar = "[" + ("#" * filled) + ("-" * (bar_width - filled)) + "] "
                    detail_suffix = f" | {current}/{total_count} ({detail_pct}%)"
                else:
                    detail_suffix = f" | {current}/{total_count}"
            self._print(
                f'[{datetime.now().strftime("%H:%M:%S")}] '
                f'↳ {stage_display} | {progress_bar}{detail}{detail_suffix}'
            )

    def print_summary(
        self,
        artifacts: Optional[List[Dict[str, Any]]] = None,
        manifest_path: str = '',
        pdf_status: str = '',
        success: bool = True,
    ) -> None:
        """Print end-of-run summary table."""
        elapsed = time.monotonic() - self._start_time
        est_str = ''
        if self.estimated_sec:
            accuracy = round(
                (1 - abs(self.estimated_sec - elapsed) / max(elapsed, 1)) * 100
            )
            est_str = f' (estimated: {_fmt_duration(self.estimated_sec)}, accuracy: {accuracy}%)'

        status_icon = '✓ SUCCESS' if success else '✗ FAILED'
        lines = [
            '',
            '═' * 55,
            f'  FinSight Run Summary — {self.run_id}',
            '═' * 55,
            f'  Target:    {self.target_name}',
            f'  Executor:  {self.executor}',
            f'  Status:    {status_icon}',
            f'  Duration:  {_fmt_duration(elapsed)}{est_str}',
            '─' * 55,
            f'  {"Stage":<15} {"Duration":<11} {"Tasks":<8} {"Status"}',
        ]
        for s in self.stages:
            dur = self._stage_finished.get(s, 0) - self._stage_started.get(s, 0)
            dur_str = _fmt_duration(dur) if s in self._stage_started else '-'
            done = self._completed.get(s, 0)
            total = self.total_tasks.get(s, 0)
            failed = self._failed.get(s, 0)
            st = self._stage_status.get(s, 'pending')
            icon = '✓' if st == 'done' else ('✗' if failed else '…')
            fail_str = f' ({failed} failed)' if failed else ''
            lines.append(f'  {s:<15} {dur_str:<11} {done}/{total}{fail_str:<8} {icon}')

        lines.append('─' * 55)
        if artifacts:
            lines.append('  Artifacts:')
            for a in artifacts:
                exists = '✓' if a.get('exists') else '✗'
                path = a.get('path', '')
                size = _fmt_size(path) if a.get('exists') else 'missing'
                name = os.path.basename(path)
                lines.append(f'    {exists} {name:<40} ({size})')
        if pdf_status:
            lines.append(f'  PDF: {pdf_status}')
        lines.append('─' * 55)
        if manifest_path:
            lines.append(f'  Manifest: {manifest_path}')
        lines.append('═' * 55)
        lines.append('')

        for line in lines:
            self._print(line)

    # ----- periodic background updates -----

    async def periodic_status(self, interval: float = 30.0) -> None:
        """Print status every *interval* seconds.  Run as ``asyncio.create_task``."""
        try:
            while True:
                await asyncio.sleep(interval)
                self.print_status()
        except asyncio.CancelledError:
            pass

    def start_periodic(self, interval: float = 30.0) -> None:
        """Launch the periodic printer as a background asyncio task."""
        self._periodic_task = asyncio.ensure_future(self.periodic_status(interval))

    def stop_periodic(self) -> None:
        """Cancel the background periodic printer."""
        if self._periodic_task and not self._periodic_task.done():
            self._periodic_task.cancel()

    # ----- helpers -----

    @staticmethod
    def _print(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)
