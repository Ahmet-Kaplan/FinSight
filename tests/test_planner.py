"""Tests for planner schema validation"""
import pytest
from pydantic import ValidationError
from src.planner.schema import PlannerConfig

def test_planner_config_valid():
    config = PlannerConfig(
        target_name="Apple Inc.",
        stock_code="AAPL",
        target_type="company",
        target_profiles=["company"],
        market="US",
        language="en",
    )
    assert config.target_name == "Apple Inc."
    assert config.market == "US"

def test_planner_config_defaults():
    config = PlannerConfig(target_name="Test")
    assert config.stock_code == ""
    assert config.language == "en"
    assert config.market == ""
    assert config.custom_collect_tasks == []
    assert config.target_profiles == []

def test_planner_config_rejects_invalid_market():
    with pytest.raises(ValidationError):
        PlannerConfig(target_name="Test", market="INVALID")

def test_planner_config_rejects_invalid_language():
    with pytest.raises(ValidationError):
        PlannerConfig(target_name="Test", language="fr")

def test_planner_config_rejects_invalid_type():
    with pytest.raises(ValidationError):
        PlannerConfig(target_name="Test", target_type="invalid_type")


def test_planner_config_rejects_invalid_profile_in_target_profiles():
    with pytest.raises(ValidationError):
        PlannerConfig(target_name="Test", target_profiles=["unknown_type"])
