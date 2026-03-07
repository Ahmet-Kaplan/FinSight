from pydantic import BaseModel, Field
from typing import Literal, List, Optional


class PlannerConfig(BaseModel):
    """Validated configuration produced by the planner LLM call.

    Every field has a sensible default so partial LLM output still
    produces a usable object.
    """

    target_name: str
    stock_code: str = ""
    target_type: Literal[
        "company",
        "financial_company",
        "macro",
        "industry",
        "financial_industry",
        "general",
    ] = "company"
    market: Literal["A", "HK", "US", ""] = ""
    language: Literal["en", "zh"] = "en"
    output_dir: str = ""
    custom_collect_tasks: List[str] = Field(default_factory=list)
    custom_analysis_tasks: List[str] = Field(default_factory=list)
