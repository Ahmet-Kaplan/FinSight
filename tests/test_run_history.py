"""Tests for src.utils.run_history — RunHistory persistence and estimation."""

import json
import os
import pytest

from src.utils.run_history import RunHistory, MAX_ENTRIES


@pytest.fixture
def history_path(tmp_path):
    """Return a temp path for run_history.json."""
    return str(tmp_path / 'run_history.json')


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_creates_file_on_record(self, history_path):
        h = RunHistory(path=history_path)
        h.record('run1', 'company', 5, 100.0, 110.0)
        assert os.path.exists(history_path)

    def test_data_round_trips(self, history_path):
        h = RunHistory(path=history_path)
        h.record('run1', 'company', 5, 100.0, 110.0)

        h2 = RunHistory(path=history_path)
        assert len(h2._entries) == 1
        assert h2._entries[0]['run_id'] == 'run1'

    def test_fifo_cap(self, history_path):
        h = RunHistory(path=history_path)
        for i in range(MAX_ENTRIES + 20):
            h.record(f'run_{i}', 'company', 3, 50.0, 55.0)
        assert len(h._entries) == MAX_ENTRIES
        # Oldest entries should be dropped
        assert h._entries[0]['run_id'] == 'run_20'

    def test_empty_file_no_crash(self, history_path):
        with open(history_path, 'w') as f:
            f.write('')
        h = RunHistory(path=history_path)
        assert h._entries == []

    def test_corrupt_json_no_crash(self, history_path):
        with open(history_path, 'w') as f:
            f.write('{invalid json')
        h = RunHistory(path=history_path)
        assert h._entries == []

    def test_nonexistent_file_creates_empty(self, history_path):
        h = RunHistory(path=history_path)
        assert h._entries == []


# ---------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------

class TestEstimation:
    def test_no_history_returns_none(self, history_path):
        h = RunHistory(path=history_path)
        assert h.estimate('company', 5) is None

    def test_estimate_from_same_type(self, history_path):
        h = RunHistory(path=history_path)
        h.record('r1', 'company', 5, None, 100.0)
        h.record('r2', 'company', 5, None, 120.0)
        h.record('r3', 'company', 5, None, 110.0)
        est = h.estimate('company', 5)
        assert est is not None
        # Average of 100, 120, 110 = 110, scaled by 5/5 = 110
        assert abs(est - 110.0) < 0.5

    def test_estimate_scales_by_task_count(self, history_path):
        h = RunHistory(path=history_path)
        h.record('r1', 'company', 5, None, 100.0)
        h.record('r2', 'company', 5, None, 100.0)
        # Estimate for 10 tasks (2x) should be ~200
        est = h.estimate('company', 10)
        assert est is not None
        assert abs(est - 200.0) < 0.5

    def test_estimate_falls_back_to_any_type(self, history_path):
        h = RunHistory(path=history_path)
        h.record('r1', 'industry', 3, None, 90.0)
        # No 'company' history → falls back to 'industry'
        est = h.estimate('company', 3)
        assert est is not None
        assert est == 90.0

    def test_estimate_uses_recent_5(self, history_path):
        h = RunHistory(path=history_path)
        for i in range(10):
            h.record(f'r{i}', 'company', 5, None, 100.0 + i * 10)
        est = h.estimate('company', 5)
        # Should use last 5: r5=150, r6=160, r7=170, r8=180, r9=190
        # Avg = 170, scale = 5/5 = 1, so ~170
        assert est is not None
        assert abs(est - 170.0) < 0.5


# ---------------------------------------------------------------------------
# Accuracy recording
# ---------------------------------------------------------------------------

class TestAccuracy:
    def test_accuracy_recorded(self, history_path):
        h = RunHistory(path=history_path)
        h.record('r1', 'company', 5, 100.0, 110.0)
        acc = h.accuracy('r1')
        assert acc is not None
        # accuracy = (1 - |100-110|/110) * 100 = (1 - 10/110) * 100 ≈ 90.9
        assert abs(acc - 90.9) < 0.5

    def test_accuracy_none_when_no_estimate(self, history_path):
        h = RunHistory(path=history_path)
        h.record('r1', 'company', 5, None, 110.0)
        acc = h.accuracy('r1')
        assert acc is None

    def test_accuracy_unknown_run_returns_none(self, history_path):
        h = RunHistory(path=history_path)
        assert h.accuracy('nonexistent') is None

    def test_record_fields(self, history_path):
        h = RunHistory(path=history_path)
        h.record('r1', 'company', 5, 100.0, 110.0, stages={'collect': {'duration_sec': 30}})
        entry = h._entries[0]
        assert entry['run_id'] == 'r1'
        assert entry['target_type'] == 'company'
        assert entry['task_count'] == 5
        assert entry['estimated_sec'] == 100.0
        assert entry['actual_sec'] == 110.0
        assert 'timestamp' in entry
        assert entry['stages'] == {'collect': {'duration_sec': 30}}
