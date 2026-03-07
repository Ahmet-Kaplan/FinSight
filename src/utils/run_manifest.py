"""
Run Manifest — Pipeline Completion Tracker

Tracks stage status (collect → analyse → report_assemble → render), records
produced artifacts, and writes a final ``run_manifest.json`` with
success/failure determination.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RunManifest:
    """Track pipeline stage status and required artifacts."""

    STAGES = ('collect', 'analyze', 'report_assemble', 'render')

    def __init__(self, output_dir: str, target_name: str = ''):
        self.output_dir = output_dir
        self.target_name = target_name
        self.stages: Dict[str, Dict[str, Any]] = {
            s: {'status': 'pending', 'started': None, 'finished': None, 'error': None}
            for s in self.STAGES
        }
        self.artifacts: List[Dict[str, Any]] = []
        self.warnings: List[str] = []

    # ----- stage lifecycle -----

    def start_stage(self, stage: str) -> None:
        if stage in self.stages:
            self.stages[stage]['status'] = 'running'
            self.stages[stage]['started'] = datetime.now().isoformat()

    def complete_stage(self, stage: str) -> None:
        if stage in self.stages:
            self.stages[stage]['status'] = 'completed'
            self.stages[stage]['finished'] = datetime.now().isoformat()

    def fail_stage(self, stage: str, error: str) -> None:
        if stage in self.stages:
            self.stages[stage]['status'] = 'failed'
            self.stages[stage]['error'] = error
            self.stages[stage]['finished'] = datetime.now().isoformat()

    # ----- artifact tracking -----

    def add_artifact(self, path: str, artifact_type: str) -> None:
        self.artifacts.append({
            'path': path,
            'type': artifact_type,
            'exists': os.path.exists(path),
        })

    def add_warning(self, warning: str) -> None:
        self.warnings.append(warning)

    # ----- completion checks -----

    def check_required_artifacts(self) -> List[str]:
        """Return a list of descriptions for missing required outputs."""
        missing: List[str] = []
        md_files = [a for a in self.artifacts if a['type'] == 'report_md']
        if not md_files or not any(a['exists'] for a in md_files):
            missing.append('No .md report file produced')
        return missing

    def is_success(self) -> bool:
        failed = [s for s, info in self.stages.items() if info['status'] == 'failed']
        missing = self.check_required_artifacts()
        return len(failed) == 0 and len(missing) == 0

    # ----- persistence -----

    def save(self) -> dict:
        """Write ``run_manifest.json`` into *output_dir* and return the dict."""
        os.makedirs(self.output_dir, exist_ok=True)
        manifest_path = os.path.join(self.output_dir, 'run_manifest.json')
        data = {
            'target': self.target_name,
            'timestamp': datetime.now().isoformat(),
            'success': self.is_success(),
            'stages': self.stages,
            'artifacts': self.artifacts,
            'warnings': self.warnings,
            'missing': self.check_required_artifacts(),
        }
        try:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            status = 'SUCCESS' if self.is_success() else 'INCOMPLETE'
            logger.info(f"Run manifest saved: {manifest_path} — {status}")
        except Exception as e:
            logger.warning(f"Failed to write run manifest: {e}")
        return data
