"""Recovery and state-management helpers for resumable FinSight runs.

This module centralizes:
- canonical task key generation
- run state JSON files under ``<working_dir>/state``
- doctor/repair diagnostics for task mapping + checkpoints
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import dill
import pickle


STATE_DIR_NAME = "state"
TASK_STATE_FILE = "task_state.json"
HEARTBEAT_FILE = "heartbeat.json"
RECOVERY_REPORT_FILE = "recovery_report.json"
MASTER_STATE_FILE = "master_state.json"
MASTER_DECISIONS_FILE = "master_decisions.jsonl"
ARTIFACT_INDEX_FILE = "artifact_index.json"
TASK_QUEUE_SNAPSHOT_FILE = "task_queue_snapshot.json"
MASTER_HEALTH_FILE = "master_health.json"
MASTER_ESCALATIONS_FILE = "master_escalations.jsonl"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def canonical_task_key(
    *,
    stage: str,
    profile: str,
    task_text: str,
    target_name: str,
    target_type: str,
) -> str:
    """Return stable task identity hash.

    Mutable orchestration prose (for example profile overlap notes) should not
    be part of ``task_text`` input.
    """
    payload = "|".join(
        [
            _normalize_text(stage),
            _normalize_text(profile),
            _normalize_text(task_text),
            _normalize_text(target_name),
            _normalize_text(target_type),
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()
    return digest


def legacy_task_key(agent_class_name: str, task_key: str) -> str:
    payload = f"{_normalize_text(agent_class_name)}|{_normalize_text(task_key)}"
    return hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()


def get_state_dir(working_dir: str) -> str:
    path = os.path.join(working_dir, STATE_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _state_path(working_dir: str, filename: str) -> str:
    return os.path.join(get_state_dir(working_dir), filename)


def load_json(path: str, default: Any):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_atomic(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def load_task_state(working_dir: str) -> Dict[str, Any]:
    return load_json(_state_path(working_dir, TASK_STATE_FILE), {})


def save_task_state(working_dir: str, data: Dict[str, Any]) -> None:
    save_json_atomic(_state_path(working_dir, TASK_STATE_FILE), data)


def load_heartbeat(working_dir: str) -> Dict[str, Any]:
    return load_json(_state_path(working_dir, HEARTBEAT_FILE), {})


def save_heartbeat(working_dir: str, data: Dict[str, Any]) -> None:
    save_json_atomic(_state_path(working_dir, HEARTBEAT_FILE), data)


def load_master_state(working_dir: str) -> Dict[str, Any]:
    return load_json(_state_path(working_dir, MASTER_STATE_FILE), {})


def save_master_state(working_dir: str, data: Dict[str, Any]) -> None:
    save_json_atomic(_state_path(working_dir, MASTER_STATE_FILE), data)


def load_artifact_index(working_dir: str) -> Dict[str, Any]:
    return load_json(_state_path(working_dir, ARTIFACT_INDEX_FILE), {})


def save_artifact_index(working_dir: str, data: Dict[str, Any]) -> None:
    save_json_atomic(_state_path(working_dir, ARTIFACT_INDEX_FILE), data)


def load_task_queue_snapshot(working_dir: str) -> Dict[str, Any]:
    return load_json(_state_path(working_dir, TASK_QUEUE_SNAPSHOT_FILE), {})


def save_task_queue_snapshot(working_dir: str, data: Dict[str, Any]) -> None:
    save_json_atomic(_state_path(working_dir, TASK_QUEUE_SNAPSHOT_FILE), data)


def load_master_health(working_dir: str) -> Dict[str, Any]:
    return load_json(_state_path(working_dir, MASTER_HEALTH_FILE), {})


def save_master_health(working_dir: str, data: Dict[str, Any]) -> None:
    save_json_atomic(_state_path(working_dir, MASTER_HEALTH_FILE), data)


def append_master_escalation(working_dir: str, row: Dict[str, Any]) -> None:
    append_jsonl(_state_path(working_dir, MASTER_ESCALATIONS_FILE), row)


def append_jsonl(path: str, row: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_heartbeat_entry(
    *,
    working_dir: str,
    agent_id: str,
    canonical_key: str,
    stage: str,
    status: str,
    current_round: int,
    checkpoint_name: str,
) -> None:
    hb = load_heartbeat(working_dir)
    hb[agent_id] = {
        "agent_id": agent_id,
        "canonical_task_key": canonical_key,
        "stage": stage,
        "status": status,
        "current_round": current_round,
        "checkpoint_name": checkpoint_name,
        "updated_at": utc_now_iso(),
    }
    save_heartbeat(working_dir, hb)


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def checkpoint_candidates(cache_dir: str, requested_checkpoint: str = "latest.pkl") -> List[str]:
    if not os.path.isdir(cache_dir):
        return []
    out: List[str] = []
    requested = os.path.join(cache_dir, requested_checkpoint)
    if os.path.exists(requested):
        out.append(requested)
    for filename in sorted(os.listdir(cache_dir)):
        if filename.endswith(".pkl"):
            path = os.path.join(cache_dir, filename)
            if path not in out:
                out.append(path)
    return out


def load_checkpoint_state(cache_dir: str, requested_checkpoint: str = "latest.pkl") -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    for path in checkpoint_candidates(cache_dir, requested_checkpoint=requested_checkpoint):
        try:
            with open(path, "rb") as f:
                return dill.load(f), os.path.basename(path)
        except Exception:
            try:
                with open(path, "rb") as f:
                    return pickle.load(f), os.path.basename(path)
            except Exception:
                continue
    return None, None


def is_checkpoint_finished(state: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(state, dict):
        return False
    if state.get("finished", False):
        return True
    return state.get("return_dict") is not None


def is_checkpoint_started(state: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(state, dict):
        return False
    if is_checkpoint_finished(state):
        return True
    try:
        return int(state.get("current_round", 0)) > 0
    except Exception:
        return False


def normalize_task_entry(
    entry: Dict[str, Any],
    *,
    target_name: str,
    target_type: str,
) -> Dict[str, Any]:
    """Ensure required canonical fields exist on a persisted task entry."""
    normalized = dict(entry)
    canonical = normalized.get("canonical_task_key")
    if not canonical:
        input_data = normalized.get("task_input", {}).get("input_data", {})
        stage = str(input_data.get("stage_name", normalized.get("agent_class_name", "")))
        profile = str(input_data.get("profile_name", ""))
        raw_task = input_data.get("raw_task_text") or normalized.get("task_key", "")
        if stage and profile and raw_task:
            canonical = canonical_task_key(
                stage=stage,
                profile=profile,
                task_text=raw_task,
                target_name=target_name,
                target_type=target_type,
            )
        else:
            canonical = legacy_task_key(normalized.get("agent_class_name", ""), normalized.get("task_key", ""))
    normalized["canonical_task_key"] = canonical
    if "created_at" not in normalized:
        normalized["created_at"] = utc_now_iso()
    return normalized


@dataclass
class DoctorSummary:
    duplicate_active_tasks: int
    missing_checkpoints: int
    stale_tasks: int
    orphaned_mappings: int
    orphaned_agent_dirs: int
    recreated_tasks: int
    recoverable_tasks: int
    run_id: str
    details: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "duplicate_active_tasks": self.duplicate_active_tasks,
            "missing_checkpoints": self.missing_checkpoints,
            "stale_tasks": self.stale_tasks,
            "orphaned_mappings": self.orphaned_mappings,
            "orphaned_agent_dirs": self.orphaned_agent_dirs,
            "recreated_tasks": self.recreated_tasks,
            "recoverable_tasks": self.recoverable_tasks,
            "run_id": self.run_id,
            "details": self.details,
            "generated_at": utc_now_iso(),
        }


def run_doctor(
    *,
    working_dir: str,
    task_mapping: List[Dict[str, Any]],
    task_attempts: Dict[str, List[Dict[str, Any]]],
    stale_seconds: int = 900,
    run_id: str = "unknown",
    expected_canonical_keys: Optional[Iterable[str]] = None,
) -> DoctorSummary:
    """Inspect mapping/checkpoints and return diagnostic summary."""
    now = datetime.now(timezone.utc)
    hb = load_heartbeat(working_dir)

    key_filter = None
    if expected_canonical_keys is not None:
        key_filter = {str(x) for x in expected_canonical_keys if str(x).strip()}

    latest_by_canonical: Dict[str, Dict[str, Any]] = {}
    duplicates = 0
    for entry in task_mapping:
        key = entry.get("canonical_task_key")
        if not key:
            continue
        if key_filter is not None and key not in key_filter:
            continue
        if key in latest_by_canonical:
            duplicates += 1
        latest_by_canonical[key] = entry

    missing_checkpoints = 0
    stale_tasks = 0
    orphaned_mappings = 0
    recoverable_tasks = 0
    stale_keys: List[str] = []
    stale_pairs: List[Dict[str, str]] = []
    missing_keys: List[str] = []
    recoverable_keys: List[str] = []

    for key, entry in latest_by_canonical.items():
        agent_id = entry.get("agent_id", "")
        cache_dir = os.path.join(working_dir, "agent_working", agent_id, ".cache")
        candidates = checkpoint_candidates(cache_dir, requested_checkpoint="latest.pkl")
        if not candidates:
            missing_checkpoints += 1
            missing_keys.append(key)
            if os.path.isdir(os.path.dirname(cache_dir)):
                orphaned_mappings += 1
            continue
        state, _ = load_checkpoint_state(cache_dir, requested_checkpoint="latest.pkl")
        started = is_checkpoint_started(state)
        finished = is_checkpoint_finished(state)
        if started and not finished:
            # stale by heartbeat / newest checkpoint mtime
            heartbeat_ts = _parse_iso(str(hb.get(agent_id, {}).get("updated_at", "")))
            newest_mtime = max([os.path.getmtime(p) for p in candidates], default=0.0)
            newest_dt = datetime.fromtimestamp(newest_mtime, tz=timezone.utc)
            touch_dt = heartbeat_ts or newest_dt
            age = (now - touch_dt).total_seconds()
            if age > stale_seconds:
                stale_tasks += 1
                stale_keys.append(key)
                stale_pairs.append(
                    {
                        "agent_class_name": str(entry.get("agent_class_name", "")),
                        "task_key": str(entry.get("task_key", "")),
                        "canonical_task_key": str(key),
                    }
                )
        # recoverable if latest missing but fallback exists
        latest_path = os.path.join(cache_dir, "latest.pkl")
        if not os.path.exists(latest_path) and candidates:
            recoverable_tasks += 1
            recoverable_keys.append(key)

    mapped_agent_ids = {
        str(entry.get("agent_id"))
        for entry in task_mapping
        if isinstance(entry, dict) and entry.get("agent_id")
    }
    agent_working_dir = os.path.join(working_dir, "agent_working")
    orphaned_dirs = 0
    if os.path.isdir(agent_working_dir):
        for dirname in os.listdir(agent_working_dir):
            abs_dir = os.path.join(agent_working_dir, dirname)
            if not os.path.isdir(abs_dir):
                continue
            if dirname not in mapped_agent_ids:
                orphaned_dirs += 1

    recreated = 0
    for attempts in task_attempts.values():
        if len(attempts) > 1:
            recreated += 1

    details = {
        "stale_task_keys": stale_keys,
        "stale_task_pairs": stale_pairs,
        "missing_checkpoint_task_keys": missing_keys,
        "recoverable_task_keys": recoverable_keys,
    }
    details.update(doctor_master_state(working_dir, load_task_state(working_dir)))
    return DoctorSummary(
        duplicate_active_tasks=duplicates,
        missing_checkpoints=missing_checkpoints,
        stale_tasks=stale_tasks,
        orphaned_mappings=orphaned_mappings,
        orphaned_agent_dirs=orphaned_dirs,
        recreated_tasks=recreated,
        recoverable_tasks=recoverable_tasks,
        run_id=run_id,
        details=details,
    )


def doctor_master_state(working_dir: str, task_state: Dict[str, Any]) -> Dict[str, Any]:
    state_dir = get_state_dir(working_dir)
    required = {
        "master_state": os.path.join(state_dir, MASTER_STATE_FILE),
        "master_decisions": os.path.join(state_dir, MASTER_DECISIONS_FILE),
        "artifact_index": os.path.join(state_dir, ARTIFACT_INDEX_FILE),
        "task_queue_snapshot": os.path.join(state_dir, TASK_QUEUE_SNAPSHOT_FILE),
    }
    missing_files = [name for name, path in required.items() if not os.path.exists(path)]

    queue_snapshot = load_task_queue_snapshot(working_dir)
    pending = queue_snapshot.get("pending_queue", []) if isinstance(queue_snapshot, dict) else []
    queue_keys = {
        str(item.get("canonical_task_key"))
        for item in pending
        if isinstance(item, dict) and item.get("canonical_task_key")
    }
    state_pending_keys = {
        key
        for key, val in (task_state or {}).items()
        if isinstance(val, dict) and str(val.get("status", "")) in {"pending", "queued", "running"}
    }
    queue_mismatch = sorted(list(state_pending_keys - queue_keys))

    decision_count = 0
    decisions_path = required["master_decisions"]
    if os.path.exists(decisions_path):
        try:
            with open(decisions_path, "r", encoding="utf-8") as f:
                for _ in f:
                    decision_count += 1
        except Exception:
            decision_count = 0

    artifact_index = load_artifact_index(working_dir)
    invalid_artifact_rows = 0
    duplicate_artifact_ids = 0
    if isinstance(artifact_index, dict):
        seen_ids = set()
        for aid, row in artifact_index.items():
            if aid in seen_ids:
                duplicate_artifact_ids += 1
            seen_ids.add(aid)
            if not isinstance(row, dict):
                invalid_artifact_rows += 1
                continue
            if "source_tier" not in row:
                invalid_artifact_rows += 1

    master_state = load_master_state(working_dir)
    stale_gate_clock = False
    if isinstance(master_state, dict):
        raw_gate_ts = master_state.get("last_gate_review_at")
        gate_ts = _parse_iso(str(raw_gate_ts)) if raw_gate_ts else None
        if gate_ts is not None:
            if gate_ts.tzinfo is None:
                gate_ts = gate_ts.replace(tzinfo=timezone.utc)
            stale_gate_clock = (datetime.now(timezone.utc) - gate_ts).total_seconds() > 3600

    return {
        "master_missing_files": missing_files,
        "master_queue_mismatch_count": len(queue_mismatch),
        "master_queue_mismatch_keys": queue_mismatch[:50],
        "master_decision_count": decision_count,
        "master_invalid_artifact_rows": invalid_artifact_rows,
        "master_duplicate_artifact_ids": duplicate_artifact_ids,
        "master_stale_gate_clock": stale_gate_clock,
    }


def write_recovery_report(working_dir: str, report: Dict[str, Any]) -> str:
    path = _state_path(working_dir, RECOVERY_REPORT_FILE)
    save_json_atomic(path, report)
    return path


def repair_task_mapping(
    *,
    working_dir: str,
    task_mapping: List[Dict[str, Any]],
    task_attempts: Dict[str, List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Compact mapping to one active entry per canonical key.

    Selection policy:
    - Prefer entries with any valid checkpoint file
    - Tie-break by latest checkpoint mtime, then newest mapping position
    """
    grouped: Dict[str, List[Tuple[int, Dict[str, Any]]]] = {}
    for idx, entry in enumerate(task_mapping):
        key = entry.get("canonical_task_key")
        if not key:
            continue
        grouped.setdefault(key, []).append((idx, entry))

    kept_entries: List[Tuple[int, Dict[str, Any]]] = []
    removed_entries: List[Dict[str, Any]] = []
    repointed = 0

    for key, candidates in grouped.items():
        scored: List[Tuple[int, float, int, Dict[str, Any]]] = []
        for idx, entry in candidates:
            agent_id = entry.get("agent_id", "")
            cache_dir = os.path.join(working_dir, "agent_working", agent_id, ".cache")
            ckpts = checkpoint_candidates(cache_dir, requested_checkpoint="latest.pkl")
            has_ckpt = 1 if ckpts else 0
            newest_mtime = max([os.path.getmtime(p) for p in ckpts], default=0.0)
            scored.append((has_ckpt, newest_mtime, idx, entry))

        scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        winner = scored[0][3]
        kept_entries.append((scored[0][2], winner))
        for _, _, _, entry in scored[1:]:
            removed_entries.append(entry)
            repointed += 1

        attempts = task_attempts.setdefault(key, [])
        winner_agent_id = winner.get("agent_id")
        if winner_agent_id and all(a.get("agent_id") != winner_agent_id for a in attempts):
            attempts.append(
                {
                    "agent_id": winner_agent_id,
                    "status": "repaired_active",
                    "updated_at": utc_now_iso(),
                }
            )

    kept_entries.sort(key=lambda x: x[0])
    new_mapping = [item[1] for item in kept_entries]

    stats = {
        "original_entries": len(task_mapping),
        "new_entries": len(new_mapping),
        "removed_entries": len(removed_entries),
        "repointed_entries": repointed,
        "removed_agent_ids": [e.get("agent_id") for e in removed_entries if e.get("agent_id")],
    }
    return new_mapping, stats


