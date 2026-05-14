# Improvements Log

Append-only log for non-bug improvements (reliability, observability, DX, quality).

Policy:
1. Newest entries go at the top.
2. Keep entries concise and implementation-focused.
3. Use IDs: `IMP-YYYYMMDD-XX`.

## Entry Template

- **ID**:
- **Date**:
- **Area**:
- **Improvement**:
- **Why**:
- **Change**:
- **Impact**:
- **Files**:
- **Reference (run_id/commit)**:

---

## Entries

### IMP-20260308-08
- **Date**: 2026-03-08
- **Area**: Master autonomy / health watchdog
- **Improvement**: Added continuous master health snapshots and autonomous recovery/escalation loop.
- **Why**: Long runs needed deterministic stall detection/recovery without manual `--doctor`/`--repair-resume` intervention.
- **Change**: Added master health tick loop (`--master-health-interval-sec`), deterministic risk scoring (`healthy|degraded|critical`), auto-recovery actions for stale/recoverable tasks, and escalation throttling with append-only events.
- **Impact**: Better unattended run resilience and explicit operator visibility into current health/recovery state.
- **Files**: `run_report.py`, `src/orchestration/master_coordinator.py`, `src/utils/recovery.py`
- **Reference (run_id/commit)**: master autonomy + health watchdog batch (2026-03-08)

### IMP-20260308-07
- **Date**: 2026-03-08
- **Area**: Master policy guardrails
- **Improvement**: Enforced Guided Auto mutation constraints and persisted policy state.
- **Why**: Master needed clear default safety boundaries to prevent silent destructive queue pruning.
- **Change**: `DROP_TASK` is disabled by default and allowed only under explicit opt-in plus confidence/status guards; policy is persisted and surfaced in status/master state.
- **Impact**: Safer autonomous steering with transparent mutation permissions.
- **Files**: `src/orchestration/master_coordinator.py`, `run_report.py`, `tests/test_master_coordinator.py`
- **Reference (run_id/commit)**: guided-auto policy hardening (2026-03-08)

### IMP-20260308-06
- **Date**: 2026-03-08
- **Area**: Status UX / end-stage observability
- **Improvement**: Strengthened live `--status` reconciliation and made end-stage detail lines more explicit.
- **Why**: During long report post-processing, snapshots could look completed/stale while active work continued, and operators needed clearer per-step visibility.
- **Change**: `print_status_snapshot` now uses `live_run_state` to override stale report completion and injects a synthetic running row when needed; progress detail line now prints an inline progress bar for sub-step counters.
- **Impact**: Better operator trust in status during long Phase2 operations and clearer “what it is doing now” visibility.
- **Files**: `run_report.py`, `src/utils/progress.py`
- **Reference (run_id/commit)**: run_id `90523065` status/progress clarity patch

### IMP-20260308-05
- **Date**: 2026-03-08
- **Area**: Runtime observability / progress UX
- **Improvement**: Added report end-stage detail task bar with sub-stage and batch progress.
- **Why**: Single-task report stage (`0/1`) hides real progress during long Phase2 reference/index work.
- **Change**: Extended `ProgressTracker` with per-stage detail lines; wired `run_report` to read live report-agent runtime markers; added index-build callback support for reference indexing progress and surfaced step details (outline/sections/post-process).
- **Impact**: Operators can see exactly what report generation is doing near the end (for example Step 3 reference indexing `x/y`) instead of appearing stalled.
- **Files**: `src/utils/progress.py`, `run_report.py`, `src/agents/report_generator/report_generator.py`, `src/utils/index_builder.py`
- **Reference (run_id/commit)**: run_id `90523065` end-stage observability patch

### IMP-20260308-04
- **Date**: 2026-03-08
- **Area**: Documentation / operations
- **Improvement**: Converted README into an explicit operator manual with stage details, model mapping, callable interfaces, and recovery runbook.
- **Why**: Long resumable runs need deterministic, copy/paste operational guidance to avoid state corruption and wasted retries.
- **Change**: Added detailed sections for pipeline, stage behavior, model/provider mapping, debugging matrix, and completion-truth verification.
- **Impact**: Faster triage, clearer ownership per stage, fewer ambiguous restart decisions.
- **Files**: `README.md`
- **Reference (run_id/commit)**: docs refresh 2026-03-08

### IMP-20260308-03
- **Date**: 2026-03-08
- **Area**: Recovery and resume reliability
- **Improvement**: Introduced/expanded doctor-repair workflow for stale mappings, missing checkpoints, and queue mismatch recovery.
- **Why**: Interrupted runs and agent recreation require deterministic state repair before continuation.
- **Change**: Added diagnostics (`--doctor`) and repair (`--repair-resume`) flows with recovery reporting and master-state checks.
- **Impact**: Higher probability of successful continuation on long jobs.
- **Files**: `run_report.py`, `src/utils/recovery.py`
- **Reference (run_id/commit)**: recovery hardening series

### IMP-20260308-02
- **Date**: 2026-03-08
- **Area**: Orchestration / adaptive control
- **Improvement**: Added master coordinator steering controls, mutation tracking, and queue snapshots.
- **Why**: Dynamic workload steering needs bounded, auditable mutation behavior.
- **Change**: Added master knobs (`--master-*`), mutation accounting, and queue/state persistence.
- **Impact**: Better adaptation under noisy search quality while retaining control over task growth.
- **Files**: `run_report.py`, `src/orchestration/master_coordinator.py`, `src/orchestration/master_types.py`
- **Reference (run_id/commit)**: master coordinator integration

### IMP-20260308-01
- **Date**: 2026-03-08
- **Area**: Output observability
- **Improvement**: Added run manifest and artifact-index-based run outcome tracking.
- **Why**: Operators need explicit success/failure and missing artifact diagnostics beyond live console logs.
- **Change**: Run manifest records stage statuses, artifacts, warnings, and required-output checks.
- **Impact**: Clear post-run truth source for automation and incident triage.
- **Files**: `src/utils/run_manifest.py`, `run_report.py`
- **Reference (run_id/commit)**: manifest integration
