from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Literal, Optional


TaskStatus = Literal["done", "failed", "skipped"]
TaskStage = Literal["collect", "analyze", "report"]
MutationOp = Literal[
    "ADD_TASK",
    "DROP_TASK",
    "REPRIORITIZE_TASK",
    "REWRITE_GUIDANCE",
    "REQUEST_RETRY",
    "SOURCE_POLICY_UPDATE",
]


@dataclass
class TaskCompletionEvent:
    canonical_task_key: str
    agent_id: str
    stage: TaskStage
    profile: str
    status: TaskStatus
    started_at: str
    completed_at: str
    duration_sec: float
    artifact_ids: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ArtifactRecord:
    artifact_id: str
    canonical_task_key: str
    name: str
    source_url: Optional[str]
    source_tier: Literal[
        "official",
        "regulator",
        "company_ir",
        "industry_assoc",
        "secondary",
        "blog",
        "social",
        "unknown",
    ]
    novelty_hash: str
    coverage_tags: List[str]
    created_at: str
    quality_score: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskMutation:
    op: MutationOp
    target_canonical_key: Optional[str]
    payload: Dict[str, Any]
    reason: str
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MasterDecision:
    decision_id: str
    cycle_index: int
    trigger: Literal["batch_size", "batch_age", "manual"]
    mutations: List[TaskMutation]
    rationale: str
    confidence: float
    created_at: str
    stats: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["mutations"] = [m.to_dict() for m in self.mutations]
        return payload