def repair_master_state(
    *,
    working_dir: str,
    task_state: Dict[str, Any],
    default_master_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state_dir = get_state_dir(working_dir)
    os.makedirs(state_dir, exist_ok=True)

    stats = {
        "created_master_state": False,
        "repaired_queue_snapshot": False,
        "rewrote_artifact_index": False,
        "queue_entries": 0,
    }

    master_state = load_master_state(working_dir)
    if not isinstance(master_state, dict) or not master_state:
        master_state = default_master_state or {
            "master_enabled": True,
            "strategy": "balanced",
            "cycle_index": 0,
            "last_gate_review_at": utc_now_iso(),
            "last_decision_confidence": 0.0,
            "master_cycles": 0,
            "mutations_applied": {
                "ADD_TASK": 0,
                "DROP_TASK": 0,
                "REPRIORITIZE_TASK": 0,
                "REWRITE_GUIDANCE": 0,
                "REQUEST_RETRY": 0,
                "SOURCE_POLICY_UPDATE": 0,
            },
            "source_policy": {},
            "initial_task_count": len(task_state),
            "added_tasks_by_stage": {"collect": 0, "analyze": 0, "report": 0},
            "updated_at": utc_now_iso(),
        }
        stats["created_master_state"] = True
    save_master_state(working_dir, master_state)

    desired_pending_by_key: Dict[str, Dict[str, Any]] = {}
    for key, value in (task_state or {}).items():
        if not isinstance(value, dict):
            continue
        status = str(value.get("status", ""))
        if status not in {"pending", "queued", "running"}:
            continue
        desired_pending_by_key[str(key)] = {
            "canonical_task_key": str(key),
            "priority": value.get("priority", 99),
            "profile_name": value.get("profile", ""),
            "stage_name": value.get("stage", ""),
            "raw_task_text": value.get("raw_task_text", ""),
        }

    def _pending_sort_key(item: Dict[str, Any]) -> tuple:
        stage_rank = {"collect": 1, "analyze": 2, "report": 3}
        stage = str(item.get("stage_name", "collect"))
        priority = int(item.get("priority", 99) or 99)
        key = str(item.get("canonical_task_key", ""))
        return (stage_rank.get(stage, 99), priority, key)

    snapshot = load_task_queue_snapshot(working_dir)
    snapshot_pending = snapshot.get("pending_queue") if isinstance(snapshot, dict) else None
    rebuilt_pending: List[Dict[str, Any]] = []
    repaired_queue_snapshot = False

    if isinstance(snapshot_pending, list):
        seen_keys = set()
        for row in snapshot_pending:
            if not isinstance(row, dict):
                repaired_queue_snapshot = True
                continue
            key = str(row.get("canonical_task_key", "")).strip()
            if not key:
                repaired_queue_snapshot = True
                continue
            if key in seen_keys:
                repaired_queue_snapshot = True
                continue
            desired = desired_pending_by_key.get(key)
            if desired is None:
                # Snapshot entry no longer pending/running.
                repaired_queue_snapshot = True
                continue
            merged = dict(row)
            merged["canonical_task_key"] = key
            merged.setdefault("priority", desired.get("priority", 99))
            merged.setdefault("profile_name", desired.get("profile_name", ""))
            merged.setdefault("stage_name", desired.get("stage_name", ""))
            merged.setdefault("raw_task_text", desired.get("raw_task_text", ""))
            rebuilt_pending.append(merged)
            seen_keys.add(key)

        missing_keys = [k for k in desired_pending_by_key.keys() if k not in seen_keys]
        if missing_keys:
            repaired_queue_snapshot = True
            extras = [desired_pending_by_key[k] for k in missing_keys]
            extras.sort(key=_pending_sort_key)
            rebuilt_pending.extend(extras)
    else:
        repaired_queue_snapshot = True
        rebuilt_pending = sorted(desired_pending_by_key.values(), key=_pending_sort_key)

    if repaired_queue_snapshot:
        snapshot = {
            "saved_at": utc_now_iso(),
            "pending_queue_size": len(rebuilt_pending),
            "pending_queue": rebuilt_pending,
        }
        save_task_queue_snapshot(working_dir, snapshot)
        stats["repaired_queue_snapshot"] = True
        stats["queue_entries"] = len(rebuilt_pending)
    else:
        stats["queue_entries"] = len(snapshot_pending or [])

    artifact_index = load_artifact_index(working_dir)
    artifact_path = os.path.join(state_dir, ARTIFACT_INDEX_FILE)
    if (not isinstance(artifact_index, dict)) or (not os.path.exists(artifact_path)):
        save_artifact_index(working_dir, {})
        stats["rewrote_artifact_index"] = True

    decisions_path = os.path.join(state_dir, MASTER_DECISIONS_FILE)
    if not os.path.exists(decisions_path):
        with open(decisions_path, "w", encoding="utf-8") as f:
            f.write("")

    return stats
