from pydantic import BaseModel, Field
from typing import Literal, List

ProfileType = Literal[
    "company",
    "financial_company",
    "macro",
    "industry",
    "financial_industry",
    "general",
    "financial_macro",
    "governance",
]

class PlannerConfig(BaseModel):
    """Validated configuration produced by the planner LLM call.

    Every field has a sensible default so partial LLM output still
    produces a usable object.
    """

    target_name: str
    stock_code: str = ""
    target_type: ProfileType = "company"
    target_profiles: List[ProfileType] = Field(default_factory=list)
    market: Literal["A", "HK", "US", ""] = ""
    language: Literal["en", "zh"] = "en"
    output_dir: str = ""
    custom_collect_tasks: List[str] = Field(default_factory=list)
    custom_analysis_tasks: List[str] = Field(default_factory=list)
