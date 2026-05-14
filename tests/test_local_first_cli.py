"""Tests for local-first CLI execution policy in run_report.py."""

from __future__ import annotations

import subprocess

import run_report


def test_parse_arguments_defaults_to_local(monkeypatch):
    monkeypatch.delenv("FINSIGHT_EXECUTOR", raising=False)
    args = run_report.parse_arguments([])
    assert args.executor == "local"
    assert args._executor_explicit is False


def test_parse_arguments_ignores_env_executor_default(monkeypatch):
    monkeypatch.setenv("FINSIGHT_EXECUTOR", "render")
    args = run_report.parse_arguments([])
    assert args.executor == "local"
    assert args._executor_explicit is False


def test_resolve_execution_backend_warns_and_keeps_local(monkeypatch):
    monkeypatch.setenv("FINSIGHT_EXECUTOR", "render")
    args = run_report.parse_arguments([])
    executor, warning = run_report.resolve_execution_backend(args)
    assert executor == "local"
    assert warning is not None
    assert "Ignoring FINSIGHT_EXECUTOR" in warning


def test_resolve_execution_backend_allows_explicit_render(monkeypatch):
    monkeypatch.setenv("FINSIGHT_EXECUTOR", "render")
    args = run_report.parse_arguments(["--executor", "render"])
    executor, warning = run_report.resolve_execution_backend(args)
    assert executor == "render"
    assert warning is None


def test_planner_flag_forces_regeneration():
    args = run_report.parse_arguments(["--planner"])
    assert run_report.should_force_planner(args) is True


def test_force_planner_alias_forces_regeneration():
    args = run_report.parse_arguments(["--force-planner"])
    assert run_report.should_force_planner(args) is True


def test_make_report_is_local_only_command():
    result = subprocess.run(
        ["make", "-n", "report"],
        check=True,
        capture_output=True,
        text=True,
    )
    stdout = result.stdout
    assert "run_report.py" in stdout
    assert "--executor local" in stdout
    assert "--general" in stdout
    assert "--pdf-mode auto" in stdout
    assert "--purge-stale-images" in stdout
