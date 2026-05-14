"""Regression tests for prompt profile mapping across target types."""

from src.agents.data_analyzer.data_analyzer import DataAnalyzer
from src.agents.report_generator.report_generator import ReportGenerator
from src.utils.prompt_loader import get_prompt_loader


def test_data_analyzer_profile_mapping():
    assert DataAnalyzer._resolve_prompt_profile("industry") == "general"
    assert DataAnalyzer._resolve_prompt_profile("general") == "general"
    assert DataAnalyzer._resolve_prompt_profile("company") == "financial"
    assert DataAnalyzer._resolve_prompt_profile("macro") == "financial"
    assert DataAnalyzer._resolve_prompt_profile("financial_industry") == "financial"


def test_report_generator_profile_mapping():
    assert ReportGenerator._resolve_prompt_profile("industry") == "general"
    assert ReportGenerator._resolve_prompt_profile("company") == "general"
    assert ReportGenerator._resolve_prompt_profile("macro") == "general"
    assert ReportGenerator._resolve_prompt_profile("financial_industry") == "financial_industry"
    assert ReportGenerator._resolve_prompt_profile("financial_unknown") == "financial_company"
    assert ReportGenerator._resolve_prompt_profile("unknown_type") == "general"


def test_mapped_profiles_load_prompt_files():
    analyzer_targets = ["industry", "general", "company", "macro", "financial_industry"]
    for target in analyzer_targets:
        profile = DataAnalyzer._resolve_prompt_profile(target)
        loader = get_prompt_loader("data_analyzer", report_type=profile)
        assert loader.get_prompt("data_analysis") is not None

    report_targets = [
        "industry",
        "general",
        "company",
        "macro",
        "financial_company",
        "financial_industry",
        "financial_macro",
        "financial_unknown",
    ]
    for target in report_targets:
        profile = ReportGenerator._resolve_prompt_profile(target)
        loader = get_prompt_loader("report_generator", report_type=profile)
        assert loader.get_prompt("outline_draft") is not None
