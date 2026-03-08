import argparse
import os
import sys
import asyncio
import traceback
import copy
from collections import defaultdict
from typing import Dict, Any, List, Optional
import logging
import glob as _glob
import yaml
import pickle
import dill
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
load_dotenv()

# Avoid repeated Matplotlib cache rebuilds on environments where ~/.matplotlib is not writable.
# This must be set before importing modules that may import matplotlib.
if not os.getenv("MPLCONFIGDIR"):
    _mpl_cache_dir = os.path.join(os.getcwd(), ".runtime", "mplconfig")
    try:
        os.makedirs(_mpl_cache_dir, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = _mpl_cache_dir
    except Exception:
        pass

from src.config import Config
from src.config.profile_router import (
    normalize_profile_name,
    profile_config_filename,
    resolve_and_write_config,
)
from src.agents import DataCollector, DataAnalyzer, ReportGenerator
from src.memory import Memory
from src.utils import setup_logger
from src.utils import get_logger
from src.utils.recovery import (
    canonical_task_key,
    load_checkpoint_state as recovery_load_checkpoint_state,
    is_checkpoint_finished as recovery_is_checkpoint_finished,
    is_checkpoint_started as recovery_is_checkpoint_started,
    load_task_state,
    save_task_state,
    run_doctor,
    write_recovery_report,
    repair_task_mapping,
    repair_master_state,
    load_master_state,
    load_task_queue_snapshot,
    load_artifact_index,
    load_master_health,
    utc_now_iso,
)
from src.orchestration import MasterCoordinator, TaskCompletionEvent
get_logger().set_agent_context('runner', 'main')

IF_RESUME = True
MAX_CONCURRENT = 6
DEFAULT_AGENT_MAX_ITERATIONS = 10
DEFAULT_STALE_SECONDS = 900

PROFILE_EXECUTION_RANK = {
    "general": 0,
    "macro": 1,
    "financial_macro": 1,
    "industry": 2,
    "financial_industry": 2,
    "governance": 2,
    "company": 3,
    "financial_company": 3,
}

PROFILE_FLAG_TO_NAME = (
    ("company", "company"),
    ("financial_company", "financial_company"),
    ("macro", "macro"),
    ("industry", "industry"),
    ("financial_industry", "financial_industry"),
    ("general", "general"),
    ("financial_macro", "financial_macro"),
    ("governance", "governance"),
)


def parse_arguments(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(description="Run FinSight report generation pipeline")
    parser.add_argument('--resume', action='store_true', default=True)
    parser.add_argument('--continue', dest='resume', action='store_true', help='Alias for --resume')
    parser.add_argument('--no-resume', action='store_false', dest='resume')
    parser.add_argument('--max-concurrent', type=int, default=MAX_CONCURRENT)
    parser.add_argument('--max-tasks-per-profile', type=int, default=0, help='Optional cap per profile for collect/analyze tasks (0 = no cap)')
    parser.add_argument('--resume-stalled-only', action='store_true', help='Run only tasks detected as stalled by doctor checks')
    parser.add_argument('--resume-run-id', default='', help='Optional explicit run_id for doctor/repair checks')
    parser.add_argument('--stale-seconds', type=int, default=DEFAULT_STALE_SECONDS, help='Stale threshold in seconds for doctor/status')
    parser.add_argument(
        '--planner',
        action='store_true',
        help='Always run interactive planner and regenerate config before execution',
    )
    parser.add_argument(
        '--force-planner',
        action='store_true',
        help='Alias for --planner (always regenerate config)',
    )
    parser.add_argument(
        '--config',
        default='my_config.yaml',
        help='Base router config path (default: my_config.yaml)',
    )
    parser.add_argument(
        '--resolved-config',
        default='.runtime/my_config_resolved.yaml',
        help='Resolved runtime config output path (default: .runtime/my_config_resolved.yaml)',
    )
    parser.add_argument(
        '--resolved-input',
        action='store_true',
        help='Treat --config as an already-resolved runtime config and skip profile resolution',
    )
    parser.add_argument('--dry-run', action='store_true', help='Resolve and validate config only; do not run agents')
    parser.add_argument('--profile', action='append', default=[], help='Repeatable profile key, e.g. --profile company')
    parser.add_argument('--company', action='store_true', help='Include company profile')
    parser.add_argument('--financial-company', dest='financial_company', action='store_true', help='Include financial_company profile')
    parser.add_argument('--macro', action='store_true', help='Include macro profile')
    parser.add_argument('--industry', action='store_true', help='Include industry profile')
    parser.add_argument('--financial-industry', dest='financial_industry', action='store_true', help='Include financial_industry profile')
    parser.add_argument('--general', action='store_true', help='Include general profile')
    parser.add_argument('--financial-macro', dest='financial_macro', action='store_true', help='Include financial_macro profile')
    parser.add_argument('--governance', action='store_true', help='Include governance profile')
    # --- New CLI flags ---
    parser.add_argument(
        '--executor', choices=['local', 'render'],
        default='local',
        help='Execution backend (default: local)',
    )
    parser.add_argument('--verbose', action='store_true', help='Show DEBUG-level logs in terminal')
    parser.add_argument('--quiet', action='store_true', help='Show only ERROR-level logs')
    parser.add_argument(
        '--pdf-mode', choices=['auto', 'force', 'skip'],
        default=os.getenv('FINSIGHT_PDF_MODE', 'auto'),
        help='PDF generation policy (default: auto)',
    )
    parser.add_argument(
        '--purge-stale-images', action='store_true',
        help='Remove old chart images from working dir before running',
    )
    parser.add_argument(
        '--status',
        action='store_true',
        help='Print current run progress snapshot and ETA, then exit',
    )
    parser.add_argument(
        '--status-stall-advice-minutes',
        type=int,
        default=10,
        help='Show auto-restart advice in --status when oldest running checkpoint age exceeds this threshold (minutes)',
    )
    parser.add_argument(
        '--doctor',
        action='store_true',
        help='Diagnose resume health: stale mappings, missing checkpoints, duplicates, orphaned dirs',
    )
    parser.add_argument(
        '--repair-resume',
        action='store_true',
        help='Repair memory task mapping and checkpoint pointers, then exit',
    )
    parser.add_argument('--master-enabled', dest='master_enabled', action='store_true', default=True, help='Enable adaptive MasterCoordinator steering loop (default: enabled)')
    parser.add_argument('--no-master', dest='master_enabled', action='store_false', help='Disable MasterCoordinator steering loop')
    parser.add_argument('--master-batch-size', type=int, default=3, help='Master deep-review batch size trigger')
    parser.add_argument('--master-batch-max-age-sec', type=int, default=600, help='Master deep-review max age trigger (seconds)')
    parser.add_argument('--master-max-added-tasks-per-stage', type=int, default=8, help='Cap for tasks added by master per stage')
    parser.add_argument('--master-max-total-task-growth-pct', type=int, default=25, help='Cap for total task growth by master as percent of initial task count')
    parser.add_argument('--master-replan-cooldown-sec', type=int, default=120, help='Cooldown between deep replans unless failure spike')
    parser.add_argument('--master-strategy', choices=['quality', 'balanced', 'speed'], default='balanced', help='Master steering strategy profile')
    parser.add_argument('--master-health-interval-sec', type=int, default=30, help='Health watchdog polling interval in seconds')
    parser.add_argument('--master-auto-recover', dest='master_auto_recover', action='store_true', default=True, help='Enable master auto-recovery on stale/recoverable tasks (default: enabled)')
    parser.add_argument('--no-master-auto-recover', dest='master_auto_recover', action='store_false', help='Disable master auto-recovery actions')
    parser.add_argument('--master-allow-drop', dest='master_allow_drop', action='store_true', default=False, help='Allow DROP_TASK mutations (default: disabled)')
    parser.add_argument('--no-master-allow-drop', dest='master_allow_drop', action='store_false', help='Disallow DROP_TASK mutations')
    parser.add_argument('--master-stall-seconds', type=int, default=900, help='Stall threshold for autonomous recovery (seconds)')
    parser.add_argument('--master-escalation-cooldown-sec', type=int, default=180, help='Cooldown between repeated escalation events (seconds)')
    args = parser.parse_args(argv)
    args._executor_explicit = '--executor' in argv
    return args


def resolve_execution_backend(args) -> tuple[str, str | None]:
    """Resolve execution backend with local-first safety.

    Returns:
        (executor, warning_message)
    """
    env_executor = os.getenv('FINSIGHT_EXECUTOR', '').strip().lower()
    if not args._executor_explicit and env_executor and env_executor != 'local':
        warning = (
            f"Ignoring FINSIGHT_EXECUTOR={env_executor!r}; defaulting to local execution. "
            "Use --executor render to dispatch a remote run explicitly."
        )
        return 'local', warning
    return args.executor, None


def maybe_run_planner(force=False, config_path='my_config.yaml'):
    if os.path.exists(config_path) and not force:
        return None  # Config exists, skip planner
    from src.planner import PlannerAgent
    user_request = input("Enter your research topic: ").strip()
    if not user_request:
        print("No topic provided. Exiting.")
        sys.exit(1)
    planner = PlannerAgent()
    planner_config = planner.plan(user_request, yaml_path=config_path)
    print(f"Config generated at {config_path}")
    return planner_config


def should_force_planner(args) -> bool:
    """Return whether planner should always regenerate config."""
    return bool(args.planner or args.force_planner)


def collect_cli_profiles(args) -> list[str]:
    """Collect selected profile keys from named flags and --profile values."""
    selected: list[str] = []
    for arg_name, profile_name in PROFILE_FLAG_TO_NAME:
        if getattr(args, arg_name, False):
            selected.append(profile_name)
    for raw_profile in args.profile:
        selected.append(normalize_profile_name(raw_profile))
    # de-duplicate while preserving order
    deduped: list[str] = []
    seen = set()
    for profile in selected:
        canonical = normalize_profile_name(profile)
        if canonical in seen:
            continue
        seen.add(canonical)
        deduped.append(canonical)
    return deduped


def _purge_stale_images(working_dir: str) -> int:
    """Remove old chart PNG images from all agent image directories."""
    removed = 0
    for images_dir in _glob.glob(os.path.join(working_dir, 'agent_working', '*/images')):
        for f in os.listdir(images_dir):
            if f.lower().endswith('.png'):
                os.remove(os.path.join(images_dir, f))
                removed += 1
    return removed


def _cleanup_incomplete_documents(working_dir: str) -> dict:
    """Delete only clearly incomplete/buggy document files under *working_dir*.

    Safety policy:
    - Never touch cache/state/agent directories.
    - Remove Office lock files (~$*.docx), empty files, temp suffix files, and structurally invalid .docx/.pdf.
    - Keep all files that appear structurally valid.
    """
    import zipfile

    removed: list[dict] = []
    doc_exts = {".md", ".docx", ".pdf"}
    temp_suffixes = (
        ".tmp",
        ".part",
        ".partial",
        ".incomplete",
        ".crdownload",
    )
    excluded_dirs = {
        ".cache",
        ".executor_cache",
        "__pycache__",
        "agent_working",
        "logs",
        "memory",
        "state",
    }

    def _valid_docx(path: str) -> bool:
        try:
            with zipfile.ZipFile(path, "r") as zf:
                bad = zf.testzip()
                if bad is not None:
                    return False
                names = set(zf.namelist())
                return "[Content_Types].xml" in names and "word/document.xml" in names
        except Exception:
            return False

    def _valid_pdf(path: str) -> bool:
        try:
            with open(path, "rb") as fh:
                head = fh.read(8)
                if not head.startswith(b"%PDF-"):
                    return False
        except Exception:
            return False

        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(path)
            _ = len(reader.pages)
            return True
        except Exception:
            # Conservative fallback when parser fails: require EOF marker near tail.
            try:
                with open(path, "rb") as fh:
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    tail_window = min(4096, max(0, size))
                    fh.seek(-tail_window, os.SEEK_END)
                    tail = fh.read()
                return b"%%EOF" in tail
            except Exception:
                return False

    for root, dirs, files in os.walk(working_dir):
        dirs[:] = [d for d in dirs if d not in excluded_dirs]
        for name in files:
            lower_name = name.lower()
            full_path = os.path.join(root, name)
            rel_path = os.path.relpath(full_path, working_dir)
            ext = os.path.splitext(lower_name)[1]

            reason = None
            if lower_name.startswith("~$") and lower_name.endswith(".docx"):
                reason = "office_lock_file"
            elif lower_name.endswith(temp_suffixes) and (
                ".md" in lower_name or ".docx" in lower_name or ".pdf" in lower_name
            ):
                reason = "temp_partial_file"
            elif ext in doc_exts:
                try:
                    size = os.path.getsize(full_path)
                except OSError:
                    size = -1
                if size == 0:
                    reason = "empty_file"
                elif ext == ".docx" and not _valid_docx(full_path):
                    reason = "invalid_docx"
                elif ext == ".pdf" and not _valid_pdf(full_path):
                    reason = "invalid_pdf"

            if reason is None:
                continue

            try:
                os.remove(full_path)
                removed.append({"path": rel_path, "reason": reason})
            except OSError:
                continue

    return {
        "removed_count": len(removed),
        "removed": removed,
    }


def _task_key(task: str) -> str:
    return " ".join(str(task or "").strip().lower().split())


def _canonical_task_key_for(
    *,
    stage: str,
    profile: str,
    task_text: str,
    target_name: str,
    target_type: str,
) -> str:
    return canonical_task_key(
        stage=stage,
        profile=profile,
        task_text=task_text,
        target_name=target_name,
        target_type=target_type,
    )


def _dedupe_profile_tasks(
    *,
    stage: str,
    profile_tasks: dict,
    ordered_profiles: list[str],
    target_name: str,
    target_type: str,
    max_tasks_per_profile: int = 0,
) -> tuple[dict, int]:
    """Deduplicate tasks by canonical key while preserving profile order."""
    deduped = {profile: [] for profile in ordered_profiles}
    seen: set[str] = set()
    dropped = 0
    for profile in ordered_profiles:
        tasks = list(profile_tasks.get(profile, []))
        if max_tasks_per_profile and max_tasks_per_profile > 0:
            tasks = tasks[:max_tasks_per_profile]
        for task in tasks:
            ckey = _canonical_task_key_for(
                stage=stage,
                profile=profile,
                task_text=task,
                target_name=target_name,
                target_type=target_type,
            )
            if ckey in seen:
                dropped += 1
                continue
            seen.add(ckey)
            deduped[profile].append(task)
    return deduped, dropped


def _ordered_profiles(target_profiles: list[str], target_type: str) -> list[str]:
    profiles: list[str] = []
    for profile in target_profiles or []:
        profiles.append(normalize_profile_name(profile))
    if not profiles:
        profiles = [normalize_profile_name(target_type or "general")]
    deduped: list[str] = []
    seen = set()
    for profile in profiles:
        if profile in seen:
            continue
        seen.add(profile)
        deduped.append(profile)
    original_pos = {name: idx for idx, name in enumerate(deduped)}
    return sorted(
        deduped,
        key=lambda p: (PROFILE_EXECUTION_RANK.get(p, 99), original_pos.get(p, 999)),
    )


def _read_yaml_file(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return {}
    return data


def _load_profile_task_index(config_file_path: str, profiles: list[str]) -> dict:
    """Load per-profile collect/analyze task sets for routing and ordering."""
    index: dict = {}
    candidate_roots = [os.path.dirname(os.path.abspath(config_file_path)), os.getcwd()]
    for profile in profiles:
        filename = profile_config_filename(profile)
        config_data = {}
        for root in candidate_roots:
            profile_path = os.path.join(root, filename)
            config_data = _read_yaml_file(profile_path)
            if config_data:
                break
        index[profile] = {
            "collect": {_task_key(t) for t in config_data.get("custom_collect_tasks", []) or []},
            "analyze": {_task_key(t) for t in config_data.get("custom_analysis_tasks", []) or []},
        }
    return index


def _bucket_tasks_by_profile(
    tasks: list[str],
    stage: str,
    profiles: list[str],
    profile_task_index: dict,
    fallback_profile: str,
) -> tuple[dict, int]:
    bucketed = {profile: [] for profile in profiles}
    unmatched = 0
    for task in tasks:
        key = _task_key(task)
        assigned = None
        for profile in profiles:
            if key in profile_task_index.get(profile, {}).get(stage, set()):
                assigned = profile
                break
        if assigned is None:
            assigned = fallback_profile
            unmatched += 1
        bucketed.setdefault(assigned, []).append(task)
    return bucketed, unmatched


def _profile_overlap_note(profile: str, previous_profiles: list[str]) -> str:
    if not previous_profiles:
        return f"\nExecution profile for this task: {profile}."
    prev = ", ".join(previous_profiles)
    return (
        f"\nExecution profile for this task: {profile}. "
        f"Previously completed profile phases in this run: {prev}. "
        "Reuse prior findings first and avoid duplicate research unless needed to verify or update."
    )


def _gather_run_artifacts(working_dir: str, max_items: int = 200) -> list[dict]:
    """Collect non-cache artifact metadata under the current run output directory."""
    include_ext = {
        ".md", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".csv",
        ".tsv", ".xlsx", ".json", ".txt",
    }
    excluded_dirs = {".cache", ".executor_cache", "logs", "memory", "__pycache__"}
    artifacts: list[dict] = []
    for root, dirs, files in os.walk(working_dir):
        dirs[:] = [d for d in dirs if d not in excluded_dirs]
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in include_ext:
                continue
            abs_path = os.path.join(root, filename)
            try:
                size = os.path.getsize(abs_path)
            except OSError:
                size = -1
            artifacts.append(
                {
                    "path": os.path.relpath(abs_path, working_dir),
                    "size_bytes": size,
                }
            )
            if len(artifacts) >= max_items:
                return artifacts
    return artifacts


def _load_checkpoint_state(cache_dir: str, requested_checkpoint: str = "latest.pkl") -> tuple[dict | None, str | None]:
    """Load checkpoint state from *cache_dir* with fallback to first available pkl."""
    return recovery_load_checkpoint_state(cache_dir, requested_checkpoint=requested_checkpoint)


def _is_finished_checkpoint_state(state: dict | None) -> bool:
    return recovery_is_checkpoint_finished(state)


def _is_started_checkpoint_state(state: dict | None) -> bool:
    return recovery_is_checkpoint_started(state)


def _build_expected_stage_task_keys(config: Config, memory: Memory, config_file_path: str) -> tuple[list[dict], list[dict]]:
    collect_tasks = list(config.config.get("custom_collect_tasks", []) or [])
    analysis_tasks = list(config.config.get("custom_analysis_tasks", []) or [])
    generated_collect_tasks = list(memory.generated_collect_tasks or [])
    generated_analysis_tasks = list(memory.generated_analysis_tasks or [])

    all_collect_tasks = collect_tasks + [task for task in generated_collect_tasks if task not in collect_tasks]
    all_analysis_tasks = analysis_tasks + [task for task in generated_analysis_tasks if task not in analysis_tasks]

    target_type = config.config.get("target_type", "general")
    target_profiles = config.config.get("target_profiles", [])
    ordered_profiles = _ordered_profiles(target_profiles, target_type)
    primary_profile = normalize_profile_name(target_type) if target_type else ordered_profiles[0]
    if primary_profile not in ordered_profiles:
        primary_profile = ordered_profiles[0]
    profile_task_index = _load_profile_task_index(config_file_path, ordered_profiles)
    collect_by_profile, _ = _bucket_tasks_by_profile(
        all_collect_tasks, "collect", ordered_profiles, profile_task_index, primary_profile
    )
    analyze_by_profile, _ = _bucket_tasks_by_profile(
        all_analysis_tasks, "analyze", ordered_profiles, profile_task_index, primary_profile
    )

    target_name = config.config.get("target_name", "")
    stock_code = config.config.get("stock_code", "")
    target_type = config.config.get("target_type", "general")

    collect_keys: list[dict] = []
    for idx, profile in enumerate(ordered_profiles):
        previous_profiles = ordered_profiles[:idx]
        for task in collect_by_profile.get(profile, []):
            scope_note = _profile_overlap_note(profile, previous_profiles)
            task_text = (
                f"Research target: {target_name} (ticker: {stock_code}), task: {task}{scope_note}"
            )
            collect_keys.append(
                {
                    "task_key": task_text,
                    "raw_task_text": task,
                    "profile": profile,
                    "canonical_task_key": _canonical_task_key_for(
                        stage="collect",
                        profile=profile,
                        task_text=task,
                        target_name=target_name,
                        target_type=target_type,
                    ),
                }
            )

    analyze_keys: list[dict] = []
    for idx, profile in enumerate(ordered_profiles):
        previous_profiles = ordered_profiles[:idx]
        for task in analyze_by_profile.get(profile, []):
            scope_note = _profile_overlap_note(profile, previous_profiles)
            analysis_task = f"{task}\n{scope_note}"
            analyze_keys.append(
                {
                    "task_key": analysis_task,
                    "raw_task_text": task,
                    "profile": profile,
                    "canonical_task_key": _canonical_task_key_for(
                        stage="analyze",
                        profile=profile,
                        task_text=task,
                        target_name=target_name,
                        target_type=target_type,
                    ),
                }
            )

    return collect_keys, analyze_keys


def _latest_progress_run_id(log_dir: str) -> str:
    """Best-effort extraction of latest run id from finsight.log."""
    log_path = os.path.join(log_dir, "finsight.log")
    if not os.path.exists(log_path):
        return "unknown"
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return "unknown"
    # Search backward for the tracker line.
    for line in reversed(lines[-4000:]):
        marker = "| run "
        if marker in line:
            return line.split(marker, 1)[1].strip().split()[0]
    return "unknown"


def _live_run_state_path(working_dir: str) -> str:
    return os.path.join(working_dir, "state", "live_run_state.json")


def _write_live_run_state(
    *,
    working_dir: str,
    run_id: str,
    status: str,
    stage: str = "",
    detail: str = "",
    health_status: str = "",
    stall_risk_score: int | None = None,
    active_recovery_action: str = "",
) -> None:
    path = _live_run_state_path(working_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "run_id": str(run_id or "").strip() or "unknown",
        "status": str(status or "unknown"),
        "stage": str(stage or "").strip(),
        "detail": str(detail or "").strip(),
        "health_status": str(health_status or "").strip(),
        "stall_risk_score": int(stall_risk_score) if stall_risk_score is not None else None,
        "active_recovery_action": str(active_recovery_action or "").strip(),
        "updated_at": utc_now_iso(),
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            import json as _json
            _json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_live_run_state(working_dir: str) -> dict:
    path = _live_run_state_path(working_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            import json as _json
            data = _json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def _fmt_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _parse_iso(ts: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(ts))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return datetime.now(timezone.utc)


def print_status_snapshot(
    config_file_path: str,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    status_stall_advice_minutes: int = 10,
) -> int:
    """Print percentage completion and ETA from on-disk task/checkpoint state."""
    from src.utils.progress import STAGE_WEIGHTS
    from src.utils.run_history import RunHistory

    config = Config(config_file_path=config_file_path, config_dict={})
    memory = Memory(config=config)
    if not memory.load():
        print("No memory checkpoint found. Progress is 0%.")
        return 1

    collect_items, analyze_items = _build_expected_stage_task_keys(config, memory, config_file_path)
    task_mapping = list(memory.task_mapping or [])

    latest_mapping: dict[tuple[str, str], dict] = {}
    latest_mapping_by_canonical: dict[tuple[str, str], dict] = {}
    latest_report_mapping: dict | None = None
    for item in reversed(task_mapping):
        cls_name = item.get("agent_class_name")
        key = item.get("task_key", "")
        if cls_name == "report_generator" and latest_report_mapping is None:
            latest_report_mapping = item
        pair = (cls_name, key)
        if pair not in latest_mapping:
            latest_mapping[pair] = item
        canonical_key = str(item.get("canonical_task_key", "") or "").strip()
        if cls_name and canonical_key:
            cpair = (cls_name, canonical_key)
            if cpair not in latest_mapping_by_canonical:
                latest_mapping_by_canonical[cpair] = item

    def _task_title(raw: str, limit: int = 180) -> str:
        text = " ".join(str(raw or "").split())
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def classify_meta(agent_id: str | None, *, stage: str = "", agent_class_name: str = "") -> dict:
        out = {
            "status": "pending",
            "checkpoint_name": None,
            "checkpoint_mtime": None,
            "current_round": None,
            "finished": False,
            "phase": None,
            "post_stage": None,
            "section_index": None,
            "detail": "",
        }
        if not agent_id:
            return out
        cache_dir = os.path.join(config.working_dir, "agent_working", agent_id, ".cache")
        requested_checkpoint = "latest.pkl"
        is_report = str(stage) == "report" or str(agent_class_name) == "report_generator"
        if is_report:
            requested_checkpoint = "report_latest.pkl"
        state, checkpoint_name = _load_checkpoint_state(cache_dir, requested_checkpoint)
        if is_report and (state is None or checkpoint_name is None):
            state, checkpoint_name = _load_checkpoint_state(cache_dir, "latest.pkl")
        checkpoint_mtime = None
        if checkpoint_name:
            checkpoint_path = os.path.join(cache_dir, checkpoint_name)
            if os.path.exists(checkpoint_path):
                try:
                    checkpoint_mtime = float(os.path.getmtime(checkpoint_path))
                except Exception:
                    checkpoint_mtime = None
        if checkpoint_mtime is None:
            latest_path = os.path.join(cache_dir, "latest.pkl")
            if os.path.exists(latest_path):
                try:
                    checkpoint_mtime = float(os.path.getmtime(latest_path))
                except Exception:
                    checkpoint_mtime = None

        phase = None
        post_stage = None
        section_index = None
        detail = ""
        if isinstance(state, dict):
            phase = state.get("phase")
            post_stage = state.get("post_stage")
            section_index = state.get("section_index")

        if is_report:
            finished = False
            if isinstance(state, dict):
                finished = bool(state.get("finished", False))
                if not finished:
                    try:
                        finished = int(post_stage or 0) >= 5
                    except Exception:
                        finished = False
            if finished:
                status = "done"
            elif isinstance(state, dict) and (phase in {"outline", "sections", "post_process"} or _is_started_checkpoint_state(state)):
                status = "running"
            else:
                status = "pending"

            runtime_detail = ""
            runtime_current = None
            runtime_total = None
            if isinstance(state, dict):
                runtime_detail = str(state.get("runtime_progress_detail", "") or "").strip()
                runtime_current = state.get("runtime_progress_current")
                runtime_total = state.get("runtime_progress_total")
            if runtime_detail:
                detail = runtime_detail
                try:
                    rc = int(runtime_current) if runtime_current is not None else None
                    rt = int(runtime_total) if runtime_total is not None else None
                    if rc is not None and rt is not None and rt > 0:
                        detail += f" ({rc}/{rt})"
                except Exception:
                    pass
            else:
                step_map = {
                    0: "Step 0: replace image paths",
                    1: "Step 1: add abstract and title",
                    2: "Step 2: add cover/basic data page",
                    3: "Step 3: add references",
                    4: "Step 4: render markdown/docx/pdf",
                    5: "Completed",
                }
                if str(phase) == "outline":
                    detail = "Phase0: generating outline"
                elif str(phase) == "sections":
                    try:
                        detail = f"Phase1: generating sections (section_index={int(section_index or 0)})"
                    except Exception:
                        detail = "Phase1: generating sections"
                elif str(phase) == "post_process":
                    try:
                        post_stage_int = int(post_stage or 0)
                    except Exception:
                        post_stage_int = 0
                    detail = f"Phase2: {step_map.get(post_stage_int, 'post processing')}"
        else:
            if _is_finished_checkpoint_state(state):
                status = "done"
            elif _is_started_checkpoint_state(state):
                status = "running"
            else:
                status = "pending"

        current_round = None
        if isinstance(state, dict):
            try:
                current_round = int(state.get("current_round", 0))
            except Exception:
                current_round = None

        out.update(
            {
                "status": status,
                "checkpoint_name": checkpoint_name,
                "checkpoint_mtime": checkpoint_mtime,
                "current_round": current_round,
                "finished": bool(isinstance(state, dict) and state.get("finished", False)),
                "phase": phase,
                "post_stage": post_stage,
                "section_index": section_index,
                "detail": detail,
            }
        )
        return out

    report_canonical_key = _canonical_task_key_for(
        stage="report",
        profile="report",
        task_text="report_generator",
        target_name=config.config.get("target_name", ""),
        target_type=config.config.get("target_type", "general"),
    )
    if latest_report_mapping is None:
        latest_report_mapping = latest_mapping_by_canonical.get(("report_generator", report_canonical_key))

    collect_done = collect_running = 0
    collect_rows = []
    for item in collect_items:
        key = str(item.get("task_key", ""))
        canonical_key = str(item.get("canonical_task_key", ""))
        info = latest_mapping_by_canonical.get(("data_collector", canonical_key)) if canonical_key else None
        if info is None:
            info = latest_mapping.get(("data_collector", key))
        agent_id = info.get("agent_id") if info else None
        meta = classify_meta(agent_id, stage="collect", agent_class_name="data_collector")
        status = meta["status"]
        collect_rows.append(
            {
                "stage": "collect",
                "task": str(item.get("raw_task_text") or key),
                "agent_id": agent_id,
                "status": status,
                "checkpoint_mtime": meta["checkpoint_mtime"],
                "current_round": meta["current_round"],
            }
        )
        if status == "done":
            collect_done += 1
        elif status == "running":
            collect_running += 1

    analyze_done = analyze_running = 0
    analyze_rows = []
    for item in analyze_items:
        key = str(item.get("task_key", ""))
        canonical_key = str(item.get("canonical_task_key", ""))
        info = latest_mapping_by_canonical.get(("data_analyzer", canonical_key)) if canonical_key else None
        if info is None:
            info = latest_mapping.get(("data_analyzer", key))
        agent_id = info.get("agent_id") if info else None
        meta = classify_meta(agent_id, stage="analyze", agent_class_name="data_analyzer")
        status = meta["status"]
        analyze_rows.append(
            {
                "stage": "analyze",
                "task": str(item.get("raw_task_text") or key),
                "agent_id": agent_id,
                "status": status,
                "checkpoint_mtime": meta["checkpoint_mtime"],
                "current_round": meta["current_round"],
            }
        )
        if status == "done":
            analyze_done += 1
        elif status == "running":
            analyze_running += 1

    report_total = 1
    report_agent_id = latest_report_mapping.get("agent_id") if latest_report_mapping else None
    report_meta = classify_meta(report_agent_id, stage="report", agent_class_name="report_generator")
    report_status = report_meta["status"]
    report_done = 1 if report_status == "done" else 0
    report_running = 1 if report_status == "running" else 0

    total_collect = len(collect_items)
    total_analyze = len(analyze_items)
    total_all = total_collect + total_analyze + report_total
    done_all = 0
    running_all = 0
    pct = 0.0

    def _recompute_rollup() -> None:
        nonlocal done_all, running_all, pct
        done_all = collect_done + analyze_done + report_done
        running_all = collect_running + analyze_running + report_running

        collect_ratio = (collect_done / total_collect) if total_collect else 1.0
        analyze_ratio = (analyze_done / total_analyze) if total_analyze else 1.0
        report_ratio = (report_done / report_total) if report_total else 1.0
        weighted_done = (
            STAGE_WEIGHTS.get("collect", 0.0) * collect_ratio
            + STAGE_WEIGHTS.get("analyze", 0.0) * analyze_ratio
            + STAGE_WEIGHTS.get("report", 0.0) * report_ratio
        )
        pct = round(weighted_done / max(sum(STAGE_WEIGHTS.values()), 1e-9) * 100, 1)

    _recompute_rollup()

    history = RunHistory()
    target_type = config.config.get("target_type", "general")
    est_total_sec = history.estimate(target_type=target_type, task_count=total_all)
    est_remaining = None
    eta_ts = None
    eta_confidence = "unknown"
    if est_total_sec is not None:
        est_remaining = est_total_sec * max(0.0, 1.0 - pct / 100.0)
        eta_ts = datetime.now() + timedelta(seconds=est_remaining)
        eta_confidence = "medium"
    else:
        # Velocity fallback from task_state.json when history is unavailable
        task_state = load_task_state(config.working_dir)
        durations = []
        if isinstance(task_state, dict):
            for item in task_state.values():
                if not isinstance(item, dict):
                    continue
                started = item.get("started_at")
                completed = item.get("completed_at")
                if not started or not completed:
                    continue
                try:
                    t0 = datetime.fromisoformat(started)
                    t1 = datetime.fromisoformat(completed)
                    dt = (t1 - t0).total_seconds()
                    if dt > 0:
                        durations.append(dt)
                except Exception:
                    continue
        if len(durations) >= 3:
            avg_task_sec = sum(durations) / len(durations)
            est_total_sec = avg_task_sec * max(total_all, 1)
            est_remaining = avg_task_sec * max(total_all - done_all, 0)
            eta_ts = datetime.now() + timedelta(seconds=est_remaining)
            eta_confidence = "low"

    live_run_state = _load_live_run_state(config.working_dir)
    run_id = str(live_run_state.get("run_id", "") or "").strip()
    live_status = str(live_run_state.get("status", "") or "").strip().lower()
    live_stage = str(live_run_state.get("stage", "") or "").strip().lower()
    live_detail = str(live_run_state.get("detail", "") or "").strip()
    if not run_id:
        run_id = _latest_progress_run_id(os.path.join(config.working_dir, "logs"))

    # If a live run marker exists, prefer it over potentially stale checkpoint
    # classification so status snapshots reflect active work.
    if live_status == "running":
        if live_stage == "report":
            report_status = "running"
            report_done = 0
            report_running = 1
            if live_detail:
                report_meta["detail"] = live_detail
        elif live_stage == "collect" and collect_done < total_collect:
            collect_running = max(collect_running, 1)
        elif live_stage == "analyze" and analyze_done < total_analyze:
            analyze_running = max(analyze_running, 1)
        _recompute_rollup()

    doctor = run_doctor(
        working_dir=config.working_dir,
        task_mapping=list(memory.task_mapping or []),
        task_attempts=dict(memory.task_attempts or {}),
        stale_seconds=stale_seconds,
        run_id=run_id,
    )
    doctor_dict = doctor.as_dict()
    print(f"Run ID: {run_id}")
    if live_status == "running":
        live_stage_display = live_stage or "unknown"
        if live_detail:
            print(f"Live Run: running | stage={live_stage_display} | detail={live_detail}")
        else:
            print(f"Live Run: running | stage={live_stage_display}")
    print(
        f"Collect: {collect_done}/{total_collect} done | "
        f"{collect_running} running | {max(total_collect - collect_done - collect_running, 0)} pending"
    )
    print(
        f"Analyze: {analyze_done}/{total_analyze} done | "
        f"{analyze_running} running | {max(total_analyze - analyze_done - analyze_running, 0)} pending"
    )
    print(
        f"Report: {report_done}/{report_total} done | "
        f"{report_running} running | {max(report_total - report_done - report_running, 0)} pending"
    )
    report_detail = str(report_meta.get("detail", "") or "").strip()
    if report_status == "running" and report_detail:
        print(f"Report Detail: {report_detail}")
    print(
        f"Overall: {pct:.1f}% ({done_all}/{total_all} tasks done, {running_all} running)"
    )
    print(
        f"Recovery Health: stalled={doctor_dict['stale_tasks']} | "
        f"recoverable={doctor_dict['recoverable_tasks']} | "
        f"orphaned={doctor_dict['orphaned_mappings']} | "
        f"recreated={doctor_dict['recreated_tasks']}"
    )
    master_state = load_master_state(config.working_dir)
    queue_snapshot = load_task_queue_snapshot(config.working_dir)
    pending_queue_size = 0
    if isinstance(queue_snapshot, dict):
        pending_queue_size = int(queue_snapshot.get("pending_queue_size", 0) or 0)
    master_cycles = int(master_state.get("master_cycles", 0) or 0) if isinstance(master_state, dict) else 0
    last_gate_at = str(master_state.get("last_gate_review_at", "")) if isinstance(master_state, dict) else ""
    last_conf = float(master_state.get("last_decision_confidence", 0.0) or 0.0) if isinstance(master_state, dict) else 0.0
    mutations = master_state.get("mutations_applied", {}) if isinstance(master_state, dict) else {}
    artifact_index = load_artifact_index(config.working_dir)
    total_artifacts = len(artifact_index) if isinstance(artifact_index, dict) else 0
    high_tier = 0
    if isinstance(artifact_index, dict):
        for row in artifact_index.values():
            if isinstance(row, dict) and row.get("source_tier") in {"official", "regulator", "company_ir", "industry_assoc"}:
                high_tier += 1
    high_tier_pct = (high_tier / max(total_artifacts, 1) * 100.0) if total_artifacts > 0 else 0.0
    print(
        "Master: "
        f"cycles={master_cycles} | "
        f"last_gate_at={last_gate_at or 'n/a'} | "
        f"pending_queue_size={pending_queue_size} | "
        f"last_decision_confidence={last_conf:.2f} | "
        f"artifact_coverage_pct={(100.0 if total_artifacts > 0 else 0.0):.1f} | "
        f"high_tier_source_pct={high_tier_pct:.1f} | "
        f"mutations={mutations}"
    )
    master_health = load_master_health(config.working_dir)
    if isinstance(master_health, dict) and master_health:
        print(
            "Master Health: "
            f"status={str(master_health.get('health_status', 'unknown'))} | "
            f"stall_risk_score={int(master_health.get('stall_risk_score', 0) or 0)} | "
            f"oldest_running_checkpoint_age={_fmt_seconds(master_health.get('oldest_running_checkpoint_age_sec'))} | "
            f"time_since_last_completion={_fmt_seconds(master_health.get('time_since_last_completion_sec'))} | "
            f"active_recovery_action={str(master_health.get('active_recovery_action', '') or 'none')}"
        )
    all_rows = collect_rows + analyze_rows
    if report_agent_id:
        all_rows.append(
            {
                "stage": "report",
                "task": "Report generation",
                "agent_id": report_agent_id,
                "status": report_status,
                "checkpoint_mtime": report_meta.get("checkpoint_mtime"),
                "current_round": report_meta.get("current_round"),
                "detail": report_meta.get("detail", ""),
            }
        )
    if live_status == "running" and live_stage in {"collect", "analyze", "report"}:
        has_running_row = any(
            str(row.get("stage", "")) == live_stage and str(row.get("status", "")) == "running"
            for row in all_rows
        )
        if not has_running_row:
            all_rows.append(
                {
                    "stage": live_stage,
                    "task": f"Live runtime activity ({live_stage})",
                    "agent_id": str(live_run_state.get("agent_id", "") or "live-run"),
                    "status": "running",
                    "checkpoint_mtime": None,
                    "current_round": None,
                    "detail": live_detail,
                }
            )

    now_ts = datetime.now().timestamp()
    running_rows = [row for row in all_rows if row.get("status") == "running"]
    pending_rows = [row for row in all_rows if row.get("status") == "pending"]
    done_rows = [row for row in all_rows if row.get("status") == "done" and row.get("checkpoint_mtime") is not None]
    done_rows = sorted(done_rows, key=lambda x: float(x.get("checkpoint_mtime") or 0.0), reverse=True)

    # Current tasks (running)
    print("Current task(s):")
    if running_rows:
        stage_rank = {"collect": 1, "analyze": 2, "report": 3}
        running_rows = sorted(
            running_rows,
            key=lambda x: (
                stage_rank.get(str(x.get("stage", "")), 99),
                -float(x.get("checkpoint_mtime") or 0.0),
            ),
        )
        for idx, row in enumerate(running_rows[:5], 1):
            age_min = None
            if row.get("checkpoint_mtime") is not None:
                age_min = max(0.0, (now_ts - float(row["checkpoint_mtime"])) / 60.0)
            age_text = f"{age_min:.1f}m" if age_min is not None else "n/a"
            round_text = row.get("current_round")
            round_text = str(round_text) if round_text is not None else "?"
            print(
                f"  {idx}. [{row['stage']}] {row.get('agent_id', 'n/a')} | "
                f"round={round_text} | last_checkpoint_age={age_text}"
            )
            print(f"     {_task_title(str(row.get('task', '')))}")
            detail_text = str(row.get("detail", "") or "").strip()
            if detail_text:
                print(f"     detail={detail_text}")
    else:
        print("  none")

    # Previous task (most recently completed)
    print("Previous task:")
    if done_rows:
        prev = done_rows[0]
        completed_at = datetime.fromtimestamp(float(prev["checkpoint_mtime"])).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  [{prev['stage']}] {_task_title(str(prev.get('task', '')))}")
        print(f"  completed_at={completed_at} | agent={prev.get('agent_id', 'n/a')}")
    else:
        print("  none")

    # Next task (first pending by stage order)
    print("Next task:")
    if pending_rows:
        stage_rank = {"collect": 1, "analyze": 2, "report": 3}
        pending_rows = sorted(pending_rows, key=lambda x: stage_rank.get(str(x.get("stage", "")), 99))
        nxt = pending_rows[0]
        print(f"  [{nxt['stage']}] {_task_title(str(nxt.get('task', '')))}")
        print(f"  agent={nxt.get('agent_id', 'n/a')}")
        next_detail = str(nxt.get("detail", "") or "").strip()
        if next_detail:
            print(f"  detail={next_detail}")
    else:
        print("  none")

    # Freshness + stall risk
    time_since_last_done_sec = None
    if done_rows:
        time_since_last_done_sec = max(0.0, now_ts - float(done_rows[0]["checkpoint_mtime"]))
    oldest_running_age_min = 0.0
    if running_rows:
        ages = []
        for row in running_rows:
            if row.get("checkpoint_mtime") is None:
                continue
            ages.append(max(0.0, (now_ts - float(row["checkpoint_mtime"])) / 60.0))
        if ages:
            oldest_running_age_min = max(ages)
    stall_threshold_min = max(1, int(status_stall_advice_minutes))
    stall_risk_score = min(
        100.0,
        doctor_dict.get("stale_tasks", 0) * 10.0
        + max(0.0, oldest_running_age_min - float(stall_threshold_min)) * 3.0,
    )
    if stall_risk_score >= 70:
        stall_risk_level = "high"
    elif stall_risk_score >= 40:
        stall_risk_level = "medium"
    else:
        stall_risk_level = "low"
    print(
        "Watchdog: "
        f"oldest_running_checkpoint_age={oldest_running_age_min:.1f}m | "
        f"time_since_last_completion={_fmt_seconds(time_since_last_done_sec)} | "
        f"stall_risk={stall_risk_level} ({stall_risk_score:.0f}/100)"
    )
    if running_rows and oldest_running_age_min >= float(stall_threshold_min):
        print(
            "Auto-restart advice: "
            f"oldest running checkpoint age exceeded {stall_threshold_min}m."
        )
        print(
            f"  1) python run_report.py --resolved-input --config {config_file_path} --repair-resume"
        )
        print(
            f"  2) python run_report.py --resolved-input --config {config_file_path} --continue --resume-stalled-only --max-concurrent 2"
        )
    print(f"Estimated total runtime: {_fmt_seconds(est_total_sec)}")
    print(f"Estimated remaining: {_fmt_seconds(est_remaining)}")
    print(f"ETA confidence: {eta_confidence}")
    if eta_ts is not None:
        print(f"Estimated completion time: {eta_ts.strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        print("Estimated completion time: unknown")
    return 0


def _print_doctor_summary(summary: dict):
    print(f"Run ID: {summary.get('run_id', 'unknown')}")
    print(
        "Doctor: "
        f"duplicate_active_tasks={summary.get('duplicate_active_tasks', 0)} | "
        f"missing_checkpoints={summary.get('missing_checkpoints', 0)} | "
        f"stale_tasks={summary.get('stale_tasks', 0)} | "
        f"orphaned_mappings={summary.get('orphaned_mappings', 0)} | "
        f"orphaned_agent_dirs={summary.get('orphaned_agent_dirs', 0)} | "
        f"recreated_tasks={summary.get('recreated_tasks', 0)} | "
        f"recoverable_tasks={summary.get('recoverable_tasks', 0)}"
    )
    details = summary.get("details", {}) if isinstance(summary, dict) else {}
    if isinstance(details, dict):
        missing = details.get("master_missing_files", [])
        queue_mismatch = details.get("master_queue_mismatch_count", 0)
        stale_gate = details.get("master_stale_gate_clock", False)
        invalid_rows = details.get("master_invalid_artifact_rows", 0)
        print(
            "Master Doctor: "
            f"missing_files={missing} | "
            f"queue_mismatch_count={queue_mismatch} | "
            f"stale_gate_clock={stale_gate} | "
            f"invalid_artifact_rows={invalid_rows}"
        )


async def run_report(
    resume: bool = True,
    max_concurrent: int = None,
    max_tasks_per_profile: int = 0,
    resume_stalled_only: bool = False,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    master_enabled: bool = True,
    master_batch_size: int = 3,
    master_batch_max_age_sec: int = 600,
    master_max_added_tasks_per_stage: int = 8,
    master_max_total_task_growth_pct: int = 25,
    master_replan_cooldown_sec: int = 120,
    master_strategy: str = "balanced",
    master_health_interval_sec: int = 30,
    master_auto_recover: bool = True,
    master_allow_drop: bool = False,
    master_stall_seconds: int = 900,
    master_escalation_cooldown_sec: int = 180,
    verbose: bool = False,
    quiet: bool = False,
    pdf_mode: str = 'auto',
    purge_stale_images: bool = False,
    config_file_path: str = 'my_config.yaml',
    executor_name: str = 'local',
):
    """
    Run report generation with optional concurrency limit.
    """
    use_llm_name = os.getenv("DS_MODEL_NAME")
    use_vlm_name = os.getenv("VLM_MODEL_NAME")
    use_embedding_name = os.getenv("EMBEDDING_MODEL_NAME")

    # Get max concurrent from parameter, env var, or default to unlimited
    if max_concurrent is None:
        max_concurrent = int(os.getenv("MAX_CONCURRENT", "0")) or None
    master_health_interval_sec = max(5, int(master_health_interval_sec))
    master_stall_seconds = max(60, int(master_stall_seconds))
    master_escalation_cooldown_sec = max(30, int(master_escalation_cooldown_sec))
    config = Config(
        config_file_path=config_file_path,
        config_dict={'pdf_mode': pdf_mode}
    )

    # Initialize logger with split levels
    log_dir = os.path.join(config.working_dir, 'logs')
    logger = setup_logger(log_dir=log_dir, log_level=logging.INFO,
                          verbose=verbose, quiet=quiet)

    collect_tasks = config.config['custom_collect_tasks']
    analysis_tasks = config.config['custom_analysis_tasks']

    # Initialize memory
    memory = Memory(config=config)

    # --- Manifest + progress + history ---
    from src.utils.run_manifest import RunManifest
    from src.utils.progress import ProgressTracker
    from src.utils.run_history import RunHistory

    target_type = config.config.get('target_type', 'general')
    target_profiles = config.config.get('target_profiles', [])
    target_name = config.config.get('target_name', '')
    language = config.config.get('language', 'en')

    manifest = RunManifest(
        output_dir=config.config.get('output_dir', './outputs'),
        target_name=target_name,
        executor=executor_name,
        config_snapshot={
            'target_name': target_name,
            'target_type': target_type,
            'target_profiles': target_profiles,
            'language': language,
        },
    )

    history = RunHistory()

    # Log concurrency settings
    if max_concurrent:
        logger.info(f"Concurrency limit: {max_concurrent} tasks")
    else:
        logger.info("No concurrency limit (unlimited)")

    if resume:
        memory.load()
        logger.info("Memory state loaded")

    # State files for manual recovery operations.
    task_state = load_task_state(config.working_dir)
    if not isinstance(task_state, dict):
        task_state = {}

    # Purge stale images if requested
    if purge_stale_images:
        n = _purge_stale_images(config.working_dir)
        logger.info(f"Purged {n} stale chart image(s)")

    # On resume/continue runs, remove only clearly broken/incomplete document outputs.
    if resume:
        cleanup_stats = _cleanup_incomplete_documents(config.working_dir)
        removed_count = int(cleanup_stats.get("removed_count", 0) or 0)
        if removed_count > 0:
            samples = cleanup_stats.get("removed", [])[:5]
            sample_text = ", ".join(
                f"{item.get('path')} ({item.get('reason')})"
                for item in samples
                if isinstance(item, dict)
            )
            logger.info(
                f"Removed {removed_count} incomplete/buggy document file(s) before resume."
                + (f" Sample: {sample_text}" if sample_text else "")
            )

    # Generate additional collect and analysis tasks using LLM if not already generated
    research_query = f"Research target: {config.config['target_name']} (ticker: {config.config['stock_code']}), target type: {config.config.get('target_type', 'company')}"

    # Generate collect tasks if not already generated (or if we want fresh tasks)
    if not memory.generated_collect_tasks:
        logger.info("Generating collect tasks using LLM...")
        generated_collect_tasks = await memory.generate_collect_tasks(
            query=research_query,
            use_llm_name=use_llm_name,
            max_num=5,
            existing_tasks=collect_tasks  # Pass existing tasks to avoid duplication
        )
        logger.info(f"Generated {len(generated_collect_tasks)} collect tasks")
    else:
        generated_collect_tasks = memory.generated_collect_tasks
        logger.info(f"Using {len(generated_collect_tasks)} previously generated collect tasks")

    # Generate analysis tasks if not already generated
    if not memory.generated_analysis_tasks:
        logger.info("Generating analysis tasks using LLM...")
        generated_analysis_tasks = await memory.generate_analyze_tasks(
            query=research_query,
            use_llm_name=use_llm_name,
            max_num=5,
            existing_tasks=analysis_tasks  # Pass existing tasks to avoid duplication
        )
        logger.info(f"Generated {len(generated_analysis_tasks)} analysis tasks")
    else:
        generated_analysis_tasks = memory.generated_analysis_tasks
        logger.info(f"Using {len(generated_analysis_tasks)} previously generated analysis tasks")

    # Merge custom tasks with generated tasks (remove duplicates)
    all_collect_tasks = list(collect_tasks) + [task for task in generated_collect_tasks if task not in collect_tasks]
    all_analysis_tasks = list(analysis_tasks) + [task for task in generated_analysis_tasks if task not in analysis_tasks]

    logger.info(f"Total collect tasks: {len(all_collect_tasks)} (custom: {len(collect_tasks)}, generated: {len(generated_collect_tasks)})")
    logger.info(f"Total analysis tasks: {len(all_analysis_tasks)} (custom: {len(analysis_tasks)}, generated: {len(generated_analysis_tasks)})")

    # Update the tasks to be used
    collect_tasks = all_collect_tasks
    analysis_tasks = all_analysis_tasks

    ordered_profiles = _ordered_profiles(target_profiles, target_type)
    primary_profile = normalize_profile_name(target_type) if target_type else ordered_profiles[0]
    if primary_profile not in ordered_profiles:
        primary_profile = ordered_profiles[0]
    profile_task_index = _load_profile_task_index(config_file_path, ordered_profiles)
    collect_by_profile, collect_unmatched = _bucket_tasks_by_profile(
        collect_tasks,
        "collect",
        ordered_profiles,
        profile_task_index,
        primary_profile,
    )
    analyze_by_profile, analyze_unmatched = _bucket_tasks_by_profile(
        analysis_tasks,
        "analyze",
        ordered_profiles,
        profile_task_index,
        primary_profile,
    )
    collect_by_profile, collect_dropped = _dedupe_profile_tasks(
        stage="collect",
        profile_tasks=collect_by_profile,
        ordered_profiles=ordered_profiles,
        target_name=target_name,
        target_type=target_type,
        max_tasks_per_profile=max_tasks_per_profile,
    )
    analyze_by_profile, analyze_dropped = _dedupe_profile_tasks(
        stage="analyze",
        profile_tasks=analyze_by_profile,
        ordered_profiles=ordered_profiles,
        target_name=target_name,
        target_type=target_type,
        max_tasks_per_profile=max_tasks_per_profile,
    )
    collect_tasks = [task for profile in ordered_profiles for task in collect_by_profile.get(profile, [])]
    analysis_tasks = [task for profile in ordered_profiles for task in analyze_by_profile.get(profile, [])]
    logger.info(
        "Execution profile order (broad -> narrow): "
        + " -> ".join(ordered_profiles)
    )
    if collect_unmatched or analyze_unmatched:
        logger.info(
            f"Unmatched tasks routed to primary profile '{primary_profile}': "
            f"collect={collect_unmatched} analyze={analyze_unmatched}"
        )
    for profile in ordered_profiles:
        logger.info(
            f"Profile '{profile}': collect={len(collect_by_profile.get(profile, []))} "
            f"analyze={len(analyze_by_profile.get(profile, []))}"
        )
    if collect_dropped or analyze_dropped:
        logger.info(
            f"Canonical dedupe dropped tasks: collect={collect_dropped} analyze={analyze_dropped}"
        )

    TERMINAL_TASK_STATUSES = {"done", "dropped", "skipped"}

    def _is_terminal_task_status(status_value: Any) -> bool:
        return str(status_value or "").strip().lower() in TERMINAL_TASK_STATUSES

    # Pre-compute expected canonical tasks for the current config so resume can be deterministic.
    expected_task_catalog: List[Dict[str, str]] = []
    for idx, profile in enumerate(ordered_profiles):
        for task in collect_by_profile.get(profile, []):
            expected_task_catalog.append(
                {
                    "stage": "collect",
                    "profile": profile,
                    "raw_task_text": task,
                    "canonical_task_key": _canonical_task_key_for(
                        stage="collect",
                        profile=profile,
                        task_text=task,
                        target_name=target_name,
                        target_type=target_type,
                    ),
                }
            )
    for idx, profile in enumerate(ordered_profiles):
        for task in analyze_by_profile.get(profile, []):
            expected_task_catalog.append(
                {
                    "stage": "analyze",
                    "profile": profile,
                    "raw_task_text": task,
                    "canonical_task_key": _canonical_task_key_for(
                        stage="analyze",
                        profile=profile,
                        task_text=task,
                        target_name=target_name,
                        target_type=target_type,
                    ),
                }
            )
    report_canonical_key = _canonical_task_key_for(
        stage="report",
        profile="report",
        task_text="report_generator",
        target_name=target_name,
        target_type=target_type,
    )
    expected_task_catalog.append(
        {
            "stage": "report",
            "profile": "report",
            "raw_task_text": "report_generator",
            "canonical_task_key": report_canonical_key,
        }
    )

    if resume and not resume_stalled_only:
        stage_order = ["collect", "analyze", "report"]
        stage_remaining: Dict[str, int] = {s: 0 for s in stage_order}
        next_task_preview = None
        for stage_name in stage_order:
            for item in expected_task_catalog:
                if item["stage"] != stage_name:
                    continue
                status = task_state.get(item["canonical_task_key"], {}).get("status", "pending")
                if _is_terminal_task_status(status):
                    continue
                stage_remaining[stage_name] += 1
                if next_task_preview is None:
                    next_task_preview = item
        total_remaining = sum(stage_remaining.values())
        current_stage = "complete"
        for stage_name in stage_order:
            if stage_remaining[stage_name] > 0:
                current_stage = stage_name
                break
        logger.info(
            "Resume context: "
            f"big_task='{target_name}' | current_stage={current_stage} | "
            f"remaining collect={stage_remaining['collect']} analyze={stage_remaining['analyze']} report={stage_remaining['report']}"
        )
        if next_task_preview is not None:
            logger.info(
                "Resume next task: "
                f"[{next_task_preview['stage']}] {next_task_preview['raw_task_text'][:180]}"
            )
        if total_remaining == 0:
            logger.info("All tasks are complete for this config. Continue requested, but nothing remains to run.")
            save_task_state(config.working_dir, task_state)
            return

    def _touch_task_state(canonical_key: str, **fields):
        entry = task_state.get(canonical_key, {})
        if not isinstance(entry, dict):
            entry = {}
        entry.update(fields)
        entry["last_update"] = utc_now_iso()
        task_state[canonical_key] = entry
        save_task_state(config.working_dir, task_state)

    def _sync_live_progress():
        completed = (
            tracker._completed.get("collect", 0)
            + tracker._completed.get("analyze", 0)
            + tracker._completed.get("report", 0)
        )
        elapsed = max(0.0, datetime.now().timestamp() - live_start_ts)
        history.update_live_run(manifest.run_id, completed, elapsed)
        master_status = master.status_snapshot()
        tracker.update_master_metrics(
            master_cycles=master_status.get("master_cycles", 0),
            pending_queue_size=master_status.get("pending_queue_size", 0),
            last_decision_confidence=master_status.get("last_decision_confidence", 0.0),
            mutations_applied=master_status.get("mutations_applied", {}),
        )

    stale_only_keys: set[str] = set()
    stale_only_pairs: set[tuple[str, str]] = set()
    if resume_stalled_only:
        doctor_for_stalled = run_doctor(
            working_dir=config.working_dir,
            task_mapping=list(memory.task_mapping or []),
            task_attempts=dict(memory.task_attempts or {}),
            stale_seconds=stale_seconds,
            run_id=manifest.run_id,
        ).as_dict()
        stale_only_keys = set(doctor_for_stalled.get("details", {}).get("stale_task_keys", []))
        for row in doctor_for_stalled.get("details", {}).get("stale_task_pairs", []):
            if not isinstance(row, dict):
                continue
            cls = str(row.get("agent_class_name", "")).strip()
            task_key = str(row.get("task_key", "")).strip()
            if cls and task_key:
                stale_only_pairs.add((cls, task_key))
        logger.info(
            f"resume_stalled_only enabled: stalled canonical tasks={len(stale_only_keys)} "
            f"stalled task-pairs={len(stale_only_pairs)}"
        )

    # Prepare prioritized task list (lower value = higher priority)
    tasks_to_run = []

    # Data-collection tasks (ordered by profile breadth)
    for idx, profile in enumerate(ordered_profiles):
        previous_profiles = ordered_profiles[:idx]
        for task in collect_by_profile.get(profile, []):
            canonical_key = _canonical_task_key_for(
                stage="collect",
                profile=profile,
                task_text=task,
                target_name=target_name,
                target_type=target_type,
            )
            scope_note = _profile_overlap_note(profile, previous_profiles)
            task_text = (
                f'Research target: {config.config["target_name"]} '
                f'(ticker: {config.config["stock_code"]}), task: {task}{scope_note}'
            )
            if resume_stalled_only:
                pair = ("data_collector", task_text)
                if canonical_key not in stale_only_keys and pair not in stale_only_pairs:
                    continue
            if resume and (not resume_stalled_only):
                if _is_terminal_task_status(task_state.get(canonical_key, {}).get("status", "pending")):
                    continue
            _touch_task_state(
                canonical_key,
                canonical_task_key=canonical_key,
                stage="collect",
                profile=profile,
                raw_task_text=task,
                status=task_state.get(canonical_key, {}).get("status", "pending"),
                recoverable=True,
            )
            tasks_to_run.append({
                'agent_class': DataCollector,
                'task_input': {
                    'input_data': {
                        'task': task_text,
                        'stage_name': 'collect',
                        'profile_name': profile,
                        'previous_profiles': previous_profiles,
                        'raw_task_text': task,
                        'canonical_task_key': canonical_key,
                    },
                    'echo': True,
                    'max_iterations': DEFAULT_AGENT_MAX_ITERATIONS,
                    'resume': resume,
                },
                'agent_kwargs': {
                    'use_llm_name': use_llm_name,
                },
                'priority': 1,
                'profile_name': profile,
                'canonical_task_key': canonical_key,
                'raw_task_text': task,
            })

    # Analysis tasks (run after collection, same profile order)
    for idx, profile in enumerate(ordered_profiles):
        previous_profiles = ordered_profiles[:idx]
        for task in analyze_by_profile.get(profile, []):
            canonical_key = _canonical_task_key_for(
                stage="analyze",
                profile=profile,
                task_text=task,
                target_name=target_name,
                target_type=target_type,
            )
            scope_note = _profile_overlap_note(profile, previous_profiles)
            analysis_task = f"{task}\n{scope_note}"
            if resume_stalled_only:
                pair = ("data_analyzer", analysis_task)
                if canonical_key not in stale_only_keys and pair not in stale_only_pairs:
                    continue
            if resume and (not resume_stalled_only):
                if _is_terminal_task_status(task_state.get(canonical_key, {}).get("status", "pending")):
                    continue
            _touch_task_state(
                canonical_key,
                canonical_task_key=canonical_key,
                stage="analyze",
                profile=profile,
                raw_task_text=task,
                status=task_state.get(canonical_key, {}).get("status", "pending"),
                recoverable=True,
            )
            tasks_to_run.append({
                'agent_class': DataAnalyzer,
                'task_input': {
                    'input_data': {
                        'task': f'Research target: {config.config["target_name"]} (ticker: {config.config["stock_code"]})',
                        'analysis_task': analysis_task,
                        'stage_name': 'analyze',
                        'profile_name': profile,
                        'previous_profiles': previous_profiles,
                        'raw_task_text': task,
                        'canonical_task_key': canonical_key,
                    },
                    'echo': True,
                    'max_iterations': DEFAULT_AGENT_MAX_ITERATIONS,
                    'resume': resume,
                },
                'agent_kwargs': {
                    'use_llm_name': use_llm_name,
                    'use_vlm_name': use_vlm_name,
                    'use_embedding_name': use_embedding_name,
                },
                'priority': 2,
                'profile_name': profile,
                'canonical_task_key': canonical_key,
                'raw_task_text': task,
            })

    # Report generation task
    run_artifacts_snapshot = _gather_run_artifacts(config.working_dir)
    report_allowed_by_resume = True
    if resume and (not resume_stalled_only):
        report_allowed_by_resume = not _is_terminal_task_status(task_state.get(report_canonical_key, {}).get("status", "pending"))
    if report_allowed_by_resume and ((not resume_stalled_only) or (report_canonical_key in stale_only_keys)):
        _touch_task_state(
            report_canonical_key,
            canonical_task_key=report_canonical_key,
            stage="report",
            profile="report",
            raw_task_text="report_generator",
            status=task_state.get(report_canonical_key, {}).get("status", "pending"),
            recoverable=True,
        )
        tasks_to_run.append({
            'agent_class': ReportGenerator,
            'task_input': {
                'input_data': {
                    'task': f'Research target: {config.config["target_name"]} (ticker: {config.config["stock_code"]})',
                    'task_type': target_type,
                    'target_profiles': ordered_profiles,
                    'run_artifacts': run_artifacts_snapshot,
                    'stage_name': 'report',
                    'profile_name': 'report',
                    'raw_task_text': 'report_generator',
                    'canonical_task_key': report_canonical_key,
                },
                'echo': True,
                'max_iterations': DEFAULT_AGENT_MAX_ITERATIONS,
                'resume': resume,
            },
            'agent_kwargs': {
                'use_llm_name': use_llm_name,
                'use_embedding_name': use_embedding_name,
            },
            'priority': 3,
            'profile_name': 'report',
            'canonical_task_key': report_canonical_key,
            'raw_task_text': 'report_generator',
        })

    if resume_stalled_only:
        logger.info(f"Tasks selected after stalled-only filter: {len(tasks_to_run)}")
        if not tasks_to_run:
            logger.info("No stalled tasks found; nothing to resume.")
            save_task_state(config.working_dir, task_state)
            return

    task_blueprints_by_key: Dict[str, Dict[str, Any]] = {}
    for item in tasks_to_run:
        canonical_key = str(item.get("canonical_task_key", "")).strip()
        if not canonical_key:
            continue
        task_blueprints_by_key[canonical_key] = dict(item)

    selected_stage_totals = {"collect": 0, "analyze": 0, "report": 0}
    for item in tasks_to_run:
        stage_name = str(item.get("task_input", {}).get("input_data", {}).get("stage_name", "collect"))
        if stage_name in selected_stage_totals:
            selected_stage_totals[stage_name] += 1
    total_task_count = sum(selected_stage_totals.values())
    if total_task_count == 0:
        logger.info("No tasks selected for execution; nothing to run.")
        save_task_state(config.working_dir, task_state)
        return

    # --- Estimate runtime and set up progress tracker ---
    estimated_sec = history.estimate(target_type, total_task_count)
    manifest.estimated_total_sec = estimated_sec
    manifest.config_snapshot['task_count'] = total_task_count

    tracker = ProgressTracker(
        run_id=manifest.run_id,
        stages=['collect', 'analyze', 'report'],
        total_tasks=selected_stage_totals,
        estimated_sec=estimated_sec,
        executor=executor_name,
        target_name=target_name,
    )
    tracker.start_periodic(interval=30)
    history.start_live_run(manifest.run_id, target_type, total_task_count)
    live_start_ts = datetime.now().timestamp()
    _write_live_run_state(
        working_dir=config.working_dir,
        run_id=manifest.run_id,
        status="running",
        stage="init",
        detail="initializing pipeline",
    )

    effective_master_enabled = bool(master_enabled and not resume_stalled_only)
    if resume_stalled_only and master_enabled:
        logger.info("MasterCoordinator is disabled for resume_stalled_only to avoid queue mutations during targeted recovery.")
    master = MasterCoordinator(
        working_dir=config.working_dir,
        enabled=effective_master_enabled,
        batch_size=master_batch_size,
        batch_max_age_sec=master_batch_max_age_sec,
        max_added_tasks_per_stage=master_max_added_tasks_per_stage,
        max_total_task_growth_pct=master_max_total_task_growth_pct,
        replan_cooldown_sec=master_replan_cooldown_sec,
        strategy=master_strategy,
        allow_drop=master_allow_drop,
    )
    master.bootstrap(total_task_count)


    pending_queue: List[Dict[str, Any]] = []
    for idx, task_info in enumerate(tasks_to_run):
        entry = dict(task_info)
        entry["order_index"] = idx
        pending_queue.append(entry)

    agent_name_to_class = {
        DataCollector.AGENT_NAME: DataCollector,
        DataAnalyzer.AGENT_NAME: DataAnalyzer,
        ReportGenerator.AGENT_NAME: ReportGenerator,
    }

    def _hydrate_queue_item(item: Dict[str, Any]) -> Dict[str, Any]:
        stage_name = item.get("task_input", {}).get("input_data", {}).get("stage_name", "collect")
        if "agent_class" not in item:
            class_name = item.get("agent_class_name")
            if class_name in agent_name_to_class:
                item["agent_class"] = agent_name_to_class[class_name]
            elif stage_name == "analyze":
                item["agent_class"] = DataAnalyzer
            elif stage_name == "report":
                item["agent_class"] = ReportGenerator
            else:
                item["agent_class"] = DataCollector
        if "agent_kwargs" not in item or not isinstance(item.get("agent_kwargs"), dict):
            if stage_name == "analyze":
                item["agent_kwargs"] = {
                    "use_llm_name": use_llm_name,
                    "use_vlm_name": use_vlm_name,
                    "use_embedding_name": use_embedding_name,
                }
            elif stage_name == "report":
                item["agent_kwargs"] = {
                    "use_llm_name": use_llm_name,
                    "use_embedding_name": use_embedding_name,
                }
            else:
                item["agent_kwargs"] = {
                    "use_llm_name": use_llm_name,
                }
        item.setdefault("task_input", {})
        item["task_input"].setdefault("echo", True)
        item["task_input"].setdefault("max_iterations", DEFAULT_AGENT_MAX_ITERATIONS)
        item["task_input"].setdefault("resume", resume)
        return item

    if resume and effective_master_enabled:
        snapshot = master.load_queue_snapshot()
        pending_from_snapshot = snapshot.get("pending_queue", []) if isinstance(snapshot, dict) else []
        if isinstance(pending_from_snapshot, list) and pending_from_snapshot:
            rebuilt: List[Dict[str, Any]] = []
            for idx, raw in enumerate(pending_from_snapshot):
                if not isinstance(raw, dict):
                    continue
                status = str(task_state.get(str(raw.get("canonical_task_key")), {}).get("status", ""))
                if status in {"done", "dropped", "skipped"}:
                    continue
                rebuilt.append(
                    _hydrate_queue_item(
                        {
                            "canonical_task_key": raw.get("canonical_task_key"),
                            "priority": raw.get("priority", 99),
                            "profile_name": raw.get("profile_name", ""),
                            "raw_task_text": raw.get("raw_task_text", ""),
                            "task_input": raw.get("task_input", {}),
                            "agent_kwargs": raw.get("agent_kwargs", {}),
                            "agent_class_name": raw.get("agent_class_name", ""),
                            "order_index": raw.get("order_index", idx),
                        }
                    )
                )
                if rebuilt[-1].get("task_input", {}).get("input_data", {}).get("stage_name") in {None, ""}:
                    rebuilt[-1].setdefault("task_input", {}).setdefault("input_data", {})
                    rebuilt[-1]["task_input"]["input_data"]["stage_name"] = raw.get("stage_name", "collect")
            if rebuilt:
                pending_queue = rebuilt
                logger.info(f"Loaded pending queue from snapshot: {len(pending_queue)} item(s)")

    pending_queue = [_hydrate_queue_item(x) for x in pending_queue]

    def _queue_sort_key(item: Dict[str, Any]):
        stage_name = item.get("task_input", {}).get("input_data", {}).get("stage_name", "collect")
        stage_rank = {"collect": 1, "analyze": 2, "report": 3}.get(stage_name, 99)
        return (stage_rank, int(item.get("priority", 99)), int(item.get("order_index", 10**9)))

    pending_queue = sorted(pending_queue, key=_queue_sort_key)

    def _manifest_stage(stage_name: str) -> str:
        return {"collect": "collect", "analyze": "analyze", "report": "report_assemble"}.get(stage_name, stage_name)

    def _stage_progress(stage_name: str) -> tuple[int, int]:
        total = 0
        done = 0
        for _, value in task_state.items():
            if not isinstance(value, dict):
                continue
            if str(value.get("stage", "")) != stage_name:
                continue
            if str(value.get("status", "")) == "dropped":
                continue
            total += 1
            if str(value.get("status", "")) == "done":
                done += 1
        return done, total

    def _can_dispatch(item: Dict[str, Any]) -> bool:
        stage_name = item.get("task_input", {}).get("input_data", {}).get("stage_name", "collect")
        if stage_name != "report":
            return True
        collect_done, collect_total = _stage_progress("collect")
        analyze_done, analyze_total = _stage_progress("analyze")
        collect_ok = collect_total == 0 or collect_done >= collect_total
        analyze_ratio = 1.0 if analyze_total == 0 else (analyze_done / max(analyze_total, 1))
        if collect_ok and analyze_ratio >= 0.80:
            return True
        override_conf = float(item.get("task_input", {}).get("input_data", {}).get("report_override_confidence", 0.0) or 0.0)
        return override_conf >= 0.85

    stage_started: set[str] = set()
    stage_finished: set[str] = set()
    running_set: Dict[str, Dict[str, Any]] = {}
    completed_buffer: List[TaskCompletionEvent] = []
    last_gate_review_at = _parse_iso(str(master.state.get("last_gate_review_at", utc_now_iso())))
    last_replan_at = last_gate_review_at
    cooldown_sec = max(0, int(master_replan_cooldown_sec))
    max_parallel = int(max_concurrent or max(1, len(pending_queue)))

    def _update_master_tracker():
        snapshot = master.status_snapshot()
        tracker.update_master_metrics(
            master_cycles=snapshot.get("master_cycles", 0),
            pending_queue_size=len(pending_queue),
            last_decision_confidence=snapshot.get("last_decision_confidence", 0.0),
            mutations_applied=snapshot.get("mutations_applied", {}),
        )

    last_live_state_signature = {"value": ""}
    health_runtime = {
        "health_status": "unknown",
        "stall_risk_score": 0,
        "active_recovery_action": "",
    }

    def _refresh_live_run_state(detail: str = "") -> None:
        stage_name = str(tracker._current_stage or "unknown")
        signature = (
            f"{manifest.run_id}|running|{stage_name}|{detail}|"
            f"{health_runtime.get('health_status','')}|"
            f"{health_runtime.get('stall_risk_score', 0)}|"
            f"{health_runtime.get('active_recovery_action','')}"
        )
        if signature == last_live_state_signature["value"]:
            return
        last_live_state_signature["value"] = signature
        _write_live_run_state(
            working_dir=config.working_dir,
            run_id=manifest.run_id,
            status="running",
            stage=stage_name,
            detail=detail,
            health_status=str(health_runtime.get("health_status", "unknown")),
            stall_risk_score=int(health_runtime.get("stall_risk_score", 0) or 0),
            active_recovery_action=str(health_runtime.get("active_recovery_action", "") or ""),
        )

    def _extract_report_runtime_detail(agent: Any) -> tuple[str, Optional[int], Optional[int]]:
        detail = str(getattr(agent, "_runtime_progress_detail", "") or "").strip()
        current = getattr(agent, "_runtime_progress_current", None)
        total = getattr(agent, "_runtime_progress_total", None)
        try:
            current_int = int(current) if current is not None else None
        except Exception:
            current_int = None
        try:
            total_int = int(total) if total is not None else None
        except Exception:
            total_int = None
        if detail:
            return detail, current_int, total_int

        phase = str(getattr(agent, "_phase", "") or "").strip().lower()
        section_done = getattr(agent, "_section_index_done", None)
        post_stage = getattr(agent, "_post_stage", None)

        if phase == "outline":
            return "[Phase0] generating outline", None, None
        if phase == "sections":
            report_obj = getattr(agent, "current_checkpoint", {}) or {}
            report = None
            if isinstance(report_obj, dict):
                report = report_obj.get("report_obj")
            total_sections = None
            if report is not None and hasattr(report, "sections"):
                try:
                    total_sections = int(len(report.sections))
                except Exception:
                    total_sections = None
            try:
                done_count = int(section_done) if section_done is not None else 0
            except Exception:
                done_count = 0
            return "[Phase1] generating sections", done_count, total_sections
        if phase == "post_process":
            step_map = {
                0: "[Phase2] Step 0: replace image paths",
                1: "[Phase2] Step 1: add abstract and title",
                2: "[Phase2] Step 2: add cover/basic data page",
                3: "[Phase2] Step 3: add references",
                4: "[Phase2] Step 4: render markdown/docx/pdf",
                5: "[Phase2] completed",
            }
            try:
                post_stage_int = int(post_stage) if post_stage is not None else 0
            except Exception:
                post_stage_int = 0
            return step_map.get(post_stage_int, "[Phase2] post processing"), None, None
        return "", None, None

    def _refresh_tracker_report_detail() -> None:
        for info in running_set.values():
            if str(info.get("stage", "")) != "report":
                continue
            detail, cur, total = _extract_report_runtime_detail(info.get("agent"))
            if detail:
                tracker.set_stage_detail("report", detail, cur, total, emit=True)
                _refresh_live_run_state(detail)
            else:
                tracker.clear_stage_detail("report")
                _refresh_live_run_state("")
            return
        tracker.clear_stage_detail("report")
        _refresh_live_run_state("")

    last_completion_ts = datetime.now(timezone.utc)
    last_health_tick_ts = 0.0
    last_escalation_ts = 0.0

    def _checkpoint_touch_age_sec(agent_id: str) -> float | None:
        cache_dir = os.path.join(config.working_dir, "agent_working", agent_id, ".cache")
        state, checkpoint_name = _load_checkpoint_state(cache_dir, "latest.pkl")
        _ = state
        candidates = []
        if checkpoint_name:
            candidates.append(os.path.join(cache_dir, checkpoint_name))
        candidates.append(os.path.join(cache_dir, "latest.pkl"))
        latest_touch = None
        for path in candidates:
            if not os.path.exists(path):
                continue
            try:
                mtime = float(os.path.getmtime(path))
                latest_touch = max(latest_touch, mtime) if latest_touch is not None else mtime
            except Exception:
                continue
        if latest_touch is None:
            return None
        return max(0.0, datetime.now().timestamp() - latest_touch)

    def _oldest_running_checkpoint_age_sec() -> float:
        ages = []
        for info in running_set.values():
            agent = info.get("agent")
            agent_id = str(getattr(agent, "id", "") or "")
            if not agent_id:
                continue
            age = _checkpoint_touch_age_sec(agent_id)
            if age is not None:
                ages.append(age)
        if not ages:
            return 0.0
        return max(ages)

    def _queue_contains(canonical_key: str) -> bool:
        return any(str(item.get("canonical_task_key", "")) == canonical_key for item in pending_queue)

    def _build_retry_item(base_item: Dict[str, Any], reason: str) -> Dict[str, Any]:
        retry_item = _hydrate_queue_item(copy.deepcopy(base_item))
        retry_item["priority"] = min(int(retry_item.get("priority", 2) or 2), 1)
        retry_item["order_index"] = int(min([int(x.get("order_index", 0) or 0) for x in pending_queue] + [0])) - 1
        retry_item.setdefault("task_input", {}).setdefault("input_data", {})
        retry_item["task_input"]["resume"] = True
        retry_hint = f"Auto-recovery: {reason}. Tighten source quality and avoid repeated failed parameters."
        existing_guidance = str(retry_item["task_input"]["input_data"].get("master_guidance", "") or "").strip()
        merged = retry_hint if not existing_guidance else f"{existing_guidance}\n{retry_hint}"
        retry_item["task_input"]["input_data"]["master_guidance"] = merged
        return retry_item

    def _auto_recover_from_doctor(doctor_dict: Dict[str, Any]) -> None:
        nonlocal last_escalation_ts
        if not (effective_master_enabled and master_auto_recover):
            return
        now_epoch = datetime.now().timestamp()
        if now_epoch - last_escalation_ts < float(master_escalation_cooldown_sec):
            return

        details = doctor_dict.get("details", {}) if isinstance(doctor_dict, dict) else {}
        stale_keys = [str(x) for x in details.get("stale_task_keys", []) if str(x).strip()]
        recoverable_keys = [str(x) for x in details.get("recoverable_task_keys", []) if str(x).strip()]
        performed: List[str] = []

        for canonical_key in stale_keys:
            info = running_set.get(canonical_key)
            if not isinstance(info, dict):
                continue
            fut = info.get("future")
            if fut is not None and not fut.done():
                fut.cancel()
            running_set.pop(canonical_key, None)
            stage_name = str(info.get("stage", "collect"))
            agent_obj = info.get("agent")
            tracker.fail_task(stage_name, str(getattr(agent_obj, "id", "") or ""), "stalled_auto_recover")
            base_item = info.get("queued_item") or task_blueprints_by_key.get(canonical_key)
            if isinstance(base_item, dict):
                pending_queue.insert(0, _build_retry_item(base_item, "stalled_checkpoint"))
            existing = task_state.get(canonical_key, {})
            _touch_task_state(
                canonical_key,
                status="pending",
                stage=existing.get("stage", stage_name),
                profile=existing.get("profile", str(info.get("profile", ""))),
                raw_task_text=existing.get("raw_task_text", str(info.get("raw_task_text", ""))),
                retry_requested=True,
                recoverable=True,
                error="auto_recover:stalled_checkpoint",
            )
            performed.append(f"retry(stale:{canonical_key[:8]})")

        for canonical_key in recoverable_keys:
            if canonical_key in running_set or _queue_contains(canonical_key):
                continue
            current_status = str(task_state.get(canonical_key, {}).get("status", "") or "").strip().lower()
            if current_status in {"done", "dropped", "skipped"}:
                continue
            base_item = task_blueprints_by_key.get(canonical_key)
            if not isinstance(base_item, dict):
                continue
            pending_queue.append(_build_retry_item(base_item, "recoverable_checkpoint"))
            existing = task_state.get(canonical_key, {})
            _touch_task_state(
                canonical_key,
                status="pending",
                stage=existing.get("stage", str(base_item.get("task_input", {}).get("input_data", {}).get("stage_name", "collect"))),
                profile=existing.get("profile", str(base_item.get("profile_name", ""))),
                raw_task_text=existing.get("raw_task_text", str(base_item.get("raw_task_text", ""))),
                retry_requested=True,
                recoverable=True,
            )
            performed.append(f"requeue(recoverable:{canonical_key[:8]})")

        if not performed:
            return
        recovery_action = "; ".join(performed[:3])
        health_runtime["active_recovery_action"] = recovery_action
        escalation_row = {
            "run_id": manifest.run_id,
            "stage": str(tracker._current_stage or "unknown"),
            "reason": "auto_recovery_triggered",
            "action": recovery_action,
            "confidence": 0.82,
            "stale_tasks": int(doctor_dict.get("stale_tasks", 0) or 0),
            "recoverable_tasks": int(doctor_dict.get("recoverable_tasks", 0) or 0),
            "target_keys": stale_keys[:5] + recoverable_keys[:5],
        }
        master.append_escalation(escalation_row)
        logger.warning(
            "Master auto-recovery triggered: "
            f"action={recovery_action} "
            f"stale={doctor_dict.get('stale_tasks', 0)} "
            f"recoverable={doctor_dict.get('recoverable_tasks', 0)}"
        )
        last_escalation_ts = now_epoch

    def _run_master_health_tick(force: bool = False) -> None:
        nonlocal last_health_tick_ts
        if not effective_master_enabled:
            return
        now_epoch = datetime.now().timestamp()
        if not force and (now_epoch - last_health_tick_ts) < float(master_health_interval_sec):
            return
        last_health_tick_ts = now_epoch

        doctor_snapshot = run_doctor(
            working_dir=config.working_dir,
            task_mapping=list(memory.task_mapping or []),
            task_attempts=dict(memory.task_attempts or {}),
            stale_seconds=master_stall_seconds,
            run_id=manifest.run_id,
        ).as_dict()
        running_stage_counts = {"collect": 0, "analyze": 0, "report": 0}
        for info in running_set.values():
            stage_name = str(info.get("stage", "") or "")
            if stage_name in running_stage_counts:
                running_stage_counts[stage_name] += 1
        recent_failure_rate = 0.0
        if completed_buffer:
            recent_failure_rate = sum(1 for x in completed_buffer if x.status == "failed") / max(len(completed_buffer), 1)

        if master_auto_recover:
            _auto_recover_from_doctor(doctor_snapshot)
            pending_queue[:] = sorted(pending_queue, key=_queue_sort_key)

        health_snapshot = master.evaluate_health(
            doctor_summary=doctor_snapshot,
            running_stage_counts=running_stage_counts,
            oldest_running_checkpoint_age_sec=_oldest_running_checkpoint_age_sec(),
            time_since_last_completion_sec=max(0.0, (datetime.now(timezone.utc) - last_completion_ts).total_seconds()),
            recent_failure_rate=recent_failure_rate,
            stale_seconds=master_stall_seconds,
            active_recovery_action=str(health_runtime.get("active_recovery_action", "")),
        )
        health_runtime["health_status"] = str(health_snapshot.get("health_status", "unknown"))
        health_runtime["stall_risk_score"] = int(health_snapshot.get("stall_risk_score", 0) or 0)
        health_runtime["active_recovery_action"] = str(health_snapshot.get("active_recovery_action", "") or "")
        master.save_health_snapshot(health_snapshot)
        _refresh_live_run_state(str(tracker._stage_detail.get(str(tracker._current_stage or ""), "") or ""))
        _update_master_tracker()

    _update_master_tracker()
    _run_master_health_tick(force=True)
    master.save_queue_snapshot(pending_queue)

    async def _dispatch_one(item: Dict[str, Any]) -> None:
        item = _hydrate_queue_item(item)
        canonical_key = str(item.get("canonical_task_key", ""))
        stage_name = item.get("task_input", {}).get("input_data", {}).get("stage_name", "collect")
        if stage_name not in stage_started:
            stage_started.add(stage_name)
            manifest.start_stage(_manifest_stage(stage_name))
            tracker.start_stage(stage_name)

        agent = await memory.get_or_create_agent(
            agent_class=item["agent_class"],
            task_input=item["task_input"],
            resume=resume,
            priority=item.get("priority", 0),
            **item.get("agent_kwargs", {}),
        )

        prior_attempts = int(task_state.get(canonical_key, {}).get("attempts", 0) or 0)
        _touch_task_state(
            canonical_key,
            status="running",
            stage=stage_name,
            profile=item.get("profile_name", ""),
            raw_task_text=item.get("raw_task_text", ""),
            agent_id=agent.id,
            started_at=task_state.get(canonical_key, {}).get("started_at", utc_now_iso()),
            attempts=max(prior_attempts + 1, 1),
            recoverable=True,
        )
        tracker.task_started(stage_name, agent.id)
        fut = asyncio.create_task(agent.async_run(**item["task_input"]))
        running_set[canonical_key] = {
            "future": fut,
            "agent": agent,
            "stage": stage_name,
            "profile": item.get("profile_name", ""),
            "raw_task_text": item.get("raw_task_text", ""),
            "queued_item": item,
        }
        memory.save()
        _sync_live_progress()
        _update_master_tracker()
        _refresh_tracker_report_detail()

    def _maybe_finish_stage(stage_name: str) -> None:
        if stage_name in stage_finished or stage_name not in stage_started:
            return
        has_pending = any(
            x.get("task_input", {}).get("input_data", {}).get("stage_name", "") == stage_name
            for x in pending_queue
        )
        has_running = any(info.get("stage") == stage_name for info in running_set.values())
        if has_pending or has_running:
            return
        failed_count = tracker._failed.get(stage_name, 0)
        if failed_count > 0:
            manifest.fail_stage(_manifest_stage(stage_name), f"{failed_count} task(s) failed")
        else:
            manifest.complete_stage(_manifest_stage(stage_name))
        tracker.finish_stage(stage_name)
        stage_finished.add(stage_name)

    while pending_queue or running_set:
        pending_queue = sorted(pending_queue, key=_queue_sort_key)
        _refresh_tracker_report_detail()
        _run_master_health_tick()

        dispatched_any = False
        i = 0
        while len(running_set) < max_parallel and i < len(pending_queue):
            candidate = pending_queue[i]
            canonical_key = str(candidate.get("canonical_task_key", ""))
            status = str(task_state.get(canonical_key, {}).get("status", ""))
            if status in {"done", "dropped", "skipped"}:
                pending_queue.pop(i)
                continue
            if not _can_dispatch(candidate):
                i += 1
                continue
            pending_queue.pop(i)
            await _dispatch_one(candidate)
            dispatched_any = True
            _refresh_tracker_report_detail()

        if not running_set and pending_queue and not dispatched_any:
            # Avoid deadlock when only report tasks remain due to guardrail.
            all_report = all(
                x.get("task_input", {}).get("input_data", {}).get("stage_name", "collect") == "report"
                for x in pending_queue
            )
            if all_report:
                for item in pending_queue:
                    item.setdefault("task_input", {}).setdefault("input_data", {})
                    item["task_input"]["input_data"]["report_override_confidence"] = 0.90
                continue

        if not running_set:
            # No work currently running; continue scheduling or break if queue empty.
            if not pending_queue:
                break
            await asyncio.sleep(0.2)
            _refresh_tracker_report_detail()
            _run_master_health_tick()
            continue

        done_futures, _ = await asyncio.wait(
            [x["future"] for x in running_set.values()],
            timeout=1.0,
            return_when=asyncio.FIRST_COMPLETED,
        )

        if not done_futures:
            _refresh_tracker_report_detail()
            _run_master_health_tick()
            trigger = master.should_review(completed_buffer=completed_buffer, last_gate_review_at=last_gate_review_at)
            if trigger:
                now = datetime.now(timezone.utc)
                if (now - last_replan_at).total_seconds() >= cooldown_sec:
                    decision = master.review(
                        trigger=trigger,
                        completed_buffer=completed_buffer,
                        pending_queue=pending_queue,
                        task_state=task_state,
                    )
                    pre_len = len(pending_queue)
                    pending_queue, _ = master.apply_decision(
                        decision=decision,
                        pending_queue=pending_queue,
                        task_state=task_state,
                        target_name=target_name,
                        target_type=target_type,
                    )
                    for item in pending_queue:
                        _hydrate_queue_item(item)
                    delta = len(pending_queue) - pre_len
                    if delta != 0:
                        stage = "collect"
                        if any(x.get("task_input", {}).get("input_data", {}).get("stage_name") == "analyze" for x in pending_queue[-max(1, abs(delta)):]):
                            stage = "analyze"
                        tracker.adjust_total_tasks(stage, delta)
                    completed_buffer = []
                    last_gate_review_at = _parse_iso(str(decision.created_at))
                    last_replan_at = last_gate_review_at
                    master.save_queue_snapshot(pending_queue)
                    _update_master_tracker()
                    save_task_state(config.working_dir, task_state)
            continue

        for fut in done_futures:
            completed_key = None
            completed_info: Optional[Dict[str, Any]] = None
            for ckey, info in list(running_set.items()):
                if info["future"] is fut:
                    completed_key = ckey
                    completed_info = info
                    running_set.pop(ckey, None)
                    break
            if completed_key is None or completed_info is None:
                continue

            agent = completed_info["agent"]
            stage_name = completed_info["stage"]
            profile_name = completed_info["profile"]
            started_at = str(task_state.get(completed_key, {}).get("started_at", utc_now_iso()))
            completed_at = utc_now_iso()
            duration = max(
                0.0,
                (_parse_iso(completed_at) - _parse_iso(started_at)).total_seconds(),
            )

            status = "done"
            err_text = None
            try:
                _ = fut.result()
                logger.info(f"Task finished: agent={agent.id} stage={stage_name} key={completed_key}")
                tracker.complete_task(stage_name, agent.id)
                _touch_task_state(
                    completed_key,
                    status="done",
                    stage=stage_name,
                    profile=profile_name,
                    raw_task_text=completed_info.get("raw_task_text", ""),
                    agent_id=agent.id,
                    completed_at=completed_at,
                    recoverable=True,
                )
            except Exception as e:
                status = "failed"
                err_text = str(e)
                tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
                logger.error(f"Task failed: agent={agent.id} stage={stage_name} key={completed_key}, error={e}\n{tb_str}")
                tracker.fail_task(stage_name, agent.id, str(e))
                _touch_task_state(
                    completed_key,
                    status="failed",
                    stage=stage_name,
                    profile=profile_name,
                    raw_task_text=completed_info.get("raw_task_text", ""),
                    agent_id=agent.id,
                    failed_at=completed_at,
                    error=err_text,
                    recoverable=True,
                )

            event = TaskCompletionEvent(
                canonical_task_key=completed_key,
                agent_id=agent.id,
                stage=stage_name,  # type: ignore[arg-type]
                profile=profile_name,
                status=status,  # type: ignore[arg-type]
                started_at=started_at,
                completed_at=completed_at,
                duration_sec=float(duration),
                artifact_ids=[],
                error=err_text,
            )
            event = master.ingest_completion(event, memory)
            completed_buffer.append(event)
            last_completion_ts = datetime.now(timezone.utc)
            health_runtime["active_recovery_action"] = ""

            _maybe_finish_stage(stage_name)
            _sync_live_progress()
            master.save_queue_snapshot(pending_queue)
            _update_master_tracker()
            _refresh_tracker_report_detail()
            _run_master_health_tick(force=True)
            save_task_state(config.working_dir, task_state)
            memory.save()

        trigger = master.should_review(completed_buffer=completed_buffer, last_gate_review_at=last_gate_review_at)
        if trigger:
            now = datetime.now(timezone.utc)
            failure_rate = sum(1 for x in completed_buffer if x.status == "failed") / max(len(completed_buffer), 1)
            if (now - last_replan_at).total_seconds() >= cooldown_sec or failure_rate > 0.30:
                decision = master.review(
                    trigger=trigger,
                    completed_buffer=completed_buffer,
                    pending_queue=pending_queue,
                    task_state=task_state,
                )
                pre_len = len(pending_queue)
                pending_queue, _ = master.apply_decision(
                    decision=decision,
                    pending_queue=pending_queue,
                    task_state=task_state,
                    target_name=target_name,
                    target_type=target_type,
                )
                for item in pending_queue:
                    _hydrate_queue_item(item)
                delta = len(pending_queue) - pre_len
                if delta != 0:
                    stage = "collect"
                    if any(x.get("task_input", {}).get("input_data", {}).get("stage_name") == "analyze" for x in pending_queue[-max(1, abs(delta)):]):
                        stage = "analyze"
                    tracker.adjust_total_tasks(stage, delta)
                completed_buffer = []
                last_gate_review_at = _parse_iso(str(decision.created_at))
                last_replan_at = last_gate_review_at
                master.save_queue_snapshot(pending_queue)
                _update_master_tracker()
                save_task_state(config.working_dir, task_state)
        _run_master_health_tick()

    for _stage in list(stage_started):
        _maybe_finish_stage(_stage)
    _run_master_health_tick(force=True)
    tracker.clear_stage_detail("report")
    _update_master_tracker()
    master.save_queue_snapshot(pending_queue)

    # Stop periodic progress updates
    tracker.stop_periodic()

    # Check for produced artifacts
    working_dir = config.working_dir
    for ext in ('*.md', '*.docx', '*.pdf'):
        for fpath in _glob.glob(os.path.join(working_dir, ext)):
            artifact_type = 'report_md' if fpath.endswith('.md') else fpath.rsplit('.', 1)[-1]
            manifest.add_artifact(fpath, artifact_type)

    # Mark render stage (pandoc/pdf was done inside report_generator)
    if manifest.stages.get('render', {}).get('status') == 'pending':
        manifest.complete_stage('render')

    manifest_data = manifest.save()

    # Record run history
    history.record(
        run_id=manifest.run_id,
        target_type=target_type,
        task_count=total_task_count,
        estimated_sec=estimated_sec,
        actual_sec=manifest_data.get('actual_total_sec', 0),
        stages={s: info.get('duration_sec') for s, info in manifest_data.get('stages', {}).items()},
    )
    history.finish_live_run(manifest.run_id)

    # Print final summary
    pdf_status = ''
    if pdf_mode == 'skip':
        pdf_status = 'skipped (pdf_mode=skip)'
    elif any(a['type'] == 'pdf' and a['exists'] for a in manifest_data.get('artifacts', [])):
        pdf_status = 'generated successfully'
    else:
        pdf_status = 'not produced (docx2pdf unavailable or failed)'

    tracker.print_summary(
        artifacts=manifest_data.get('artifacts', []),
        manifest_path=os.path.join(manifest.output_dir, 'run_manifest.json'),
        pdf_status=pdf_status,
        success=manifest_data.get('success', False),
    )
    _write_live_run_state(
        working_dir=config.working_dir,
        run_id=manifest.run_id,
        status="finished",
        stage="complete",
        detail="success" if bool(manifest_data.get("success", False)) else "failed_or_incomplete",
        health_status=str(health_runtime.get("health_status", "unknown")),
        stall_risk_score=int(health_runtime.get("stall_risk_score", 0) or 0),
        active_recovery_action=str(health_runtime.get("active_recovery_action", "") or ""),
    )

    # Persist final state
    memory.save()
    if manifest_data.get("success", False):
        logger.info("Run completed successfully")
    else:
        logger.warning("Run finished with failures or incomplete required outputs")


if __name__ == '__main__':
    args = parse_arguments()
    selected_executor, executor_warning = resolve_execution_backend(args)
    if executor_warning:
        print(f"[warning] {executor_warning}", file=sys.stderr)

    if args.resolved_input:
        if not os.path.exists(args.config):
            print(f"Resolved input config not found: {args.config}", file=sys.stderr)
            sys.exit(2)
        resolved_config_path = args.config
        print(f"Using pre-resolved config: {resolved_config_path}", file=sys.stderr)
    else:
        planner_overrides = None
        if args.planner or args.force_planner:
            planner_overrides = maybe_run_planner(force=should_force_planner(args), config_path=args.config)

        try:
            cli_profiles = collect_cli_profiles(args)
        except ValueError as exc:
            print(f"Invalid profile selection: {exc}", file=sys.stderr)
            sys.exit(2)

        if planner_overrides:
            planner_profiles = planner_overrides.get('target_profiles') or []
            if not planner_profiles and planner_overrides.get('target_type'):
                planner_profiles = [planner_overrides['target_type']]
            active_profiles = [normalize_profile_name(p) for p in planner_profiles]
            if not active_profiles:
                print("Planner did not return any profiles. Cannot continue.", file=sys.stderr)
                sys.exit(2)
            if cli_profiles:
                print(
                    f"[warning] Ignoring CLI profile flags in planner mode: {', '.join(cli_profiles)}",
                    file=sys.stderr,
                )
        else:
            if not cli_profiles:
                print(
                    "No profiles selected. Use one or more profile flags such as "
                    "--company, --macro, --industry, or --profile <name>.",
                    file=sys.stderr,
                )
                sys.exit(2)
            active_profiles = cli_profiles

        try:
            resolution = resolve_and_write_config(
                base_config_path=args.config,
                selected_profiles=active_profiles,
                resolved_config_path=args.resolved_config,
                planner_overrides=planner_overrides or {},
                runtime_overrides={'pdf_mode': args.pdf_mode},
            )
        except ValueError as exc:
            print(f"Config resolution failed: {exc}", file=sys.stderr)
            sys.exit(2)

        resolved_config_path = resolution['resolved_config_path']
        print(
            f"Resolved config: {resolved_config_path} | profiles: {', '.join(resolution['selected_profiles'])} | "
            f"tasks collect={resolution['collect_task_count']} analyze={resolution['analysis_task_count']}",
            file=sys.stderr,
        )

    if args.dry_run:
        print("Dry run complete. No agents executed.", file=sys.stderr)
        sys.exit(0)

    if args.status:
        sys.exit(
            print_status_snapshot(
                resolved_config_path,
                stale_seconds=args.stale_seconds,
                status_stall_advice_minutes=args.status_stall_advice_minutes,
            )
        )

    if args.doctor or args.repair_resume:
        config = Config(config_file_path=resolved_config_path, config_dict={})
        memory = Memory(config=config)
        loaded = memory.load()
        if not loaded:
            print("No memory checkpoint found. Nothing to diagnose/repair.", file=sys.stderr)
            if args.repair_resume:
                sys.exit(1)
        run_id = args.resume_run_id.strip() or _latest_progress_run_id(os.path.join(config.working_dir, "logs"))
        doctor_before = run_doctor(
            working_dir=config.working_dir,
            task_mapping=list(memory.task_mapping or []),
            task_attempts=dict(memory.task_attempts or {}),
            stale_seconds=args.stale_seconds,
            run_id=run_id,
        ).as_dict()
        _print_doctor_summary(doctor_before)
        report_payload = {
            "mode": "repair" if args.repair_resume else "doctor",
            "before": doctor_before,
            "run_id": run_id,
            "generated_at": utc_now_iso(),
        }

        if args.repair_resume:
            repaired_mapping, repair_stats = repair_task_mapping(
                working_dir=config.working_dir,
                task_mapping=list(memory.task_mapping or []),
                task_attempts=dict(memory.task_attempts or {}),
            )
            memory.task_mapping = repaired_mapping
            if hasattr(memory, "_migrate_task_structures"):
                memory._migrate_task_structures()
            memory.save()
            master_repair_stats = repair_master_state(
                working_dir=config.working_dir,
                task_state=load_task_state(config.working_dir),
            )
            doctor_after = run_doctor(
                working_dir=config.working_dir,
                task_mapping=list(memory.task_mapping or []),
                task_attempts=dict(memory.task_attempts or {}),
                stale_seconds=args.stale_seconds,
                run_id=run_id,
            ).as_dict()
            print("")
            print("After repair:")
            _print_doctor_summary(doctor_after)
            report_payload["repair_stats"] = repair_stats
            report_payload["master_repair_stats"] = master_repair_stats
            report_payload["after"] = doctor_after

        report_path = write_recovery_report(config.working_dir, report_payload)
        print(f"Recovery report saved: {report_path}")
        sys.exit(0)

    if selected_executor == 'render':
        from src.executors.render_executor import RenderExecutor
        api_key = os.getenv('RENDER_API_KEY', '')
        service_id = os.getenv('RENDER_SERVICE_ID', '')
        executor = RenderExecutor(api_key=api_key, service_id=service_id)
        # Read config to send to remote
        config_path = resolved_config_path
        if not os.path.exists(config_path):
            print(f"Config file {config_path} not found. Run with --planner first.", file=sys.stderr)
            sys.exit(1)
        with open(config_path, 'r') as f:
            config_yaml = f.read()
        # Forward relevant args to remote
        remote_args = []
        if not args.resume:
            remote_args.append('--no-resume')
        if args.pdf_mode != 'auto':
            remote_args.extend(['--pdf-mode', args.pdf_mode])
        if args.max_concurrent != MAX_CONCURRENT:
            remote_args.extend(['--max-concurrent', str(args.max_concurrent)])
        if args.max_tasks_per_profile > 0:
            remote_args.extend(['--max-tasks-per-profile', str(args.max_tasks_per_profile)])
        if args.resume_stalled_only:
            remote_args.append('--resume-stalled-only')
        if args.stale_seconds != DEFAULT_STALE_SECONDS:
            remote_args.extend(['--stale-seconds', str(args.stale_seconds)])
        if not args.master_enabled:
            remote_args.append('--no-master')
        if args.master_batch_size != 3:
            remote_args.extend(['--master-batch-size', str(args.master_batch_size)])
        if args.master_batch_max_age_sec != 600:
            remote_args.extend(['--master-batch-max-age-sec', str(args.master_batch_max_age_sec)])
        if args.master_max_added_tasks_per_stage != 8:
            remote_args.extend(['--master-max-added-tasks-per-stage', str(args.master_max_added_tasks_per_stage)])
        if args.master_max_total_task_growth_pct != 25:
            remote_args.extend(['--master-max-total-task-growth-pct', str(args.master_max_total_task_growth_pct)])
        if args.master_replan_cooldown_sec != 120:
            remote_args.extend(['--master-replan-cooldown-sec', str(args.master_replan_cooldown_sec)])
        if args.master_strategy != 'balanced':
            remote_args.extend(['--master-strategy', args.master_strategy])
        if args.master_health_interval_sec != 30:
            remote_args.extend(['--master-health-interval-sec', str(args.master_health_interval_sec)])
        if not args.master_auto_recover:
            remote_args.append('--no-master-auto-recover')
        if args.master_allow_drop:
            remote_args.append('--master-allow-drop')
        if args.master_stall_seconds != 900:
            remote_args.extend(['--master-stall-seconds', str(args.master_stall_seconds)])
        if args.master_escalation_cooldown_sec != 180:
            remote_args.extend(['--master-escalation-cooldown-sec', str(args.master_escalation_cooldown_sec)])
        if args.verbose:
            remote_args.append('--verbose')
        if args.quiet:
            remote_args.append('--quiet')
        remote_args.extend(['--config', 'my_config.yaml', '--resolved-input'])
        exit_code = asyncio.run(executor.run(config_yaml, cli_args=remote_args))
        sys.exit(exit_code)
    else:
        asyncio.run(run_report(
            resume=args.resume,
            max_concurrent=args.max_concurrent,
            max_tasks_per_profile=args.max_tasks_per_profile,
            resume_stalled_only=args.resume_stalled_only,
            stale_seconds=args.stale_seconds,
            master_enabled=args.master_enabled,
            master_batch_size=args.master_batch_size,
            master_batch_max_age_sec=args.master_batch_max_age_sec,
            master_max_added_tasks_per_stage=args.master_max_added_tasks_per_stage,
            master_max_total_task_growth_pct=args.master_max_total_task_growth_pct,
            master_replan_cooldown_sec=args.master_replan_cooldown_sec,
            master_strategy=args.master_strategy,
            master_health_interval_sec=args.master_health_interval_sec,
            master_auto_recover=args.master_auto_recover,
            master_allow_drop=args.master_allow_drop,
            master_stall_seconds=args.master_stall_seconds,
            master_escalation_cooldown_sec=args.master_escalation_cooldown_sec,
            verbose=args.verbose,
            quiet=args.quiet,
            pdf_mode=args.pdf_mode,
            purge_stale_images=args.purge_stale_images,
            config_file_path=resolved_config_path,
            executor_name='local',
        ))
