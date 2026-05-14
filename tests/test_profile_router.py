"""Tests for profile-based config routing and resolved config generation."""

from __future__ import annotations

from pathlib import Path

import yaml

from src.config.profile_router import (
    SUPPORTED_PROFILES,
    ensure_profile_configs,
    normalize_profile_name,
    resolve_and_write_config,
    validate_english_profile_tasks,
)


def test_normalize_profile_name_supports_aliases():
    assert normalize_profile_name("financial-company") == "financial_company"
    assert normalize_profile_name("financial_macro") == "financial_macro"
    assert normalize_profile_name("market") == "industry"


def test_ensure_profile_configs_creates_all(tmp_path: Path):
    written = ensure_profile_configs(str(tmp_path))
    assert len(written) == len(SUPPORTED_PROFILES)
    for profile in SUPPORTED_PROFILES:
        assert (tmp_path / f"my_config_{profile}.yaml").exists()


def test_profile_templates_are_english_only(tmp_path: Path):
    ensure_profile_configs(str(tmp_path))
    for profile in SUPPORTED_PROFILES:
        profile_path = tmp_path / f"my_config_{profile}.yaml"
        payload = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        violations = validate_english_profile_tasks(payload)
        assert violations == []


def test_resolve_and_write_merges_profiles_and_dedupes_tasks(tmp_path: Path):
    base_config = tmp_path / "my_config.yaml"
    base_config.write_text(
        yaml.safe_dump(
            {
                "target_name": "Semiconductor Test",
                "target_type": "industry",
                "target_profiles": ["industry"],
                "custom_collect_tasks": ["Shared collect task"],
                "custom_analysis_tasks": ["Shared analysis task"],
            },
            sort_keys=False,
            allow_unicode=False,
        ),
        encoding="utf-8",
    )
    resolved_path = tmp_path / ".runtime" / "my_config_resolved.yaml"

    result = resolve_and_write_config(
        base_config_path=str(base_config),
        selected_profiles=["company", "macro"],
        resolved_config_path=str(resolved_path),
        planner_overrides={
            "target_type": "company",
            "custom_collect_tasks": ["Shared collect task", "Planner collect task"],
            "custom_analysis_tasks": ["Planner analysis task"],
        },
        runtime_overrides={"pdf_mode": "skip"},
    )

    assert result["selected_profiles"] == ["company", "macro"]
    payload = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
    assert payload["target_type"] == "company"
    assert payload["target_profiles"] == ["company", "macro"]
    assert payload["pdf_mode"] == "skip"
    assert "Shared collect task" in payload["custom_collect_tasks"]
    assert payload["custom_collect_tasks"].count("Shared collect task") == 1
    assert "Planner collect task" in payload["custom_collect_tasks"]
    assert "Planner analysis task" in payload["custom_analysis_tasks"]
