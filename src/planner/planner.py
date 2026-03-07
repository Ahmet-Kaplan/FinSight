"""PlannerAgent -- turn a free-form user request into a validated YAML config."""

import os
import yaml
import json_repair
from openai import OpenAI
from pathlib import Path
from typing import Dict, Any

from src.planner.schema import PlannerConfig
from src.utils.prompt_loader import PromptLoader


class PlannerAgent:
    """Generate a FinSight pipeline config from a natural-language request."""

    def __init__(self):
        self.client = OpenAI(
            base_url=os.getenv("DS_BASE_URL"),
            api_key=os.getenv("DS_API_KEY"),
        )
        self.model = os.getenv("DS_MODEL_NAME", "deepseek-chat")

        # Load the prompt template via the project's PromptLoader
        prompts_dir = str(Path(__file__).parent / "prompts")
        self.prompt_loader = PromptLoader(prompts_dir)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def generate_config(self, user_request: str) -> PlannerConfig:
        """Call the LLM and return a validated PlannerConfig."""
        prompt = self.prompt_loader.get_prompt(
            "plan_config", user_request=user_request
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
        )

        raw = response.choices[0].message.content
        data = json_repair.loads(raw)
        config = PlannerConfig(**data)
        return config

    # ------------------------------------------------------------------
    # YAML helpers
    # ------------------------------------------------------------------

    def config_to_yaml_dict(self, config: PlannerConfig) -> dict:
        """Merge planner output with infrastructure defaults.

        The resulting dict mirrors the structure of ``my_config.yaml`` so it
        can be loaded directly by ``src.config.Config``.
        """
        # Determine template paths based on language and target type
        lang_suffix = "_zh" if config.language == "zh" else ""
        # Choose outline template based on target type
        if config.target_type in ("industry", "financial_industry"):
            outline_name = f"industry_outline{lang_suffix}.md"
        else:
            outline_name = f"company_outline{lang_suffix}.md"

        # If the language-specific template doesn't exist, fall back
        outline_path = f"src/template/{outline_name}"
        if not os.path.exists(outline_path):
            # Fall back to the non-suffixed version
            fallback = outline_name.replace(lang_suffix, "")
            if os.path.exists(f"src/template/{fallback}"):
                outline_path = f"src/template/{fallback}"

        output_dir = config.output_dir or f"./outputs/{config.target_name.lower().replace(' ', '-')}"

        yaml_dict: Dict[str, Any] = {
            "target_name": config.target_name,
            "stock_code": config.stock_code,
            "target_type": config.target_type,
            "market": config.market,
            "output_dir": output_dir,
            "language": config.language,
            "reference_doc_path": "src/template/report_template.docx",
            "outline_template_path": outline_path,
            # Task lists
            "custom_collect_tasks": config.custom_collect_tasks,
            "custom_analysis_tasks": config.custom_analysis_tasks,
            # Caches -- on by default so interrupted runs can resume
            "use_collect_data_cache": True,
            "use_analysis_cache": True,
            "use_report_outline_cache": True,
            "use_full_report_cache": True,
            "use_post_process_cache": True,
            # Rate limits
            "rate_limits": {
                "search_engines": 1.0,
                "financial_apis": 0.5,
                "fred_api": 0.5,
                "yfinance": 0.2,
            },
            # LLM configuration -- uses env-var placeholders resolved at load
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
        }
        return yaml_dict

    @staticmethod
    def write_yaml(yaml_dict: dict, path: str) -> None:
        """Safely write a dict to a YAML file (never writes raw LLM text)."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump(
                yaml_dict,
                fh,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

    # ------------------------------------------------------------------
    # End-to-end convenience
    # ------------------------------------------------------------------

    def plan(self, user_request: str, yaml_path: str = "my_config.yaml") -> dict:
        """Full pipeline: LLM -> validate -> merge defaults -> write YAML."""
        config = self.generate_config(user_request)
        yaml_dict = self.config_to_yaml_dict(config)
        self.write_yaml(yaml_dict, yaml_path)
        return yaml_dict
