from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from src.orchestration.master_types import (
    ArtifactRecord,
    MasterDecision,
    TaskCompletionEvent,
    TaskMutation,
)
from src.utils.recovery import (
    get_state_dir,
    load_json,
    save_json_atomic,
    utc_now_iso,
    canonical_task_key,
)


MASTER_STATE_FILE = "master_state.json"
MASTER_DECISIONS_FILE = "master_decisions.jsonl"
ARTIFACT_INDEX_FILE = "artifact_index.json"
TASK_QUEUE_SNAPSHOT_FILE = "task_queue_snapshot.json"
MASTER_HEALTH_FILE = "master_health.json"
MASTER_ESCALATIONS_FILE = "master_escalations.jsonl"

MUTATION_ORDER = {
    "DROP_TASK": 0,
    "REWRITE_GUIDANCE": 1,
    "REPRIORITIZE_TASK": 2,
    "ADD_TASK": 3,
    "REQUEST_RETRY": 4,
    "SOURCE_POLICY_UPDATE": 5,
}

STAGE_RANK = {"collect": 1, "analyze": 2, "report": 3}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return _now_utc()


def _sha1_text(text: str) -> str:
    return hashlib.sha1(str(text).encode("utf-8", errors="replace")).hexdigest()


def _safe_preview(value: Any, limit: int = 500) -> str:
    text = str(value or "")
    text = " ".join(text.split())
    return text[:limit]


