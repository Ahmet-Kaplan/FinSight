"""Profile-based config routing and resolved-config generation."""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import yaml


SUPPORTED_PROFILES: Tuple[str, ...] = (
    "company",
    "financial_company",
    "macro",
    "industry",
    "financial_industry",
    "general",
    "financial_macro",
    "governance",
)

PROFILE_ALIASES: Dict[str, str] = {
    "financial-company": "financial_company",
    "financial_industry": "financial_industry",
    "financial-industry": "financial_industry",
    "financial_macro": "financial_macro",
    "financial-macro": "financial_macro",
    "market": "industry",
    "sector": "industry",
}

PROMPT_PROFILE_MAP: Dict[str, Dict[str, str]] = {
    "memory": {
        "company": "financial",
        "financial_company": "financial",
        "macro": "financial",
        "industry": "general",
        "financial_industry": "financial",
        "general": "general",
        "financial_macro": "financial",
        "governance": "general",
    },
    "data_analyzer": {
        "company": "financial",
        "financial_company": "financial",
        "macro": "financial",
        "industry": "general",
        "financial_industry": "financial",
        "general": "general",
        "financial_macro": "financial",
        "governance": "general",
    },
    "report_generator": {
        "company": "general",
        "financial_company": "financial_company",
        "macro": "general",
        "industry": "general",
        "financial_industry": "financial_industry",
        "general": "general",
        "financial_macro": "financial_macro",
        "governance": "governance",
    },
}

_CJK_PATTERN = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")


def contains_cjk(text: str) -> bool:
    """Return True when text contains common CJK codepoint ranges."""
    return bool(_CJK_PATTERN.search(text or ""))


def normalize_profile_name(name: str) -> str:
    """Normalize profile aliases and validate support."""
    if not name:
        raise ValueError("Profile name cannot be empty")
    candidate = name.strip().lower().replace(" ", "_")
    canonical = PROFILE_ALIASES.get(candidate, candidate)
    if canonical not in SUPPORTED_PROFILES:
        supported = ", ".join(SUPPORTED_PROFILES)
        raise ValueError(f"Unsupported profile '{name}'. Supported profiles: {supported}")
    return canonical


def profile_config_filename(profile_name: str) -> str:
    canonical = normalize_profile_name(profile_name)
    return f"my_config_{canonical}.yaml"


