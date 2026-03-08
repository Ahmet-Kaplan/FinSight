"""
Run History — Persist and query prior pipeline run durations for ETA estimation.

History is stored at ``~/.finsight/run_history.json`` (max 100 entries, FIFO).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

MAX_ENTRIES = 100
_DEFAULT_PATH = os.path.join(os.path.expanduser('~'), '.finsight', 'run_history.json')
_DEFAULT_LIVE_PATH = os.path.join(os.path.expanduser('~'), '.finsight', 'run_history_live.json')


class RunHistory:
    """Persist run metadata and estimate future run durations."""

    def __init__(self, path: str = _DEFAULT_PATH, live_path: str = _DEFAULT_LIVE_PATH):
        self.path = path
        self.live_path = live_path
        self._entries: List[Dict[str, Any]] = []
        self._live_entries: Dict[str, Dict[str, Any]] = {}
        self._load()
        self._load_live()

    # ----- estimation -----

    def estimate(self, target_type: str, task_count: int) -> Optional[float]:
        """Return estimated total seconds based on similar previous runs.

        Looks for runs with the same *target_type*, averages their durations,
        and scales linearly by ``task_count / avg_task_count``.

        Returns ``None`` when there is no history to base an estimate on.
        """
        similar = [
            e for e in self._entries
            if e.get('target_type') == target_type and e.get('actual_sec')
        ]
        if not similar:
            # Fall back to any run
            similar = [e for e in self._entries if e.get('actual_sec')]
        if not similar:
            return self._estimate_from_live(target_type=target_type, task_count=task_count)

        # Use the most recent 5 entries
        recent = similar[-5:]
        avg_sec = sum(e['actual_sec'] for e in recent) / len(recent)
        avg_tasks = sum(e.get('task_count', task_count) for e in recent) / len(recent)
        if avg_tasks <= 0:
            return avg_sec
        scale = task_count / avg_tasks
        return round(avg_sec * scale, 1)

    def _estimate_from_live(self, target_type: str, task_count: int) -> Optional[float]:
        candidates = []
        for item in self._live_entries.values():
            if item.get('target_type') != target_type:
                continue
            completed = int(item.get('completed_tasks', 0) or 0)
            elapsed = float(item.get('elapsed_sec', 0.0) or 0.0)
            total = int(item.get('task_count', 0) or 0)
            if completed < 3 or elapsed <= 0 or total <= 0:
                continue
            estimated = elapsed * (task_count / max(completed, 1))
            candidates.append(estimated)
        if not candidates:
            return None
        return round(sum(candidates) / len(candidates), 1)

    # ----- recording -----

    def record(
        self,
        run_id: str,
        target_type: str,
        task_count: int,
        estimated_sec: Optional[float],
        actual_sec: float,
        stages: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append a completed run entry and persist to disk."""
        entry: Dict[str, Any] = {
            'run_id': run_id,
            'target_type': target_type,
            'task_count': task_count,
            'estimated_sec': estimated_sec,
            'actual_sec': round(actual_sec, 2),
            'timestamp': datetime.now().isoformat(),
        }
        if stages:
            entry['stages'] = stages
        if estimated_sec and actual_sec > 0:
            entry['accuracy'] = round(
                (1 - abs(estimated_sec - actual_sec) / actual_sec) * 100, 1
            )
        self._entries.append(entry)
        # FIFO cap
        if len(self._entries) > MAX_ENTRIES:
            self._entries = self._entries[-MAX_ENTRIES:]
        self._save()

    def accuracy(self, run_id: str) -> Optional[float]:
        """Return estimate accuracy (0-100 %) for a past run, or None."""
        for e in reversed(self._entries):
            if e.get('run_id') == run_id:
                return e.get('accuracy')
        return None

    # ----- live progress -----

    def start_live_run(self, run_id: str, target_type: str, task_count: int) -> None:
        self._live_entries[run_id] = {
            'run_id': run_id,
            'target_type': target_type,
            'task_count': task_count,
            'completed_tasks': 0,
            'elapsed_sec': 0.0,
            'updated_at': datetime.now().isoformat(),
        }
        self._save_live()

    def update_live_run(self, run_id: str, completed_tasks: int, elapsed_sec: float) -> None:
        item = self._live_entries.get(run_id)
        if not item:
            return
        item['completed_tasks'] = int(completed_tasks)
        item['elapsed_sec'] = float(round(elapsed_sec, 2))
        item['updated_at'] = datetime.now().isoformat()
        self._save_live()

    def finish_live_run(self, run_id: str) -> None:
        if run_id in self._live_entries:
            self._live_entries.pop(run_id, None)
            self._save_live()

    # ----- persistence -----

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._entries = data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError):
                self._entries = []
        else:
            self._entries = []

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self._entries, f, indent=2, ensure_ascii=False)
        except OSError:
            pass

    def _load_live(self) -> None:
        if os.path.exists(self.live_path):
            try:
                with open(self.live_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._live_entries = data
                else:
                    self._live_entries = {}
            except (json.JSONDecodeError, OSError):
                self._live_entries = {}
        else:
            self._live_entries = {}

    def _save_live(self) -> None:
        os.makedirs(os.path.dirname(self.live_path), exist_ok=True)
        try:
            with open(self.live_path, 'w', encoding='utf-8') as f:
                json.dump(self._live_entries, f, indent=2, ensure_ascii=False)
        except OSError:
            pass
