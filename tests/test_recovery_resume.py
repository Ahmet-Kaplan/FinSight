import os
import dill

from src.utils.recovery import canonical_task_key, repair_task_mapping


def test_canonical_task_key_stable_for_case_and_whitespace():
    key_a = canonical_task_key(
        stage="Collect",
        profile="Macro",
        task_text="  GDP   growth   and inflation  ",
        target_name="Global Semiconductor Industry",
        target_type="industry",
    )
    key_b = canonical_task_key(
        stage="collect",
        profile="macro",
        task_text="gdp growth and inflation",
        target_name="global semiconductor industry",
        target_type="INDUSTRY",
    )
    assert key_a == key_b


def test_repair_task_mapping_prefers_entry_with_checkpoint(tmp_path):
    working_dir = str(tmp_path)
    canonical = canonical_task_key(
        stage="collect",
        profile="industry",
        task_text="Inventory levels and utilization rates",
        target_name="Global Semiconductor Industry",
        target_type="industry",
    )

    stale_agent = "agent_data_collector_stale"
    valid_agent = "agent_data_collector_valid"

    # Only valid_agent has checkpoint artifacts.
    cache_dir = tmp_path / "agent_working" / valid_agent / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    with open(cache_dir / "latest.pkl", "wb") as f:
        dill.dump({"current_round": 3, "finished": False}, f)

    task_mapping = [
        {
            "canonical_task_key": canonical,
            "agent_id": stale_agent,
            "agent_class_name": "data_collector",
            "task_key": "legacy-1",
        },
        {
            "canonical_task_key": canonical,
            "agent_id": valid_agent,
            "agent_class_name": "data_collector",
            "task_key": "legacy-2",
        },
    ]

    task_attempts = {canonical: [{"agent_id": stale_agent, "status": "created"}]}

    repaired_mapping, stats = repair_task_mapping(
        working_dir=working_dir,
        task_mapping=task_mapping,
        task_attempts=task_attempts,
    )

    assert len(repaired_mapping) == 1
    assert repaired_mapping[0]["agent_id"] == valid_agent
    assert stats["removed_entries"] == 1