def _default_profile_templates() -> Dict[str, Dict[str, Any]]:
    """Return default, English-only profile config templates."""
    base = {
        "language": "en",
        "reference_doc_path": "src/template/report_template.docx",
        "use_collect_data_cache": True,
        "use_analysis_cache": True,
        "use_report_outline_cache": True,
        "use_full_report_cache": True,
        "use_post_process_cache": True,
    }
    return {
        "company": {
            **base,
            "profile_name": "company",
            "target_type": "company",
            "outline_template_path": "src/template/company_outline.md",
            "custom_collect_tasks": [
                "Historical income statement, balance sheet, and cash flow statement for the last 5 to 10 fiscal years",
                "Stock price, volume, market capitalization, and valuation multiple history",
                "Revenue and margin breakdown by segment, geography, and product line",
                "Capital allocation records: dividends, share buybacks, and major acquisitions",
                "Ownership structure: institutional holders, insider ownership, and float changes",
                "Recent filings, annual reports, investor presentations, and earnings transcripts",
                "Peer set data for direct competitors and relevant benchmark indices",
                "Consensus forecasts: revenue, EBITDA, EPS, and free cash flow estimates",
                "Key operational KPIs disclosed by management and industry sources",
                "Material legal, regulatory, and governance disclosures over the last 3 years",
            ],
            "custom_analysis_tasks": [
                "Summarize the company's business model, value proposition, and strategic positioning",
                "Assess management quality, governance structure, and capital allocation discipline",
                "Analyze growth trajectory by segment and identify durable growth drivers",
                "Evaluate profitability quality through gross margin, operating leverage, and cash conversion",
                "Assess balance-sheet resilience, liquidity, and debt maturity risk",
                "Compare valuation versus peers under normalized earnings assumptions",
                "Map competitive moat sources, switching costs, and execution risks",
                "Build base, bull, and bear 3-year scenarios for revenue, margin, and valuation",
                "Identify near-term catalysts and disconfirming indicators for the thesis",
                "Conclude with an investor-ready risk-reward view and monitoring framework",
            ],
        },
        "financial_company": {
            **base,
            "profile_name": "financial_company",
            "target_type": "financial_company",
            "outline_template_path": "src/template/company_outline.md",
            "custom_collect_tasks": [
                "Regulatory capital metrics: CET1, Tier 1, leverage ratio, and capital buffers",
                "Asset quality metrics: NPL ratio, provisioning coverage, and charge-off trends",
                "Net interest income and net interest margin history by reporting segment",
                "Fee income composition by product line and client segment",
                "Liquidity and funding profile: deposit mix, wholesale funding, and maturity ladder",
                "Stress-test outcomes, regulatory actions, and supervisory disclosures",
                "Credit concentration by sector, geography, and counterparty class",
                "Insurance reserves or loan-loss methodology disclosures, as applicable",
                "Peer financial institution metrics and valuation comparables",
                "Recent earnings calls, investor decks, and regulatory filings",
            ],
            "custom_analysis_tasks": [
                "Evaluate earnings quality and sustainability across net interest and fee streams",
                "Assess underwriting discipline and credit-cycle sensitivity",
                "Analyze capital adequacy under baseline and stressed assumptions",
                "Evaluate liquidity, funding stability, and refinancing risk",
                "Assess profitability drivers including expense efficiency and operating leverage",
                "Compare valuation against bank or insurer peers on risk-adjusted metrics",
                "Analyze regulatory and policy changes that can affect returns",
                "Build 3-year base, bull, and bear scenarios for earnings and capital ratios",
                "Identify tail risks such as credit shocks, duration mismatch, and litigation",
                "Provide an investment conclusion with key monitoring triggers",
            ],
        },
        "macro": {
            **base,
            "profile_name": "macro",
            "target_type": "macro",
            "outline_template_path": "src/template/company_outline.md",
            "custom_collect_tasks": [
                "GDP growth, inflation, and labor market data for major economies over 10 years",
                "Policy rates, yield curves, and central bank balance-sheet trends",
                "Household consumption, savings rates, and real income indicators",
                "Business investment, industrial production, and PMI trend data",
                "Credit growth, lending standards, and financial conditions indicators",
                "Trade volumes, commodity prices, and shipping or logistics indicators",
                "Fiscal policy stance, deficits, debt trajectories, and major policy packages",
                "Foreign exchange trends and cross-border capital flow data",
                "Leading and coincident indicators from official statistical agencies",
                "Consensus macro forecasts from multilateral and sell-side sources",
            ],
            "custom_analysis_tasks": [
                "Diagnose the current cycle phase using growth, inflation, and labor indicators",
                "Evaluate the policy mix and likely central bank reaction function",
                "Assess recession versus reacceleration probabilities over the next 12 to 36 months",
                "Analyze credit and liquidity conditions as transmission channels",
                "Assess trade and commodity dynamics as inflation and growth drivers",
                "Compare regional divergences in macro momentum and policy constraints",
                "Develop base, bull, and bear macro scenarios with explicit assumptions",
                "Translate scenarios into implications for rates, FX, equities, and credit",
                "Identify key data releases and policy events as catalysts",
                "Conclude with actionable macro positioning and risk controls",
            ],
        },
        "industry": {
            **base,
            "profile_name": "industry",
            "target_type": "industry",
            "outline_template_path": "src/template/industry_outline.md",
            "custom_collect_tasks": [
                "Global market size, historical growth, and 3-year forecast by region",
                "Value-chain segmentation with revenue and margin pools by segment",
                "Top company market shares and concentration trends",
                "Demand drivers by end market and customer cohort",
                "Supply-side capacity, utilization, and expansion pipeline data",
                "Pricing or ASP trends and key cost-input benchmarks",
                "Regulatory, policy, and subsidy frameworks across major regions",
                "Technology roadmap milestones and adoption curves",
                "Recent investment, M&A, and financing activity in the sector",
                "Industry association datasets and leading third-party research estimates",
            ],
            "custom_analysis_tasks": [
                "Assess structural growth versus cyclical demand components",
                "Map value capture across the value chain and identify margin leaders",
                "Analyze competitive structure and barriers to entry",
                "Evaluate supply-demand balance and utilization risk over 3 years",
                "Assess pricing power dynamics and input-cost pass-through behavior",
                "Analyze regional policy effects on supply chain and competitiveness",
                "Compare incumbent and challenger strategies by segment",
                "Build base, bull, and bear sector scenarios with explicit assumptions",
                "Identify high-conviction themes, catalysts, and key downside risks",
                "Deliver investor-oriented sector conclusion and monitoring framework",
            ],
        },
        "financial_industry": {
            **base,
            "profile_name": "financial_industry",
            "target_type": "financial_industry",
            "outline_template_path": "src/template/industry_outline.md",
            "custom_collect_tasks": [
                "Sector-level assets, liabilities, and profitability metrics across major institutions",
                "Industry capital and solvency aggregates by region and regulator",
                "Credit growth, default rates, and provisioning trends by product category",
                "Deposit, funding, and liquidity mix across the sector",
                "Fee and non-interest revenue composition trends",
                "Regulatory rule changes, supervisory guidance, and compliance timelines",
                "Digital adoption, fintech disruption metrics, and channel migration data",
                "M&A activity, strategic partnerships, and market-entry events",
                "Cross-country peer benchmarks for returns, valuation, and risk",
                "Consensus outlooks from regulators, industry bodies, and analysts",
            ],
            "custom_analysis_tasks": [
                "Assess structural profitability and return-on-capital prospects for the sector",
                "Analyze credit-cycle sensitivity and risk concentration by subsegment",
                "Evaluate capital, liquidity, and funding resilience under stress",
                "Assess regulatory burden and likely impact on growth and margins",
                "Map competitive dynamics among incumbents, challengers, and fintech entrants",
                "Analyze technology-led operating leverage and efficiency opportunities",
                "Build 3-year base, bull, and bear scenarios for sector earnings and valuation",
                "Identify leading indicators that signal turning points in sector risk",
                "Evaluate geopolitical, policy, and macro spillover risks",
                "Conclude with an investor-ready sector allocation view",
            ],
        },
        "general": {
            **base,
            "profile_name": "general",
            "target_type": "general",
            "outline_template_path": "src/template/company_outline.md",
            "custom_collect_tasks": [
                "Definitions, scope boundaries, and taxonomy for the research topic",
                "Authoritative datasets and official publications for core metrics",
                "Historical trend data and milestone events over at least 5 years",
                "Key stakeholder landscape and role mapping",
                "Regional comparison data across major geographies",
                "Policy and regulatory context from primary sources",
                "Technology, product, or process adoption metrics",
                "Cost, productivity, and performance benchmark data",
                "Recent case studies and best-practice examples with outcomes",
                "Current forecast ranges from reputable institutions",
            ],
            "custom_analysis_tasks": [
                "Clarify core questions and decision criteria for the report objective",
                "Synthesize historical evolution and current-state baseline",
                "Analyze key causal drivers and interdependencies",
                "Compare strategic alternatives and trade-offs",
                "Assess risks, constraints, and implementation frictions",
                "Evaluate regional or segment-level divergences",
                "Develop base, upside, and downside scenario frameworks",
                "Translate evidence into actionable recommendations",
                "Define leading indicators to track thesis validation",
                "Deliver a concise conclusion with next-step priorities",
            ],
        },
        "financial_macro": {
            **base,
            "profile_name": "financial_macro",
            "target_type": "financial_macro",
            "outline_template_path": "src/template/company_outline.md",
            "custom_collect_tasks": [
                "Policy-rate paths, real-rate estimates, and forward guidance by central bank",
                "Government bond yield curves, term premia, and breakeven inflation series",
                "Credit spreads, default rates, and issuance volumes across rating buckets",
                "Global liquidity indicators and cross-currency funding stress metrics",
                "Equity risk premium proxies and earnings revision breadth",
                "FX volatility, carry metrics, and capital flow indicators",
                "Commodity and energy price dynamics with inflation pass-through metrics",
                "Fiscal impulse estimates and sovereign debt sustainability data",
                "Bank lending standards and private-sector credit conditions",
                "Consensus macro-financial outlooks from major institutions",
            ],
            "custom_analysis_tasks": [
                "Assess macro regime: disinflation, reflation, or stagflation probabilities",
                "Evaluate policy-path implications for rates duration and curve shape",
                "Analyze credit-cycle risk and spread compensation adequacy",
                "Assess equity valuation sensitivity to real rates and growth revisions",
                "Evaluate FX regime shifts and carry trade vulnerability",
                "Analyze liquidity and funding channels as systemic risk amplifiers",
                "Build 3-year base, bull, and bear macro-financial scenarios",
                "Translate scenarios into multi-asset allocation implications",
                "Define event-risk map and trigger thresholds for positioning changes",
                "Conclude with a risk-managed portfolio stance and hedge framework",
            ],
        },
        "governance": {
            **base,
            "profile_name": "governance",
            "target_type": "governance",
            "outline_template_path": "src/template/company_outline.md",
            "custom_collect_tasks": [
                "Policy texts, legal frameworks, and enforcement records for the domain",
                "Institutional governance models across major jurisdictions",
                "Compliance standards, audit requirements, and certification practices",
                "Incident databases and enforcement case outcomes",
                "Stakeholder positions from regulators, firms, and civil society",
                "Operational implementation data from organizations adopting governance controls",
                "Metrics on transparency, accountability, and redress mechanisms",
                "Budget, staffing, and capability data for oversight institutions",
                "International coordination initiatives and standard-setting outputs",
                "Recent legislative proposals and consultation feedback documents",
            ],
            "custom_analysis_tasks": [
                "Compare governance frameworks by scope, enforceability, and practical burden",
                "Assess institutional capacity and implementation feasibility",
                "Analyze compliance incentives and potential regulatory arbitrage",
                "Evaluate effectiveness of existing accountability mechanisms",
                "Assess innovation versus safety trade-offs under different policy designs",
                "Map stakeholder alignment and conflict points",
                "Build scenario analysis for policy tightening, continuity, and fragmentation",
                "Identify measurable indicators of governance effectiveness",
                "Recommend pragmatic control architecture and rollout sequencing",
                "Conclude with governance strategy, risks, and monitoring priorities",
            ],
        },
    }


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML object in {path}, got: {type(data).__name__}")
    return data


