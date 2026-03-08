"""Tests for profile-selection CLI behavior in run_report.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml
import pytest

import run_report


def test_collect_cli_profiles_supports_named_and_generic_flags():
    args = run_report.parse_arguments(
        ["--company", "--macro", "--profile", "financial-industry", "--profile", "company"]
    )
    assert run_report.collect_cli_profiles(args) == ["company", "macro", "financial_industry"]


def test_collect_cli_profiles_rejects_unknown_profile():
    args = run_report.parse_arguments(["--profile", "unknown_profile"])
    with pytest.raises(ValueError):
        run_report.collect_cli_profiles(args)


def test_dry_run_requires_profiles(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, "run_report.py", "--dry-run"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "No profiles selected" in result.stderr


def test_dry_run_writes_resolved_config_for_selected_profiles(tmp_path: Path):
    base_config = tmp_path / "my_router.yaml"
    resolved_config = tmp_path / ".runtime" / "my_resolved.yaml"
    result = subprocess.run(
        [
            sys.executable,
            "run_report.py",
            "--dry-run",
            "--company",
            "--macro",
            "--config",
            str(base_config),
            "--resolved-config",
            str(resolved_config),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert resolved_config.exists()
    payload = yaml.safe_load(resolved_config.read_text(encoding="utf-8"))
    assert payload["target_profiles"] == ["company", "macro"]
    assert payload["target_type"] == "company"


def test_dry_run_with_resolved_input_skips_profile_requirement(tmp_path: Path):
    resolved_input = tmp_path / "resolved_input.yaml"
    resolved_input.write_text(
        yaml.safe_dump(
            {
                "target_name": "Resolved Topic",
                "target_type": "general",
                "target_profiles": ["general"],
                "custom_collect_tasks": [],
                "custom_analysis_tasks": [],
            },
            sort_keys=False,
            allow_unicode=False,
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "run_report.py",
            "--dry-run",
            "--resolved-input",
            "--config",
            str(resolved_input),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Using pre-resolved config" in result.stderr
