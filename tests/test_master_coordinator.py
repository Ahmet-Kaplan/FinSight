from __future__ import annotations

from datetime import timedelta, datetime, timezone

from src.orchestration.master_coordinator import MasterCoordinator
from src.orchestration.master_types import MasterDecision, TaskCompletionEvent, TaskMutation


def _event(idx: int, status: str = "done") -> TaskCompletionEvent:
    now = datetime.now(timezone.utc)
    return TaskCompletionEvent(
        canonical_task_key=f"k{idx}",
        agent_id=f"a{idx}",
        stage="collect",
        profile="industry",
        status=status,  # type: ignore[arg-type]
        started_at=now.isoformat(),
        completed_at=(now + timedelta(seconds=2)).isoformat(),
        duration_sec=2.0,
        artifact_ids=[],
        error=None if status == "done" else "failed",
    )


def test_gate_trigger_batch_size_and_age(tmp_path):
    mc = MasterCoordinator(working_dir=str(tmp_path), enabled=True, batch_size=3, batch_max_age_sec=600)
    last_gate = datetime.now(timezone.utc)

    assert mc.should_review(completed_buffer=[_event(1), _event(2), _event(3)], last_gate_review_at=last_gate) == "batch_size"
    assert mc.should_review(
        completed_buffer=[_event(1)],
        last_gate_review_at=last_gate - timedelta(seconds=601),
    ) == "batch_age"
    assert mc.should_review(completed_buffer=[_event(1)], last_gate_review_at=last_gate) is None


def test_apply_decision_uses_deterministic_mutation_order(tmp_path):
    mc = MasterCoordinator(working_dir=str(tmp_path), enabled=True, allow_drop=True)
    queue = [
        {
            "canonical_task_key": "k1",
            "priority": 1,
            "profile_name": "industry",
            "raw_task_text": "task1",
            "task_input": {"input_data": {"stage_name": "collect", "master_guidance": ""}, "echo": True, "resume": True},
            "agent_kwargs": {},
            "agent_class_name": "data_collector",
            "order_index": 0,
        }
    ]
    task_state = {"k1": {"status": "pending", "stage": "collect"}}
    decision = MasterDecision(
        decision_id="d1",
        cycle_index=1,
        trigger="manual",
        mutations=[
            TaskMutation(
                op="REWRITE_GUIDANCE",
                target_canonical_key="k1",
                payload={"master_guidance": "should_not_apply_if_dropped"},
                reason="rewrite",
                confidence=0.9,
            ),
            TaskMutation(
                op="DROP_TASK",
                target_canonical_key="k1",
                payload={},
                reason="drop-first",
                confidence=0.9,
            ),
        ],
        rationale="test",
        confidence=0.8,
        created_at=datetime.now(timezone.utc).isoformat(),
        stats={},
    )

    new_queue, stats = mc.apply_decision(
        decision=decision,
        pending_queue=queue,
        task_state=task_state,
        target_name="Global Semiconductor Industry",
        target_type="industry",
    )
    assert new_queue == []
    assert stats["dropped"] == 1
    assert task_state["k1"]["status"] == "dropped"


def test_drop_task_blocked_by_guided_auto_policy_default(tmp_path):
    mc = MasterCoordinator(working_dir=str(tmp_path), enabled=True, allow_drop=False)
    queue = [
        {
            "canonical_task_key": "k1",
            "priority": 1,
            "profile_name": "industry",
            "raw_task_text": "task1",
            "task_input": {"input_data": {"stage_name": "collect", "master_guidance": ""}, "echo": True, "resume": True},
            "agent_kwargs": {},
            "agent_class_name": "data_collector",
            "order_index": 0,
        }
    ]
    task_state = {"k1": {"status": "pending", "stage": "collect"}}
    decision = MasterDecision(
        decision_id="d2",
        cycle_index=1,
        trigger="manual",
        mutations=[
            TaskMutation(
                op="DROP_TASK",
                target_canonical_key="k1",
                payload={},
                reason="drop-attempt",
                confidence=0.95,
            ),
        ],
        rationale="test",
        confidence=0.8,
        created_at=datetime.now(timezone.utc).isoformat(),
        stats={},
    )

    new_queue, stats = mc.apply_decision(
        decision=decision,
        pending_queue=queue,
        task_state=task_state,
        target_name="Global Semiconductor Industry",
        target_type="industry",
    )
    assert len(new_queue) == 1
    assert stats["dropped"] == 0
    assert task_state["k1"]["status"] == "pending"


def test_health_classification_and_status_snapshot(tmp_path):
    mc = MasterCoordinator(working_dir=str(tmp_path), enabled=True, allow_drop=False)
    health = mc.evaluate_health(
        doctor_summary={
            "stale_tasks": 2,
            "recoverable_tasks": 1,
            "orphaned_mappings": 1,
            "missing_checkpoints": 0,
        },
        running_stage_counts={"collect": 1, "analyze": 0, "report": 0},
        oldest_running_checkpoint_age_sec=1200.0,
        time_since_last_completion_sec=1500.0,
        recent_failure_rate=0.4,
        stale_seconds=900,
        active_recovery_action="retry(stale:k1)",
    )
    assert health["health_status"] in {"degraded", "critical"}
    assert int(health["stall_risk_score"]) >= 35
    mc.save_health_snapshot(health)
    snapshot = mc.status_snapshot()
    assert snapshot["health_status"] == health["health_status"]
    assert int(snapshot["stall_risk_score"]) == int(health["stall_risk_score"])
    assert snapshot["policy"]["allow_drop"] is False


def test_review_respects_add_caps(tmp_path):
    mc = MasterCoordinator(
        working_dir=str(tmp_path),
        enabled=True,
        max_added_tasks_per_stage=1,
        max_total_task_growth_pct=10,
        strategy="balanced",
    )
    mc.state["initial_task_count"] = 10
    mc.state["added_tasks_by_stage"] = {"collect": 1, "analyze": 0, "report": 0}

    decision = mc.review(
        trigger="manual",
        completed_buffer=[_event(1), _event(2), _event(3)],
        pending_queue=[
            {
                "canonical_task_key": "p1",
                "priority": 1,
                "profile_name": "industry",
                "task_input": {"input_data": {"stage_name": "collect"}, "echo": True, "resume": True},
                "raw_task_text": "collect-x",
            }
        ],
        task_state={},
    )
    assert not any(m.op == "ADD_TASK" for m in decision.mutations)


def test_source_tier_mapping():
    assert MasterCoordinator.source_tier("https://www.sec.gov/Archives/edgar/data/0000000") == "regulator"
    assert MasterCoordinator.source_tier("https://investor.tsmc.com/sites/ir/sec-filings/2024%2020-F.pdf") == "company_ir"
    assert MasterCoordinator.source_tier("https://www.wsts.org/") == "industry_assoc"
    assert MasterCoordinator.source_tier("https://www.linkedin.com/posts/abc") == "social"