def _write_yaml(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=False)


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _dedupe_preserve_order(items: Iterable[str]) -> List[str]:
    output: List[str] = []
    seen = set()
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        key = re.sub(r"\s+", " ", value).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def ensure_base_router_config(base_config_path: str) -> Path:
    """Create a minimal router-style base config if missing."""
    path = Path(base_config_path)
    if path.exists():
        return path
    base_config = {
        "target_name": "Research Topic",
        "stock_code": "",
        "target_type": "general",
        "target_profiles": ["general"],
        "market": "",
        "output_dir": "./outputs/research-topic",
        "language": "en",
        "reference_doc_path": "src/template/report_template.docx",
        "custom_collect_tasks": [],
        "custom_analysis_tasks": [],
        "rate_limits": {
            "search_engines": 1.0,
            "financial_apis": 0.5,
            "fred_api": 0.5,
            "yfinance": 0.2,
        },
        "llm_config_list": [
            {
                "model_name": "${DS_MODEL_NAME}",
                "api_key": "${DS_API_KEY}",
                "base_url": "${DS_BASE_URL}",
                "generation_params": {
                    "temperature": 0.7,
                    "max_tokens": 8192,
                    "top_p": 0.95,
                },
            },
            {
                "model_name": "${EMBEDDING_MODEL_NAME}",
                "api_key": "${EMBEDDING_API_KEY}",
                "base_url": "${EMBEDDING_BASE_URL}",
            },
            {
                "model_name": "${VLM_MODEL_NAME}",
                "api_key": "${VLM_API_KEY}",
                "base_url": "${VLM_BASE_URL}",
            },
        ],
        "use_collect_data_cache": True,
        "use_analysis_cache": True,
        "use_report_outline_cache": True,
        "use_full_report_cache": True,
        "use_post_process_cache": True,
    }
    _write_yaml(path, base_config)
    return path


