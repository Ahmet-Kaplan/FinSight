# Bug Log

Append-only log for defects identified in FinSight.

Policy:
1. Newest entries go at the top.
2. Keep entries concise.
3. For each bug, record: bug, cause, change, validation.
4. Use IDs: `BUG-YYYYMMDD-XX`.

## Entry Template

- **ID**:
- **Date**:
- **Status**: `fixed` | `known` | `monitoring`
- **Area**:
- **Bug**:
- **Cause**:
- **Change**:
- **Validation**:
- **Files**:
- **Reference (run_id/commit)**:

---

## Entries

### BUG-20260308-07
- **Date**: 2026-03-08
- **Status**: fixed
- **Area**: Agent code sandbox / filesystem mutation boundaries
- **Bug**: Agent-executed code could mutate filesystem paths outside intended run-scoped directories through `os.*` mutation calls, even when `open()` write checks existed.
- **Cause**: Path restrictions were enforced on `open()` writes but not consistently across destructive/mutating `os` operations (`remove`, `rename`, `replace`, `makedirs`, etc.).
- **Change**: Added centralized allowed-path guard and wrapped mutating `os` APIs in `AsyncCodeExecutor`; now mutations are restricted to state/memory/agent_working plus top-level report artifacts.
- **Validation**: `tests/test_sandbox.py` now includes blocked outside-scope `os.makedirs` and allowed in-scope mutation coverage; targeted suite passes.
- **Files**: `src/utils/code_executor_async.py`, `tests/test_sandbox.py`
- **Reference (run_id/commit)**: master autonomy + permission hardening batch (2026-03-08)

### BUG-20260308-06
- **Date**: 2026-03-08
- **Status**: fixed
- **Area**: Report post-processing (title/introduction grounding)
- **Bug**: Generated report title/introduction could be unrelated to the actual report topic (for example, climate/cultural framing in a semiconductor report).
- **Cause**: Prompt templates for `abstract`/`title_generation` did not reliably consume `{report_content}`/`{task}`; `.format(...)` accepted extra args silently, so the model received weak context.
- **Change**: Added grounded prompt builder in `ReportGenerator` that always injects report/task context when placeholders are absent; updated prompt packs (`general`, `financial_macro`, `governance`) to explicitly include `{report_content}` and `{task}`.
- **Validation**: `python -m py_compile src/agents/report_generator/report_generator.py run_report.py src/utils/progress.py`; prompt files now explicitly declare report/task inputs.
- **Files**: `src/agents/report_generator/report_generator.py`, `src/agents/report_generator/prompts/general_prompts.yaml`, `src/agents/report_generator/prompts/financial_macro_prompts.yaml`, `src/agents/report_generator/prompts/governance_prompts.yaml`
- **Reference (run_id/commit)**: run_id `90523065` output audit and prompt-grounding patch

### BUG-20260308-05
- **Date**: 2026-03-08
- **Status**: fixed
- **Area**: Status classification / run observability
- **Bug**: `--status` could mark report as done while report post-processing was still running, and show a stale run ID.
- **Cause**: Generic checkpoint-finished heuristic treated `return_dict` as completion for report checkpoints; run ID relied on log scraping that can lag active runs.
- **Change**: Added report-specific status classification using `phase/post_stage/finished`; added live run state marker (`state/live_run_state.json`) and prioritized it in `--status`; added report detail extraction in status output.
- **Validation**: Report checkpoints in `post_process` with `post_stage < 5` now classify as `running`, and status can display live stage/detail from the active run marker.
- **Files**: `run_report.py`, `src/agents/report_generator/report_generator.py`
- **Reference (run_id/commit)**: run_id `90523065` status/progress patch

### BUG-20260308-04
- **Date**: 2026-03-08
- **Status**: known
- **Area**: Status/observability
- **Bug**: `--status` can report `100%` while latest execution still failed to produce final report artifact.
- **Cause**: Status snapshot derives progress from task/checkpoint classification, not strict latest manifest success + required artifacts.
- **Change**: Documented as operational caveat in README; added completion-truth checks (manifest + artifacts) as required verification.
- **Validation**: Reproduced in live run sequence where status showed done while run manifests (`4d6b8fff`, `7210d405`, `09943ec5`) reported failure/no `.md`.
- **Files**: `run_report.py`, `src/utils/run_manifest.py`, `README.md`
- **Reference (run_id/commit)**: run_id `4d6b8fff`, `7210d405`, `09943ec5`

### BUG-20260308-03
- **Date**: 2026-03-08
- **Status**: fixed
- **Area**: Report generation prompts
- **Bug**: Report stage crashed with `KeyError: 'reference_data'` during section generation/final polish.
- **Cause**: Prompt templates required `{reference_data}` / `{reference_analysis}` placeholders but report generator `.format(...)` calls did not pass those keys.
- **Change**: Added robust reference summary/image builders and passed required placeholders in section drafting and final polish prompt formatting.
- **Validation**: Python compile check passed; failure mode shifted away from key-mismatch to upstream provider connection retries.
- **Files**: `src/agents/report_generator/report_generator.py`, `src/agents/report_generator/prompts/general_prompts.yaml`
- **Reference (run_id/commit)**: run_id `4d6b8fff` failure context

### BUG-20260308-02
- **Date**: 2026-03-08
- **Status**: fixed
- **Area**: Tool execution / async runtime
- **Bug**: Tool calls from generated code could deadlock or fail under nested event-loop conditions.
- **Cause**: Direct async execution patterns in executor context are unsafe inside already-running loops.
- **Change**: Base agent tool-call path uses async bridge pattern and defensive tool-result handling to avoid loop misuse and reduce malformed tool payload propagation.
- **Validation**: Runtime is able to execute repeated collector/analyzer tool calls without event-loop deadlock in resumed runs.
- **Files**: `src/agents/base_agent.py`, `src/utils/async_bridge.py`
- **Reference (run_id/commit)**: commit `dbbaaaa` (historical fix series)

### BUG-20260308-01
- **Date**: 2026-03-08
- **Status**: fixed
- **Area**: Data collector save interface
- **Bug**: Generated collector code used alternate `save_result(...)` signatures that could fail or skip data persistence.
- **Cause**: Function contract had positional assumptions while prompts/code used keyword variants.
- **Change**: Collector `save_result` now supports positional and keyword forms (`data`, `name`, `description`, `source`) and guards `None` inputs.
- **Validation**: Collector accepted both save call styles in resumed collection runs and persisted results into memory.
- **Files**: `src/agents/data_collector/data_collector.py`, `src/agents/data_collector/prompts/prompts.yaml`
- **Reference (run_id/commit)**: `PROGRESS.md` Phase C item 15
