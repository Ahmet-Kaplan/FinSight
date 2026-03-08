"""Tests for src.utils.run_manifest — manifest upgrade with run_id, timing, executor."""

import json
import os
import time
from datetime import datetime

import pytest

from src.utils.run_manifest import RunManifest


@pytest.fixture
def output_dir(tmp_path):
    return str(tmp_path / 'outputs')


# ---------------------------------------------------------------------------
# Initialization & new fields
# ---------------------------------------------------------------------------

class TestManifestInit:
    def test_run_id_present(self, output_dir):
        m = RunManifest(output_dir)
        assert m.run_id
        assert len(m.run_id) == 8  # UUID4 hex[:8]

    def test_run_id_is_unique(self, output_dir):
        m1 = RunManifest(output_dir)
        m2 = RunManifest(output_dir)
        assert m1.run_id != m2.run_id

    def test_executor_default(self, output_dir):
        m = RunManifest(output_dir)
        assert m.executor == 'local'

    def test_executor_custom(self, output_dir):
        m = RunManifest(output_dir, executor='render')
        assert m.executor == 'render'

    def test_config_snapshot_stored(self, output_dir):
        snap = {'target_name': 'TestCo', 'language': 'en', 'task_count': 5}
        m = RunManifest(output_dir, config_snapshot=snap)
        assert m.config_snapshot == snap

    def test_config_snapshot_default_empty(self, output_dir):
        m = RunManifest(output_dir)
        assert m.config_snapshot == {}

    def test_estimated_total_sec_settable(self, output_dir):
        m = RunManifest(output_dir)
        m.estimated_total_sec = 300.0
        assert m.estimated_total_sec == 300.0


# ---------------------------------------------------------------------------
# Stage lifecycle
# ---------------------------------------------------------------------------

class TestStageLifecycle:
    def test_start_stage(self, output_dir):
        m = RunManifest(output_dir)
        m.start_stage('collect')
        assert m.stages['collect']['status'] == 'running'
        assert m.stages['collect']['started'] is not None

    def test_complete_stage(self, output_dir):
        m = RunManifest(output_dir)
        m.start_stage('collect')
        m.complete_stage('collect')
        assert m.stages['collect']['status'] == 'completed'
        assert m.stages['collect']['finished'] is not None

    def test_fail_stage(self, output_dir):
        m = RunManifest(output_dir)
        m.start_stage('analyze')
        m.fail_stage('analyze', 'timeout error')
        assert m.stages['analyze']['status'] == 'failed'
        assert m.stages['analyze']['error'] == 'timeout error'
        assert m.stages['analyze']['finished'] is not None

    def test_duration_sec_computed(self, output_dir):
        m = RunManifest(output_dir)
        m.start_stage('collect')
        time.sleep(0.05)  # small delay to get measurable duration
        m.complete_stage('collect')
        dur = m.stages['collect']['duration_sec']
        assert dur is not None
        assert dur >= 0.0

    def test_unknown_stage_ignored(self, output_dir):
        m = RunManifest(output_dir)
        m.start_stage('nonexistent')  # should not raise
        m.complete_stage('nonexistent')
        assert 'nonexistent' not in m.stages


# ---------------------------------------------------------------------------
# Artifacts & success
# ---------------------------------------------------------------------------

class TestArtifacts:
    def test_add_artifact(self, output_dir):
        m = RunManifest(output_dir)
        m.add_artifact('/tmp/report.md', 'report_md')
        assert len(m.artifacts) == 1
        assert m.artifacts[0]['type'] == 'report_md'

    def test_is_success_when_no_failures(self, output_dir, tmp_path):
        md = tmp_path / 'report.md'
        md.write_text('# Report')
        m = RunManifest(output_dir)
        m.add_artifact(str(md), 'report_md')
        assert m.is_success() is True

    def test_is_failure_on_failed_stage(self, output_dir, tmp_path):
        md = tmp_path / 'report.md'
        md.write_text('# Report')
        m = RunManifest(output_dir)
        m.add_artifact(str(md), 'report_md')
        m.start_stage('collect')
        m.fail_stage('collect', 'error')
        assert m.is_success() is False

    def test_is_failure_on_missing_artifact(self, output_dir):
        m = RunManifest(output_dir)
        m.add_artifact('/nonexistent/report.md', 'report_md')
        assert m.is_success() is False


# ---------------------------------------------------------------------------
# save() output
# ---------------------------------------------------------------------------

class TestSave:
    def test_save_creates_json(self, output_dir):
        m = RunManifest(output_dir, target_name='TestCo')
        data = m.save()
        manifest_path = os.path.join(output_dir, 'run_manifest.json')
        assert os.path.exists(manifest_path)

    def test_save_contains_run_id(self, output_dir):
        m = RunManifest(output_dir)
        data = m.save()
        assert 'run_id' in data
        assert data['run_id'] == m.run_id

    def test_save_contains_executor(self, output_dir):
        m = RunManifest(output_dir, executor='render')
        data = m.save()
        assert data['executor'] == 'render'

    def test_save_contains_actual_total_sec(self, output_dir):
        m = RunManifest(output_dir)
        time.sleep(0.05)
        data = m.save()
        assert 'actual_total_sec' in data
        assert data['actual_total_sec'] >= 0

    def test_save_contains_estimated_total_sec(self, output_dir):
        m = RunManifest(output_dir)
        m.estimated_total_sec = 200.0
        data = m.save()
        assert data['estimated_total_sec'] == 200.0

    def test_save_contains_config_snapshot(self, output_dir):
        snap = {'language': 'en'}
        m = RunManifest(output_dir, config_snapshot=snap)
        data = m.save()
        assert data['config_snapshot'] == snap

    def test_save_contains_stage_durations(self, output_dir):
        m = RunManifest(output_dir)
        m.start_stage('collect')
        m.complete_stage('collect')
        data = m.save()
        assert data['stages']['collect']['duration_sec'] is not None

    def test_save_roundtrips_from_disk(self, output_dir):
        m = RunManifest(output_dir, target_name='RoundTrip Corp')
        m.start_stage('collect')
        m.complete_stage('collect')
        m.save()
        path = os.path.join(output_dir, 'run_manifest.json')
        with open(path, 'r') as f:
            loaded = json.load(f)
        assert loaded['run_id'] == m.run_id
        assert loaded['target'] == 'RoundTrip Corp'
        assert loaded['stages']['collect']['status'] == 'completed'
