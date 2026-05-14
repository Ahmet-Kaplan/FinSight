"""Tests for src.utils.progress — ProgressTracker."""

import io
import sys
import time
from unittest.mock import patch

import pytest

from src.utils.progress import ProgressTracker, _fmt_duration, _fmt_size, STAGE_WEIGHTS


# ---------------------------------------------------------------------------
# _fmt_duration
# ---------------------------------------------------------------------------

class TestFmtDuration:
    def test_seconds_only(self):
        assert _fmt_duration(45) == '45s'

    def test_minutes_and_seconds(self):
        assert _fmt_duration(125) == '2m05s'

    def test_zero(self):
        assert _fmt_duration(0) == '0s'

    def test_negative(self):
        assert _fmt_duration(-1) == '?'

    def test_exactly_one_minute(self):
        assert _fmt_duration(60) == '1m00s'

    def test_large_value(self):
        assert _fmt_duration(3661) == '61m01s'


# ---------------------------------------------------------------------------
# _fmt_size
# ---------------------------------------------------------------------------

class TestFmtSize:
    def test_nonexistent_file(self):
        assert _fmt_size('/nonexistent/file.txt') == '-'

    def test_real_file(self, tmp_path):
        p = tmp_path / 'test.txt'
        p.write_text('hello')
        result = _fmt_size(str(p))
        assert 'B' in result


# ---------------------------------------------------------------------------
# ProgressTracker lifecycle
# ---------------------------------------------------------------------------

class TestProgressTrackerLifecycle:
    def _make_tracker(self, **kwargs):
        defaults = dict(
            run_id='abc12345',
            stages=['collect', 'analyze', 'report'],
            total_tasks={'collect': 4, 'analyze': 2, 'report': 1},
            estimated_sec=120.0,
            executor='local',
            target_name='Test Corp',
        )
        defaults.update(kwargs)
        return ProgressTracker(**defaults)

    def test_initial_state(self):
        tracker = self._make_tracker()
        assert tracker.run_id == 'abc12345'
        assert tracker._overall_pct() == 0.0

    def test_start_stage(self):
        tracker = self._make_tracker()
        tracker.start_stage('collect')
        assert tracker._current_stage == 'collect'
        assert tracker._stage_status['collect'] == 'running'
        assert 'collect' in tracker._stage_started

    def test_complete_task_increments_count(self):
        tracker = self._make_tracker()
        tracker.start_stage('collect')
        tracker.complete_task('collect', 'agent_1')
        assert tracker._completed['collect'] == 1

    def test_fail_task_increments_failures(self):
        tracker = self._make_tracker()
        tracker.start_stage('collect')
        tracker.task_started('collect', 'agent_1')
        tracker.fail_task('collect', 'agent_1', 'timeout')
        assert tracker._failed['collect'] == 1
        assert tracker._running['collect'] == 0

    def test_running_count_tracked(self):
        tracker = self._make_tracker()
        tracker.start_stage('collect')
        tracker.task_started('collect', 'agent_1')
        tracker.task_started('collect', 'agent_2')
        assert tracker._running['collect'] == 2
        tracker.complete_task('collect', 'agent_1')
        assert tracker._running['collect'] == 1

    def test_finish_stage(self):
        tracker = self._make_tracker()
        tracker.start_stage('collect')
        tracker.complete_task('collect', 'agent_1')
        tracker.finish_stage('collect')
        assert tracker._stage_status['collect'] == 'done'
        assert tracker._running['collect'] == 0


# ---------------------------------------------------------------------------
# Percentage calculation
# ---------------------------------------------------------------------------

class TestPercentage:
    def test_zero_when_nothing_done(self):
        tracker = ProgressTracker(
            run_id='x', stages=['collect', 'analyze', 'report'],
            total_tasks={'collect': 4, 'analyze': 2, 'report': 1},
        )
        assert tracker._overall_pct() == 0.0

    def test_collect_half_done(self):
        tracker = ProgressTracker(
            run_id='x', stages=['collect', 'analyze', 'report'],
            total_tasks={'collect': 4, 'analyze': 2, 'report': 1},
        )
        tracker._completed['collect'] = 2  # 2/4 = 50% of collect stage
        # 50% of collect weight (0.30) = 0.15 out of 1.0 = 15%
        pct = tracker._overall_pct()
        assert pct == 15.0

    def test_all_complete_is_100(self):
        tracker = ProgressTracker(
            run_id='x', stages=['collect', 'analyze', 'report'],
            total_tasks={'collect': 4, 'analyze': 2, 'report': 1},
        )
        tracker._completed['collect'] = 4
        tracker._completed['analyze'] = 2
        tracker._completed['report'] = 1
        assert tracker._overall_pct() == 100.0

    def test_cannot_exceed_100(self):
        tracker = ProgressTracker(
            run_id='x', stages=['collect', 'analyze', 'report'],
            total_tasks={'collect': 4, 'analyze': 2, 'report': 1},
        )
        tracker._completed['collect'] = 10  # more than total
        tracker._completed['analyze'] = 5
        tracker._completed['report'] = 3
        assert tracker._overall_pct() == 100.0


# ---------------------------------------------------------------------------
# ETA
# ---------------------------------------------------------------------------

class TestEta:
    def test_eta_falls_back_to_estimate_when_no_progress(self):
        tracker = ProgressTracker(
            run_id='x', stages=['collect', 'analyze', 'report'],
            total_tasks={'collect': 4, 'analyze': 2, 'report': 1},
            estimated_sec=300.0,
        )
        # No tasks completed, so ETA should fall back to estimated_sec
        eta = tracker._eta_sec()
        assert eta == 300.0

    def test_eta_none_when_no_estimate_and_no_progress(self):
        tracker = ProgressTracker(
            run_id='x', stages=['collect', 'analyze', 'report'],
            total_tasks={'collect': 4, 'analyze': 2, 'report': 1},
        )
        eta = tracker._eta_sec()
        assert eta is None


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

class TestOutput:
    def test_print_status_writes_to_stderr(self, capsys):
        tracker = ProgressTracker(
            run_id='test1', stages=['collect', 'analyze', 'report'],
            total_tasks={'collect': 4, 'analyze': 2, 'report': 1},
        )
        tracker.start_stage('collect')
        captured = capsys.readouterr()
        assert 'collect' in captured.err
        assert 'test1' in captured.err

    def test_print_summary_includes_stage_table(self, capsys):
        tracker = ProgressTracker(
            run_id='sum1', stages=['collect', 'analyze', 'report'],
            total_tasks={'collect': 2, 'analyze': 1, 'report': 1},
            target_name='TestCo',
        )
        tracker.print_summary(success=True)
        captured = capsys.readouterr()
        assert 'TestCo' in captured.err
        assert 'SUCCESS' in captured.err
        assert 'collect' in captured.err

    def test_print_summary_shows_failure(self, capsys):
        tracker = ProgressTracker(
            run_id='fail1', stages=['collect'],
            total_tasks={'collect': 1},
        )
        tracker.print_summary(success=False)
        captured = capsys.readouterr()
        assert 'FAILED' in captured.err

    def test_print_summary_shows_artifacts(self, capsys, tmp_path):
        p = tmp_path / 'report.docx'
        p.write_text('dummy')
        tracker = ProgressTracker(
            run_id='art1', stages=['report'],
            total_tasks={'report': 1},
        )
        artifacts = [{'path': str(p), 'exists': True}]
        tracker.print_summary(artifacts=artifacts, success=True)
        captured = capsys.readouterr()
        assert 'report.docx' in captured.err
