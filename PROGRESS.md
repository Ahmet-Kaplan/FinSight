# FinSight Hardening — Implementation Progress

Track each step. Mark `[x]` when complete, `[-]` when skipped with reason.

## Phase A — New Utility Modules
- [x] 1. `PROGRESS.md` — this file
- [x] 2. `src/utils/chart_utils.py` — font detection, filename sanitization (Part 12)
- [x] 3. `src/utils/language_utils.py` — language helpers (Part 11)
- [x] 4. `src/utils/tool_result_utils.py` — tool result safety helpers (Part 2)
- [x] 5. `src/utils/report_validator.py` — report language/image validation (Part 15)
- [x] 6. `src/utils/run_manifest.py` — run completion tracking (Part 23)

## Phase B — Core Runtime Fixes
- [x] 7. `src/utils/code_executor_async.py` — safe font defaults + figure cleanup (Parts 11, 13, 20)
- [x] 8. `src/utils/figure_helper.py` — safe font loading + language param (Parts 13, 18)
- [x] 9. `src/tools/financial/market.py` — consistent tool returns (Part 2)
- [x] 10. `src/tools/web/web_crawler.py` — per-URL errors + download handling + shared crawler (Parts 7, 19, 20)
- [x] 11. `src/tools/web/search_engine_playwright.py` — download guard + resource cleanup (Parts 19, 20)
- [x] 12. `src/agents/base_agent.py` — safe tool result handling (Part 2)
- [x] 13. `src/memory/variable_memory.py` — dedup with content fingerprint (Part 6)

## Phase C — Agent Behavior Fixes
- [x] 14. `src/agents/search_agent/search_agent.py` — click fix + iteration control (Parts 3, 4)
- [x] 15. `src/agents/data_collector/data_collector.py` — save_result keyword compat (Part 22)
- [x] 16. `src/agents/data_analyzer/data_analyzer.py` — language-aware chart + VLM + chart validation (Parts 11, 17, 18, 21)
- [x] 17. `src/agents/report_generator/report_generator.py` — validation + language kline (Parts 15, 18)

## Phase D — Prompt Improvements
- [x] 18. `src/agents/data_analyzer/prompts/financial_prompts.yaml` (Parts 11, 12, 14, 16)
- [x] 19. `src/agents/data_analyzer/prompts/general_prompts.yaml` (Parts 11, 12, 14, 16)
- [x] 20. `src/agents/data_collector/prompts/prompts.yaml` (Parts 4, 5, 22)
- [x] 21. `src/agents/search_agent/prompts/general_prompts.yaml` (Parts 5, 8)

## Phase E — Planner Agent
- [x] 22. `src/planner/` module — all 4 new files (Part 1)
- [x] 23. `run_report.py` — argparse CLI + planner integration + manifest (Parts 1, 23)
- [x] 24. `demo/backend/app.py` — web planner integration (Part 1)

## Phase F — Tests
- [x] 25. All `tests/test_*.py` new files (Part 24) — 53 tests, all passing

## Phase G — Documentation
- [x] 26. `README.md` — documentation (Part 10)