def ensure_profile_configs(base_dir: str = ".") -> List[Path]:
    """Ensure all profile config files exist with English defaults."""
    root = Path(base_dir)
    templates = _default_profile_templates()
    written: List[Path] = []
    for profile_name, payload in templates.items():
        path = root / profile_config_filename(profile_name)
        if not path.exists():
            _write_yaml(path, payload)
            written.append(path)
    return written


def validate_english_profile_tasks(profile_config: Dict[str, Any]) -> List[str]:
    """Return offending task strings that contain CJK characters."""
    violations: List[str] = []
    for key in ("custom_collect_tasks", "custom_analysis_tasks"):
        for task in profile_config.get(key, []) or []:
            if contains_cjk(str(task)):
                violations.append(str(task))
    return violations


def resolve_and_write_config(
    *,
    base_config_path: str,
    selected_profiles: List[str],
    resolved_config_path: str,
    planner_overrides: Dict[str, Any] | None = None,
    runtime_overrides: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Resolve base + profiles + planner + runtime into a single run config."""
    planner_overrides = planner_overrides or {}
    runtime_overrides = runtime_overrides or {}

    canonical_profiles = [normalize_profile_name(p) for p in selected_profiles]
    canonical_profiles = _dedupe_preserve_order(canonical_profiles)
    if not canonical_profiles:
        raise ValueError("At least one profile must be selected.")

    base_path = ensure_base_router_config(base_config_path)
    ensure_profile_configs(str(base_path.parent))

    default_path = Path(__file__).with_name("default_config.yaml")
    resolved = _load_yaml(default_path)
    base_config = _load_yaml(base_path)
    _deep_merge(resolved, base_config)

    collect_tasks: List[str] = []
    analysis_tasks: List[str] = []

    for profile in canonical_profiles:
        profile_path = base_path.parent / profile_config_filename(profile)
        profile_cfg = _load_yaml(profile_path)
        collect_tasks.extend(profile_cfg.get("custom_collect_tasks", []) or [])
        analysis_tasks.extend(profile_cfg.get("custom_analysis_tasks", []) or [])
        profile_no_tasks = {k: v for k, v in profile_cfg.items() if k not in ("custom_collect_tasks", "custom_analysis_tasks")}
        _deep_merge(resolved, profile_no_tasks)

    collect_tasks.extend(base_config.get("custom_collect_tasks", []) or [])
    analysis_tasks.extend(base_config.get("custom_analysis_tasks", []) or [])
    collect_tasks.extend(planner_overrides.get("custom_collect_tasks", []) or [])
    analysis_tasks.extend(planner_overrides.get("custom_analysis_tasks", []) or [])

    planner_no_tasks = {k: v for k, v in planner_overrides.items() if k not in ("custom_collect_tasks", "custom_analysis_tasks")}
    _deep_merge(resolved, planner_no_tasks)
    _deep_merge(resolved, runtime_overrides)

    planner_primary = planner_overrides.get("target_type")
    if planner_primary:
        primary_type = normalize_profile_name(str(planner_primary))
    else:
        primary_type = canonical_profiles[0]

    resolved["target_type"] = primary_type
    resolved["target_profiles"] = canonical_profiles
    resolved["custom_collect_tasks"] = _dedupe_preserve_order(collect_tasks)
    resolved["custom_analysis_tasks"] = _dedupe_preserve_order(analysis_tasks)

    resolved_path = Path(resolved_config_path)
    _write_yaml(resolved_path, resolved)

    return {
        "resolved_config": resolved,
        "resolved_config_path": str(resolved_path),
        "selected_profiles": canonical_profiles,
        "collect_task_count": len(resolved["custom_collect_tasks"]),
        "analysis_task_count": len(resolved["custom_analysis_tasks"]),
    }