class MasterCoordinator:
    def __init__(
        self,
        *,
        working_dir: str,
        enabled: bool = True,
        batch_size: int = 3,
        batch_max_age_sec: int = 600,
        max_added_tasks_per_stage: int = 8,
        max_total_task_growth_pct: int = 25,
        replan_cooldown_sec: int = 120,
        strategy: str = "balanced",
        allow_drop: bool = False,
    ):
        self.working_dir = working_dir
        self.state_dir = get_state_dir(working_dir)
        self.enabled = bool(enabled)
        self.batch_size = max(1, int(batch_size))
        self.batch_max_age_sec = max(30, int(batch_max_age_sec))
        self.max_added_tasks_per_stage = max(0, int(max_added_tasks_per_stage))
        self.max_total_task_growth_pct = max(0, int(max_total_task_growth_pct))
        self.replan_cooldown_sec = max(0, int(replan_cooldown_sec))
        self.strategy = str(strategy or "balanced").strip().lower()
        self.allow_drop = bool(allow_drop)
        if self.strategy not in {"quality", "balanced", "speed"}:
            self.strategy = "balanced"

        self.master_state_path = os.path.join(self.state_dir, MASTER_STATE_FILE)
        self.decisions_path = os.path.join(self.state_dir, MASTER_DECISIONS_FILE)
        self.artifact_index_path = os.path.join(self.state_dir, ARTIFACT_INDEX_FILE)
        self.queue_snapshot_path = os.path.join(self.state_dir, TASK_QUEUE_SNAPSHOT_FILE)
        self.master_health_path = os.path.join(self.state_dir, MASTER_HEALTH_FILE)
        self.master_escalations_path = os.path.join(self.state_dir, MASTER_ESCALATIONS_FILE)

        self.state: Dict[str, Any] = {}
        self.artifact_index: Dict[str, Dict[str, Any]] = {}
        self._load_state()
        self._load_artifact_index()

    def _default_state(self) -> Dict[str, Any]:
        now = utc_now_iso()
        return {
            "master_enabled": self.enabled,
            "strategy": self.strategy,
            "cycle_index": 0,
            "last_gate_review_at": now,
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
            "policy": {"allow_drop": self.allow_drop},
            "initial_task_count": 0,
            "added_tasks_by_stage": {"collect": 0, "analyze": 0, "report": 0},
            "updated_at": now,
        }

    def _load_state(self) -> None:
        loaded = load_json(self.master_state_path, {})
        base = self._default_state()
        if isinstance(loaded, dict):
            base.update(loaded)
        base["strategy"] = self.strategy
        base["master_enabled"] = self.enabled
        base.setdefault("policy", {})
        base["policy"]["allow_drop"] = self.allow_drop
        self.state = base

    def _save_state(self) -> None:
        self.state["updated_at"] = utc_now_iso()
        save_json_atomic(self.master_state_path, self.state)

    def _load_artifact_index(self) -> None:
        loaded = load_json(self.artifact_index_path, {})
        self.artifact_index = loaded if isinstance(loaded, dict) else {}

    def _save_artifact_index(self) -> None:
        save_json_atomic(self.artifact_index_path, self.artifact_index)

    def bootstrap(self, initial_task_count: int) -> None:
        if int(self.state.get("initial_task_count", 0) or 0) <= 0:
            self.state["initial_task_count"] = int(initial_task_count)
        self._save_state()

    def save_queue_snapshot(self, pending_queue: List[Dict[str, Any]]) -> None:
        snapshot: List[Dict[str, Any]] = []
        for idx, item in enumerate(pending_queue):
            task_input = item.get("task_input", {})
            input_data = task_input.get("input_data", {})
            snapshot.append(
                {
                    "canonical_task_key": item.get("canonical_task_key"),
                    "priority": item.get("priority", 0),
                    "profile_name": item.get("profile_name", ""),
                    "stage_name": input_data.get("stage_name", ""),
                    "raw_task_text": item.get("raw_task_text", ""),
                    "agent_class_name": getattr(item.get("agent_class"), "AGENT_NAME", ""),
                    "task_input": task_input,
                    "agent_kwargs": item.get("agent_kwargs", {}),
                    "order_index": item.get("order_index", idx),
                }
            )
        payload = {
            "saved_at": utc_now_iso(),
            "pending_queue_size": len(snapshot),
            "pending_queue": snapshot,
        }
        save_json_atomic(self.queue_snapshot_path, payload)

    def load_queue_snapshot(self) -> Dict[str, Any]:
        payload = load_json(self.queue_snapshot_path, {})
        return payload if isinstance(payload, dict) else {}

    def append_decision(self, decision: MasterDecision) -> None:
        line = json.dumps(decision.to_dict(), ensure_ascii=False)
        with open(self.decisions_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def save_health_snapshot(self, snapshot: Dict[str, Any]) -> None:
        payload = dict(snapshot or {})
        payload["updated_at"] = utc_now_iso()
        save_json_atomic(self.master_health_path, payload)

    def load_health_snapshot(self) -> Dict[str, Any]:
        loaded = load_json(self.master_health_path, {})
        return loaded if isinstance(loaded, dict) else {}

    def append_escalation(self, row: Dict[str, Any]) -> None:
        line = dict(row or {})
        line.setdefault("created_at", utc_now_iso())
        with open(self.master_escalations_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def evaluate_health(
        self,
        *,
        doctor_summary: Dict[str, Any],
        running_stage_counts: Dict[str, int],
        oldest_running_checkpoint_age_sec: float,
        time_since_last_completion_sec: float,
        recent_failure_rate: float,
        stale_seconds: int,
        active_recovery_action: str = "",
    ) -> Dict[str, Any]:
        stale_tasks = int(doctor_summary.get("stale_tasks", 0) or 0)
        recoverable_tasks = int(doctor_summary.get("recoverable_tasks", 0) or 0)
        orphaned_mappings = int(doctor_summary.get("orphaned_mappings", 0) or 0)
        missing_checkpoints = int(doctor_summary.get("missing_checkpoints", 0) or 0)
        running_total = sum(int(v or 0) for v in running_stage_counts.values())

        risk = 0.0
        risk += min(40.0, stale_tasks * 20.0)
        risk += min(15.0, recoverable_tasks * 5.0)
        risk += min(20.0, orphaned_mappings * 5.0)
        risk += min(10.0, missing_checkpoints * 2.0)
        risk += min(15.0, max(0.0, float(recent_failure_rate)) * 50.0)
        if running_total > 0:
            stall_ratio = max(0.0, float(oldest_running_checkpoint_age_sec) / max(float(stale_seconds), 1.0))
            risk += min(20.0, stall_ratio * 10.0)
            idle_ratio = max(0.0, float(time_since_last_completion_sec) / max(float(stale_seconds), 1.0))
            risk += min(20.0, idle_ratio * 10.0)
        if active_recovery_action:
            risk = max(risk, 45.0)
        stall_risk_score = int(max(0.0, min(100.0, round(risk))))
        if stall_risk_score >= 75:
            health_status = "critical"
        elif stall_risk_score >= 35:
            health_status = "degraded"
        else:
            health_status = "healthy"

        return {
            "health_status": health_status,
            "stall_risk_score": stall_risk_score,
            "running_stage_counts": {
                "collect": int(running_stage_counts.get("collect", 0) or 0),
                "analyze": int(running_stage_counts.get("analyze", 0) or 0),
                "report": int(running_stage_counts.get("report", 0) or 0),
            },
            "oldest_running_checkpoint_age_sec": round(float(oldest_running_checkpoint_age_sec or 0.0), 2),
            "time_since_last_completion_sec": round(float(time_since_last_completion_sec or 0.0), 2),
            "recent_failure_rate": round(float(recent_failure_rate or 0.0), 4),
            "doctor": {
                "stale_tasks": stale_tasks,
                "recoverable_tasks": recoverable_tasks,
                "orphaned_mappings": orphaned_mappings,
                "missing_checkpoints": missing_checkpoints,
            },
            "active_recovery_action": str(active_recovery_action or ""),
        }

    @staticmethod
    def source_tier(url: Optional[str]) -> str:
        if not url:
            return "unknown"
        host = urlparse(url).netloc.lower().replace("www.", "")

        if any(x in host for x in ["sec.gov", "edf", "ecb.europa.eu", "federalreserve.gov"]):
            return "regulator"
        if any(x in host for x in ["investor.", "ir.", "annualreport", "10-k", "20-f"]):
            return "company_ir"
        if any(x in host for x in ["wsts.org", "semi.org", "oecd.org", "imf.org", "worldbank.org"]):
            return "industry_assoc"
        if any(x in host for x in ["linkedin.com", "x.com", "twitter.com", "reddit.com"]):
            return "social"
        if any(x in host for x in ["substack.com", "medium.com", "blog.", "wordpress.com"]):
            return "blog"
        if host.endswith(".gov") or host.endswith(".edu"):
            return "official"
        if any(x in host for x in ["reuters.com", "bloomberg.com", "ft.com", "wsj.com"]):
            return "secondary"
        return "unknown"

    @staticmethod
    def quality_score_for_tier(tier: str) -> float:
        mapping = {
            "official": 0.95,
            "regulator": 0.98,
            "company_ir": 0.92,
            "industry_assoc": 0.90,
            "secondary": 0.65,
            "blog": 0.35,
            "social": 0.20,
            "unknown": 0.45,
        }
        return float(mapping.get(tier, 0.45))

    def _extract_artifacts_for_agent(
        self,
        *,
        memory,
        canonical_task_key_value: str,
        agent_id: str,
        stage: str,
        profile: str,
        created_at: str,
    ) -> List[ArtifactRecord]:
        artifacts: List[ArtifactRecord] = []
        logs = [x for x in (memory.log or []) if isinstance(x, dict) and str(x.get("id")) == str(agent_id)]
        if not logs:
            return artifacts

        for entry in logs[-40:]:
            output_data = entry.get("output_data")
            candidates: List[Any] = []
            if isinstance(output_data, list):
                candidates.extend(output_data)
            elif isinstance(output_data, dict):
                result = output_data.get("result")
                if isinstance(result, list):
                    candidates.extend(result)
                elif result is not None:
                    candidates.append(result)
            elif output_data is not None:
                candidates.append(output_data)

            for item in candidates:
                name = getattr(item, "name", None) or ""
                source = getattr(item, "source", None)
                if not source and isinstance(item, dict):
                    source = item.get("link") or item.get("source")
                source = str(source) if source else None
                label = str(name or entry.get("type") or "artifact")
                preview = _safe_preview(getattr(item, "data", item))
                novelty_hash = _sha1_text(preview)
                artifact_id = _sha1_text(f"{canonical_task_key_value}|{source or ''}|{label}")
                tier = self.source_tier(source)
                quality = self.quality_score_for_tier(tier)

                record = ArtifactRecord(
                    artifact_id=artifact_id,
                    canonical_task_key=canonical_task_key_value,
                    name=label,
                    source_url=source,
                    source_tier=tier,  # type: ignore[arg-type]
                    novelty_hash=novelty_hash,
                    coverage_tags=[stage, profile],
                    created_at=created_at,
                    quality_score=quality,
                )
                artifacts.append(record)
        return artifacts

    def ingest_completion(self, event: TaskCompletionEvent, memory) -> TaskCompletionEvent:
        records = self._extract_artifacts_for_agent(
            memory=memory,
            canonical_task_key_value=event.canonical_task_key,
            agent_id=event.agent_id,
            stage=event.stage,
            profile=event.profile,
            created_at=event.completed_at,
        )
        artifact_ids: List[str] = []
        for record in records:
            artifact_ids.append(record.artifact_id)
            self.artifact_index[record.artifact_id] = record.to_dict()
        if artifact_ids:
            self._save_artifact_index()
        event.artifact_ids = artifact_ids
        return event

    def should_review(
        self,
        *,
        completed_buffer: List[TaskCompletionEvent],
        last_gate_review_at: datetime,
    ) -> Optional[str]:
        if not self.enabled or not completed_buffer:
            return None
        now = _now_utc()
        if len(completed_buffer) >= self.batch_size:
            return "batch_size"
        if (now - last_gate_review_at).total_seconds() >= self.batch_max_age_sec:
            return "batch_age"
        return None

    def _high_tier_ratio(self) -> float:
        if not self.artifact_index:
            return 0.0
        high = 0
        for item in self.artifact_index.values():
            tier = item.get("source_tier", "unknown")
            if tier in {"official", "regulator", "company_ir", "industry_assoc"}:
                high += 1
        return high / max(len(self.artifact_index), 1)

    def _min_high_tier_ratio(self) -> float:
        if self.strategy == "quality":
            return 0.70
        if self.strategy == "speed":
            return 0.35
        return 0.50

    def review(
        self,
        *,
        trigger: str,
        completed_buffer: List[TaskCompletionEvent],
        pending_queue: List[Dict[str, Any]],
        task_state: Dict[str, Any],
    ) -> MasterDecision:
        failure_count = sum(1 for x in completed_buffer if x.status == "failed")
        failure_rate = failure_count / max(len(completed_buffer), 1)
        high_tier_ratio = self._high_tier_ratio()

        pending_stage_counts = {"collect": 0, "analyze": 0, "report": 0}
        for item in pending_queue:
            stage_name = item.get("task_input", {}).get("input_data", {}).get("stage_name", "collect")
            if stage_name in pending_stage_counts:
                pending_stage_counts[stage_name] += 1

        mutations: List[TaskMutation] = []

        # 1) Rewrite guidance when source quality is below threshold.
        if high_tier_ratio < self._min_high_tier_ratio():
            rewrite_candidates = [x for x in pending_queue if x.get("task_input", {}).get("input_data", {}).get("stage_name") in {"collect", "analyze"}]
            for item in rewrite_candidates[:3]:
                mutations.append(
                    TaskMutation(
                        op="REWRITE_GUIDANCE",
                        target_canonical_key=item.get("canonical_task_key"),
                        payload={
                            "master_guidance": (
                                "Prioritize official/regulatory/company-IR/industry-association sources. "
                                "Avoid repeating weak sources and duplicate query patterns."
                            )
                        },
                        reason="Source quality below threshold",
                        confidence=0.80,
                    )
                )

        # 2) Retry failed tasks.
        if failure_rate > 0.30:
            for event in completed_buffer:
                if event.status != "failed":
                    continue
                mutations.append(
                    TaskMutation(
                        op="REQUEST_RETRY",
                        target_canonical_key=event.canonical_task_key,
                        payload={"stage": event.stage, "profile": event.profile},
                        reason="High recent failure rate; retry with tightened guidance",
                        confidence=0.75,
                    )
                )
                if len([m for m in mutations if m.op == "REQUEST_RETRY"]) >= 2:
                    break

        # 3) Add one authority-focused collect task if needed and caps permit.
        if high_tier_ratio < self._min_high_tier_ratio():
            added_collect = int(self.state.get("added_tasks_by_stage", {}).get("collect", 0) or 0)
            can_add_stage = added_collect < self.max_added_tasks_per_stage
            initial_count = int(self.state.get("initial_task_count", 0) or 0)
            growth_cap = int(initial_count * (self.max_total_task_growth_pct / 100.0))
            total_added = sum(int(v) for v in self.state.get("added_tasks_by_stage", {}).values())
            can_add_total = total_added < max(growth_cap, 1)
            if can_add_stage and can_add_total:
                add_text = "Collect official/regulatory/company-IR sources to validate weakly-supported quantitative claims from recent batch"
                mutations.append(
                    TaskMutation(
                        op="ADD_TASK",
                        target_canonical_key=None,
                        payload={
                            "stage": "collect",
                            "profile": "industry",
                            "raw_task_text": add_text,
                            "priority": 1,
                        },
                        reason="Low high-tier source ratio",
                        confidence=0.72,
                    )
                )

        # 4) Keep collect ahead of analyze when collect pending is non-trivial.
        if pending_stage_counts["collect"] > 0 and pending_stage_counts["analyze"] > 0:
            for item in pending_queue:
                stage = item.get("task_input", {}).get("input_data", {}).get("stage_name")
                if stage == "collect":
                    mutations.append(
                        TaskMutation(
                            op="REPRIORITIZE_TASK",
                            target_canonical_key=item.get("canonical_task_key"),
                            payload={"priority": 1},
                            reason="Maintain collect-first evidence quality",
                            confidence=0.70,
                        )
                    )
                    break

        # 5) Update source policy in coordinator state.
        mutations.append(
            TaskMutation(
                op="SOURCE_POLICY_UPDATE",
                target_canonical_key=None,
                payload={
                    "min_high_tier_ratio": self._min_high_tier_ratio(),
                    "disallowed_tiers_for_quant": ["social", "blog"],
                },
                reason="Enforce strategy-specific source policy",
                confidence=0.85,
            )
        )

        # Deterministic ordering.
        mutations = sorted(mutations, key=lambda x: (MUTATION_ORDER.get(x.op, 999), x.target_canonical_key or ""))

        cycle_index = int(self.state.get("cycle_index", 0) or 0) + 1
        stats = {
            "failure_rate": round(failure_rate, 4),
            "high_tier_ratio": round(high_tier_ratio, 4),
            "pending_stage_counts": pending_stage_counts,
            "completed_buffer_size": len(completed_buffer),
        }
        confidence = max(0.45, min(0.92, 0.90 - failure_rate * 0.25))
        decision = MasterDecision(
            decision_id=uuid.uuid4().hex[:12],
            cycle_index=cycle_index,
            trigger=str(trigger),  # type: ignore[arg-type]
            mutations=mutations,
            rationale=(
                "Applied deterministic steering based on recent completion quality, "
                "source-tier mix, and failure-rate signals."
            ),
            confidence=round(confidence, 4),
            created_at=utc_now_iso(),
            stats=stats,
        )
        return decision

    def apply_decision(
        self,
        *,
        decision: MasterDecision,
        pending_queue: List[Dict[str, Any]],
        task_state: Dict[str, Any],
        target_name: str,
        target_type: str,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        queue = list(pending_queue)
        stats = {
            "added": 0,
            "dropped": 0,
            "reprioritized": 0,
            "rewritten": 0,
            "retried": 0,
            "source_policy_updates": 0,
        }

        existing_keys = {item.get("canonical_task_key") for item in queue}

        def _find_idx(canonical_key_value: str) -> Optional[int]:
            for idx, item in enumerate(queue):
                if item.get("canonical_task_key") == canonical_key_value:
                    return idx
            return None

        next_order = max([int(x.get("order_index", 0) or 0) for x in queue] + [0]) + 1

        for mutation in decision.mutations:
            op = mutation.op
            target_key = mutation.target_canonical_key
            payload = mutation.payload or {}

            if op == "DROP_TASK" and target_key:
                if not self.allow_drop:
                    continue
                if float(mutation.confidence or 0.0) < 0.85:
                    continue
                target_status = str(task_state.get(target_key, {}).get("status", "pending")).strip().lower()
                if target_status != "pending":
                    continue
                idx = _find_idx(target_key)
                if idx is None:
                    continue
                dropped_item = queue.pop(idx)
                stats["dropped"] += 1
                self.state["mutations_applied"]["DROP_TASK"] += 1
                task_state[target_key] = {
                    **task_state.get(target_key, {}),
                    "status": "dropped",
                    "drop_reason": mutation.reason,
                    "last_update": utc_now_iso(),
                }
                existing_keys.discard(dropped_item.get("canonical_task_key"))

            elif op == "REWRITE_GUIDANCE" and target_key:
                idx = _find_idx(target_key)
                if idx is None:
                    continue
                queue[idx].setdefault("task_input", {}).setdefault("input_data", {})
                existing_guidance = str(queue[idx]["task_input"]["input_data"].get("master_guidance", "")).strip()
                new_guidance = str(payload.get("master_guidance", "")).strip()
                merged = new_guidance if not existing_guidance else f"{existing_guidance}\n{new_guidance}"
                queue[idx]["task_input"]["input_data"]["master_guidance"] = merged.strip()
                stats["rewritten"] += 1
                self.state["mutations_applied"]["REWRITE_GUIDANCE"] += 1

            elif op == "REPRIORITIZE_TASK" and target_key:
                idx = _find_idx(target_key)
                if idx is None:
                    continue
                new_priority = int(payload.get("priority", queue[idx].get("priority", 0)))
                queue[idx]["priority"] = new_priority
                stats["reprioritized"] += 1
                self.state["mutations_applied"]["REPRIORITIZE_TASK"] += 1

            elif op == "ADD_TASK":
                stage = str(payload.get("stage", "collect"))
                profile = str(payload.get("profile", "industry"))
                raw_task_text = str(payload.get("raw_task_text", "")).strip()
                if not raw_task_text:
                    continue
                new_key = canonical_task_key(
                    stage=stage,
                    profile=profile,
                    task_text=raw_task_text,
                    target_name=target_name,
                    target_type=target_type,
                )
                if new_key in existing_keys:
                    continue
                priority = int(payload.get("priority", STAGE_RANK.get(stage, 3)))
                task_str = f"Research target: {target_name} (ticker: ), task: {raw_task_text}"
                if stage == "analyze":
                    task_input_data = {
                        "task": f"Research target: {target_name} (ticker: )",
                        "analysis_task": raw_task_text,
                        "stage_name": "analyze",
                        "profile_name": profile,
                        "previous_profiles": [],
                        "raw_task_text": raw_task_text,
                        "canonical_task_key": new_key,
                        "master_guidance": payload.get("master_guidance", ""),
                    }
                    agent_class_name = "data_analyzer"
                elif stage == "report":
                    task_input_data = {
                        "task": f"Research target: {target_name} (ticker: )",
                        "task_type": target_type,
                        "stage_name": "report",
                        "profile_name": "report",
                        "raw_task_text": raw_task_text,
                        "canonical_task_key": new_key,
                        "master_guidance": payload.get("master_guidance", ""),
                    }
                    agent_class_name = "report_generator"
                else:
                    task_input_data = {
                        "task": task_str,
                        "stage_name": "collect",
                        "profile_name": profile,
                        "previous_profiles": [],
                        "raw_task_text": raw_task_text,
                        "canonical_task_key": new_key,
                        "master_guidance": payload.get("master_guidance", ""),
                    }
                    agent_class_name = "data_collector"

                queue.append(
                    {
                        "canonical_task_key": new_key,
                        "priority": priority,
                        "profile_name": profile,
                        "raw_task_text": raw_task_text,
                        "task_input": {
                            "input_data": task_input_data,
                            "echo": True,
                            "max_iterations": 10,
                            "resume": True,
                        },
                        "agent_kwargs": {},
                        "agent_class_name": agent_class_name,
                        "order_index": next_order,
                    }
                )
                next_order += 1
                existing_keys.add(new_key)
                stats["added"] += 1
                self.state["mutations_applied"]["ADD_TASK"] += 1
                self.state["added_tasks_by_stage"][stage] = int(self.state["added_tasks_by_stage"].get(stage, 0)) + 1
                task_state[new_key] = {
                    "canonical_task_key": new_key,
                    "stage": stage,
                    "profile": profile,
                    "raw_task_text": raw_task_text,
                    "status": "pending",
                    "recoverable": True,
                    "master_added": True,
                    "last_update": utc_now_iso(),
                }

            elif op == "REQUEST_RETRY" and target_key:
                # Retry is implemented as a guidance rewrite on pending clone, or add clone if absent.
                if target_key in existing_keys:
                    idx = _find_idx(target_key)
                    if idx is not None:
                        queue[idx].setdefault("task_input", {}).setdefault("input_data", {})
                        mg = str(queue[idx]["task_input"]["input_data"].get("master_guidance", "")).strip()
                        retry_hint = "Retry requested by master: tighten source quality and avoid prior failed parameters."
                        queue[idx]["task_input"]["input_data"]["master_guidance"] = (f"{mg}\n{retry_hint}").strip()
                        stats["retried"] += 1
                        self.state["mutations_applied"]["REQUEST_RETRY"] += 1
                else:
                    # If target not pending, mark task state for retry hint; runner may requeue from failed pool.
                    task_state[target_key] = {
                        **task_state.get(target_key, {}),
                        "retry_requested": True,
                        "last_update": utc_now_iso(),
                    }

            elif op == "SOURCE_POLICY_UPDATE":
                self.state["source_policy"] = {**self.state.get("source_policy", {}), **payload}
                stats["source_policy_updates"] += 1
                self.state["mutations_applied"]["SOURCE_POLICY_UPDATE"] += 1

        queue = sorted(
            queue,
            key=lambda item: (
                STAGE_RANK.get(item.get("task_input", {}).get("input_data", {}).get("stage_name", "collect"), 99),
                int(item.get("priority", 99)),
                int(item.get("order_index", 10**9)),
            ),
        )

        self.state["cycle_index"] = int(decision.cycle_index)
        self.state["master_cycles"] = int(self.state.get("master_cycles", 0) or 0) + 1
        self.state["last_gate_review_at"] = decision.created_at
        self.state["last_decision_confidence"] = float(decision.confidence)
        self._save_state()
        self.append_decision(decision)
        return queue, stats

    def status_snapshot(self) -> Dict[str, Any]:
        total_artifacts = len(self.artifact_index)
        high_tier_ratio = self._high_tier_ratio()
        queue_snapshot = self.load_queue_snapshot()
        health_snapshot = self.load_health_snapshot()
        pending_queue_size = 0
        if isinstance(queue_snapshot, dict):
            pending_queue_size = int(queue_snapshot.get("pending_queue_size", 0) or 0)
        return {
            "master_cycles": int(self.state.get("master_cycles", 0) or 0),
            "last_gate_at": self.state.get("last_gate_review_at", ""),
            "last_decision_confidence": float(self.state.get("last_decision_confidence", 0.0) or 0.0),
            "mutations_applied": dict(self.state.get("mutations_applied", {})),
            "pending_queue_size": pending_queue_size,
            "artifact_coverage_pct": 100.0 if total_artifacts > 0 else 0.0,
            "high_tier_source_pct": round(high_tier_ratio * 100.0, 2),
            "source_policy": dict(self.state.get("source_policy", {})),
            "total_artifacts": total_artifacts,
            "health_status": str(health_snapshot.get("health_status", "unknown")),
            "stall_risk_score": int(health_snapshot.get("stall_risk_score", 0) or 0),
            "active_recovery_action": str(health_snapshot.get("active_recovery_action", "")),
            "policy": dict(self.state.get("policy", {})),
        }
