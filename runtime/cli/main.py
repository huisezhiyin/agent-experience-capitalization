import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any

from runtime.backends import resolve_backend_config
from runtime.core.engine import (
    activate_assets,
    apply_asset_effectiveness,
    apply_candidate_promotion_feedback,
    build_candidate_review_queue,
    build_trace_bundle,
    explain_object,
    extract_candidates,
    now_utc,
    promote_candidate,
    review_trace_bundle,
    should_promote_candidate,
)
from runtime.core.project_install import install_project_agents
from runtime.core.project_policy import (
    DEFAULT_PROJECT_STATUS,
    load_project_policy,
)
from runtime.storage.fs_store import (
    default_activation_view_path,
    default_db_path,
    default_milvus_db_path,
    default_trace_bundle_path,
    iter_json_objects,
    load_json,
    memory_root_for_workspace,
    save_json,
    default_shared_asset_path,
    shared_db_path,
    shared_memory_root,
    shared_milvus_db_path,
    storage_layout_for_workspace,
    workspace_from_payload,
)
from runtime.storage.milvus_store import (
    milvus_available,
    milvus_backend_summary,
    milvus_lock_summary,
    sync_assets_directory_with_report,
    upsert_asset_vector,
)
from runtime.storage.sqlite_store import (
    ensure_db,
    find_latest_activation,
    get_asset,
    get_candidate,
    list_activation_logs,
    list_assets,
    list_candidates,
    log_activation,
    record_activation_feedback,
    summarize_asset_feedback,
    touch_assets_last_used,
    upsert_asset,
    upsert_candidate,
    upsert_episode,
    upsert_trace,
)

ALL_CANDIDATE_STATUSES = ("new", "needs_review", "approved", "rejected", "promoted")
DEFAULT_REVIEW_QUEUE_STATUSES = ("needs_review", "approved", "new")
DEFAULT_FEEDBACK_PENDING_HOURS = 24.0
STALE_FEEDBACK_HELP_SIGNAL = "unclear"


def _feedback_pending_hours() -> float:
    raw_value = os.environ.get("EXPCAP_FEEDBACK_PENDING_HOURS")
    if raw_value is None:
        return DEFAULT_FEEDBACK_PENDING_HOURS
    try:
        return max(float(raw_value), 0.0)
    except ValueError:
        return DEFAULT_FEEDBACK_PENDING_HOURS


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_pending_feedback(activation: dict[str, Any], *, now: datetime, pending_hours: float) -> bool:
    created_at = _parse_datetime(activation.get("created_at"))
    if created_at is None:
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return now - created_at <= timedelta(hours=pending_hours)


def _summarize_activation_feedback(activations: list[dict[str, Any]]) -> dict[str, Any]:
    feedback_summary = {
        "supported_strong": 0,
        "supported_weak": 0,
        "unclear": 0,
        "pending": 0,
        "missing": 0,
        "missing_total": 0,
    }
    pending_hours = _feedback_pending_hours()
    now = datetime.now(timezone.utc)
    for activation in activations:
        help_signal = activation.get("feedback", {}).get("help_signal")
        if help_signal in {"supported_strong", "supported_weak", "unclear"}:
            feedback_summary[help_signal] += 1
        else:
            feedback_summary["missing_total"] += 1
            if _is_pending_feedback(activation, now=now, pending_hours=pending_hours):
                feedback_summary["pending"] += 1
            else:
                feedback_summary["missing"] += 1
    feedback_summary["pending_hours"] = pending_hours
    return feedback_summary


def _build_unresolved_activation_items(
    activations: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    pending_hours = _feedback_pending_hours()
    now = datetime.now(timezone.utc)
    items: list[dict[str, Any]] = []
    for activation in activations:
        help_signal = activation.get("feedback", {}).get("help_signal")
        if help_signal in {"supported_strong", "supported_weak", "unclear"}:
            continue
        created_at = _parse_datetime(activation.get("created_at"))
        age_hours = None
        if created_at is not None:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_hours = round((now - created_at).total_seconds() / 3600, 2)
        state = (
            "pending"
            if _is_pending_feedback(activation, now=now, pending_hours=pending_hours)
            else "missing"
        )
        items.append(
            {
                "activation_id": activation.get("activation_id"),
                "task_query": activation.get("task_query"),
                "state": state,
                "created_at": activation.get("created_at"),
                "age_hours": age_hours,
                "selected_count": len(activation.get("selected_assets", [])),
            }
        )

    state_priority = {"missing": 0, "pending": 1}
    items.sort(
        key=lambda item: (
            state_priority.get(str(item.get("state")), 99),
            -(item.get("age_hours") or 0.0),
            item.get("created_at") or "",
        )
    )
    return items[:limit]


def _build_asset_review_backlog(
    review_status_summary: dict[str, int],
    *,
    total_assets: int,
) -> dict[str, Any]:
    unproven_count = int(review_status_summary.get("unproven", 0) or 0)
    healthy_count = int(review_status_summary.get("healthy", 0) or 0)
    watch_count = int(review_status_summary.get("watch", 0) or 0)
    needs_review_count = int(review_status_summary.get("needs_review", 0) or 0)
    denominator = total_assets if total_assets > 0 else 1
    return {
        "total_assets": total_assets,
        "healthy_count": healthy_count,
        "watch_count": watch_count,
        "needs_review_count": needs_review_count,
        "unproven_count": unproven_count,
        "healthy_ratio": round(healthy_count / denominator, 4) if total_assets else 0.0,
        "unproven_ratio": round(unproven_count / denominator, 4) if total_assets else 0.0,
    }


def _update_activation_view_file(workspace: Path, activation: dict[str, Any]) -> None:
    activation_view_path = memory_root_for_workspace(workspace) / "views" / f"{activation['activation_id']}.json"
    if activation_view_path.exists():
        save_json(activation_view_path, activation)


def _auto_resolve_stale_activation_feedback(
    *,
    workspace: Path,
    db_path: Path,
) -> dict[str, Any]:
    summary = {
        "auto_resolved_count": 0,
        "auto_resolved_activation_ids": [],
        "resolution_help_signal": STALE_FEEDBACK_HELP_SIGNAL,
        "pending_hours": _feedback_pending_hours(),
    }
    activations = list_activation_logs(db_path, workspace=str(workspace))
    if not activations:
        return summary

    feedback_at = now_utc()
    now = datetime.now(timezone.utc)
    for activation in activations:
        if activation.get("feedback", {}).get("help_signal"):
            continue
        if _is_pending_feedback(activation, now=now, pending_hours=summary["pending_hours"]):
            continue
        updated_activation = record_activation_feedback(
            db_path,
            activation_id=activation["activation_id"],
            feedback={
                "help_signal": STALE_FEEDBACK_HELP_SIGNAL,
                "signal_source": "auto_cleanup_stale",
                "feedback_summary": "Auto-closed after feedback window expired without explicit review.",
                "feedback_at": feedback_at,
                "resolution": "stale_timeout",
            },
        )
        if not updated_activation:
            continue
        _update_activation_view_file(workspace, updated_activation)
        summary["auto_resolved_count"] += 1
        summary["auto_resolved_activation_ids"].append(updated_activation["activation_id"])
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="expcap",
        description="Local-first runtime for agent experience capitalization.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Create and persist a trace bundle from task facts.")
    ingest.add_argument("--workspace", required=True, help="Workspace path for the task.")
    ingest.add_argument("--task", required=True, help="Task summary used as the trace hint.")
    ingest.add_argument("--user-request", help="Original user request. Defaults to --task.")
    ingest.add_argument("--constraint", dest="constraints", action="append", default=[], help="Explicit task constraint.")
    ingest.add_argument("--command", dest="commands", action="append", default=[], help="Important command executed during the task.")
    ingest.add_argument("--error", dest="errors", action="append", default=[], help="Important error observed during the task.")
    ingest.add_argument("--file-changed", dest="files_changed", action="append", default=[], help="Changed file path. May be passed multiple times.")
    ingest.add_argument("--verification-status", default="unknown", help="Verification status, e.g. passed/failed.")
    ingest.add_argument("--verification-summary", help="Verification summary.")
    ingest.add_argument("--result-status", default="success", help="Result status, e.g. success/partial/failed.")
    ingest.add_argument("--result-summary", help="Result summary.")
    ingest.add_argument("--host", default="codex", help="Host name. Defaults to codex.")
    ingest.add_argument("--session-id", help="Optional host session id.")
    ingest.add_argument("--trace-id", help="Optional explicit trace id.")
    ingest.add_argument("--output", help="Optional output path for the trace JSON.")

    auto_start = subparsers.add_parser(
        "auto-start",
        help="Run the default start-of-task experience get flow.",
    )
    auto_start.add_argument("--task", required=True, help="Current task description.")
    auto_start.add_argument("--workspace", required=True, help="Workspace path for the task.")
    auto_start.add_argument(
        "--constraint",
        dest="constraints",
        action="append",
        default=[],
        help="Explicit task constraint. May be passed multiple times.",
    )
    auto_start.add_argument("--output", help="Optional path for the activation view JSON.")

    auto_finish = subparsers.add_parser(
        "auto-finish",
        help="Run the default end-of-task save flow from trace to optional promoted asset.",
    )
    auto_finish.add_argument("--workspace", required=True, help="Workspace path for the task.")
    auto_finish.add_argument("--task", required=True, help="Task summary used as the trace hint.")
    auto_finish.add_argument("--user-request", help="Original user request. Defaults to --task.")
    auto_finish.add_argument("--constraint", dest="constraints", action="append", default=[], help="Explicit task constraint.")
    auto_finish.add_argument("--command", dest="commands", action="append", default=[], help="Important command executed during the task.")
    auto_finish.add_argument("--error", dest="errors", action="append", default=[], help="Important error observed during the task.")
    auto_finish.add_argument("--file-changed", dest="files_changed", action="append", default=[], help="Changed file path. May be passed multiple times.")
    auto_finish.add_argument("--verification-status", default="unknown", help="Verification status, e.g. passed/failed.")
    auto_finish.add_argument("--verification-summary", help="Verification summary.")
    auto_finish.add_argument("--result-status", default="success", help="Result status, e.g. success/partial/failed.")
    auto_finish.add_argument("--result-summary", help="Result summary.")
    auto_finish.add_argument("--host", default="codex", help="Host name. Defaults to codex.")
    auto_finish.add_argument("--session-id", help="Optional host session id.")
    auto_finish.add_argument("--trace-id", help="Optional explicit trace id.")
    auto_finish.add_argument(
        "--promote-threshold",
        type=float,
        default=0.70,
        help="Minimum per-score threshold required for auto promotion.",
    )
    auto_finish.add_argument(
        "--no-promote",
        action="store_true",
        help="Disable auto promotion even if the candidate meets the threshold.",
    )
    auto_finish.add_argument(
        "--knowledge-scope",
        choices=["project", "cross-project"],
        default="project",
        help="Scope to assign when auto-promoting an asset.",
    )
    auto_finish.add_argument(
        "--knowledge-kind",
        choices=["pattern", "anti_pattern", "rule", "context", "checklist"],
        help="Optional knowledge kind override for promoted assets.",
    )

    install_project = subparsers.add_parser(
        "install-project",
        help="Non-destructively integrate expcap into another project's agent instruction files.",
    )
    install_project.add_argument("--workspace", required=True, help="Target project workspace.")
    install_project.add_argument(
        "--include-claude",
        action="store_true",
        help="Also append an expcap block to CLAUDE.md for Claude Code users.",
    )
    install_project.add_argument(
        "--project-status",
        choices=["active", "inactive"],
        default=DEFAULT_PROJECT_STATUS,
        help="Whether this workspace should auto-start expcap by default. Defaults to active.",
    )

    sync_milvus = subparsers.add_parser(
        "sync-milvus",
        help="Backfill local and optional shared assets into Milvus Lite indexes.",
    )
    sync_milvus.add_argument("--workspace", required=True, help="Workspace path to sync.")
    sync_milvus.add_argument(
        "--include-shared",
        action="store_true",
        help="Also sync shared cross-project assets from ~/.codex/expcap-memory/assets.",
    )
    sync_milvus.add_argument(
        "--prune",
        action="store_true",
        help="Delete Milvus entities whose asset_id no longer exists in the source assets directory.",
    )

    review = subparsers.add_parser("review", help="Generate an episode from a trace bundle.")
    review.add_argument("--input", required=True, help="Path to a trace bundle JSON file.")
    review.add_argument("--output", help="Optional path for the generated episode JSON.")

    extract = subparsers.add_parser("extract", help="Generate candidates from an episode.")
    extract.add_argument("--episode", required=True, help="Path to an episode JSON file.")
    extract.add_argument(
        "--output-dir",
        help="Optional directory for generated candidate JSON files.",
    )

    promote = subparsers.add_parser("promote", help="Promote one candidate into an asset.")
    promote.add_argument("--candidate", required=True, help="Path to a candidate JSON file.")
    promote.add_argument(
        "--output",
        help="Optional path for the promoted asset JSON file.",
    )
    promote.add_argument(
        "--knowledge-scope",
        choices=["project", "cross-project"],
        default="project",
        help="Whether to store this asset in the project layer or shared cross-project layer.",
    )
    promote.add_argument(
        "--knowledge-kind",
        choices=["pattern", "anti_pattern", "rule", "context", "checklist"],
        help="Optional knowledge kind override.",
    )

    activate = subparsers.add_parser("activate", help="Build an activation view for a task.")
    activate.add_argument("--task", required=True, help="Current task description.")
    activate.add_argument(
        "--workspace",
        required=True,
        help="Workspace path used to resolve the local memory store.",
    )
    activate.add_argument(
        "--constraints",
        action="append",
        default=[],
        help="Explicit task constraint. May be passed multiple times.",
    )
    activate.add_argument(
        "--assets-dir",
        help="Optional asset directory. Defaults to <workspace>/.agent-memory/assets.",
    )
    activate.add_argument(
        "--candidates-dir",
        help="Optional candidate directory used as a fallback source.",
    )
    activate.add_argument("--output", help="Optional path for the activation view JSON.")

    explain = subparsers.add_parser("explain", help="Explain a stored object.")
    explain.add_argument(
        "--input",
        required=True,
        help="Path to an episode, candidate, asset, or activation view JSON file.",
    )

    review_candidates = subparsers.add_parser(
        "review-candidates",
        help="Build the candidate review queue or apply manual review actions.",
    )
    review_candidates.add_argument("--workspace", required=True, help="Workspace path for the queue.")
    review_candidates.add_argument(
        "--status",
        dest="statuses",
        action="append",
        choices=list(ALL_CANDIDATE_STATUSES),
        help="Candidate statuses to include. Defaults to needs_review, approved, and new.",
    )
    review_candidates.add_argument(
        "--action",
        choices=["approve", "reject", "promote"],
        help="Optional review action to apply to one candidate before rebuilding the queue.",
    )
    review_candidates.add_argument(
        "--candidate-id",
        help="Candidate id used together with --action.",
    )
    review_candidates.add_argument(
        "--knowledge-scope",
        choices=["project", "cross-project"],
        default="project",
        help="Knowledge scope used when --action promote is selected.",
    )
    review_candidates.add_argument(
        "--knowledge-kind",
        choices=["pattern", "anti_pattern", "rule", "context", "checklist"],
        help="Optional knowledge kind override used when --action promote is selected.",
    )
    review_candidates.add_argument("--output", help="Optional output path for the review queue JSON.")

    status = subparsers.add_parser(
        "status",
        help="Build a short test readiness and usage summary for one workspace.",
    )
    status.add_argument("--workspace", required=True, help="Workspace path to summarize.")
    status.add_argument(
        "--limit",
        type=int,
        default=5,
        help="How many recent items to include per section. Defaults to 5.",
    )
    status.add_argument("--output", help="Optional output path for the status JSON.")
    status.add_argument(
        "--deep-retrieval-check",
        action="store_true",
        help="Open retrieval backends for deeper health checks. Defaults to lightweight checks only.",
    )

    doctor = subparsers.add_parser(
        "doctor",
        help="Diagnose workspace health, retrieval backends, feedback gaps, and review queues.",
    )
    doctor.add_argument("--workspace", required=True, help="Workspace path to diagnose.")
    doctor.add_argument(
        "--limit",
        type=int,
        default=5,
        help="How many recent items to include per section. Defaults to 5.",
    )
    doctor.add_argument("--output", help="Optional output path for the doctor JSON.")
    doctor.add_argument(
        "--deep-retrieval-check",
        action="store_true",
        help="Open retrieval backends for deeper health checks. Defaults to lightweight checks only.",
    )

    return parser


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _infer_activation_help_signal(*, verification_status: str, result_status: str) -> str:
    if verification_status == "passed" and result_status == "success":
        return "supported_strong"
    if verification_status == "passed" or result_status == "success":
        return "supported_weak"
    return "unclear"


def _candidate_path_for_workspace(workspace: Path, candidate_id: str) -> Path:
    return memory_root_for_workspace(workspace) / "candidates" / f"{candidate_id}.json"


def _load_candidate_for_workspace(workspace: Path, db_path: Path, candidate_id: str) -> tuple[dict[str, Any], Path]:
    candidate = get_candidate(db_path, candidate_id=candidate_id)
    candidate_path = _candidate_path_for_workspace(workspace, candidate_id)
    if candidate is None and candidate_path.exists():
        candidate = load_json(candidate_path)
    if candidate is None:
        raise SystemExit(f"candidate not found: {candidate_id}")
    return candidate, candidate_path


def _persist_candidate(workspace: Path, db_path: Path, candidate: dict[str, Any]) -> Path:
    candidate_path = _candidate_path_for_workspace(workspace, candidate["candidate_id"])
    save_json(candidate_path, candidate)
    upsert_candidate(db_path, candidate)
    return candidate_path


def _persist_promoted_asset(
    candidate: dict[str, Any],
    *,
    knowledge_scope: str,
    knowledge_kind: str | None,
) -> tuple[dict[str, Any], Path]:
    workspace = workspace_from_payload(candidate, Path.cwd())
    asset = promote_candidate(
        candidate,
        knowledge_scope=knowledge_scope,
        knowledge_kind=knowledge_kind,
    )
    output_path = (
        default_shared_asset_path(asset)
        if knowledge_scope == "cross-project"
        else memory_root_for_workspace(workspace) / "assets" / f"{asset['asset_type']}s" / f"{asset['asset_id']}.json"
    )
    asset_db_path = shared_db_path() if knowledge_scope == "cross-project" else default_db_path(workspace)
    ensure_db(asset_db_path)
    save_json(output_path, asset)
    upsert_asset(asset_db_path, asset)
    upsert_asset_vector(
        shared_milvus_db_path() if knowledge_scope == "cross-project" else default_milvus_db_path(workspace),
        asset,
    )
    return asset, output_path


def _apply_review_candidate_action(
    *,
    args: argparse.Namespace,
    workspace: Path,
    db_path: Path,
) -> dict[str, Any] | None:
    if not args.action:
        return None
    if not args.candidate_id:
        raise SystemExit("--candidate-id is required when --action is used")

    candidate, _ = _load_candidate_for_workspace(workspace, db_path, args.candidate_id)
    previous_status = candidate.get("status")
    reviewed_at = now_utc()

    if args.action == "approve":
        candidate["status"] = "approved"
    elif args.action == "reject":
        candidate["status"] = "rejected"
    else:
        candidate["status"] = "promoted"

    decision = {
        "action": args.action,
        "review_source": "review-candidates",
        "reviewed_at": reviewed_at,
        "previous_status": previous_status,
        "status_after": candidate["status"],
    }
    if args.action == "promote":
        decision["knowledge_scope"] = args.knowledge_scope
        decision["knowledge_kind"] = args.knowledge_kind or candidate.get("knowledge_kind")

    history = list(candidate.get("review_history") or [])
    history.append(decision)
    candidate["review_history"] = history
    candidate["review_decision"] = decision
    candidate["updated_at"] = reviewed_at
    candidate_path = _persist_candidate(workspace, db_path, candidate)

    asset_result = None
    if args.action == "promote":
        asset, asset_path = _persist_promoted_asset(
            candidate,
            knowledge_scope=args.knowledge_scope,
            knowledge_kind=args.knowledge_kind,
        )
        asset_result = {
            "asset_id": asset["asset_id"],
            "path": str(asset_path),
            "knowledge_scope": asset["knowledge_scope"],
            "knowledge_kind": asset["knowledge_kind"],
        }

    return {
        "candidate_id": candidate["candidate_id"],
        "candidate_path": str(candidate_path),
        "action": args.action,
        "previous_status": previous_status,
        "status": candidate["status"],
        "reviewed_at": reviewed_at,
        "asset": asset_result,
    }


def _handle_ingest(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    trace = build_trace_bundle(
        workspace=workspace,
        task=args.task,
        user_request=args.user_request,
        constraints=args.constraints,
        commands=args.commands,
        errors=args.errors,
        files_changed=args.files_changed,
        verification_status=args.verification_status,
        verification_summary=args.verification_summary,
        result_status=args.result_status,
        result_summary=args.result_summary,
        host=args.host,
        session_id=args.session_id,
        trace_id=args.trace_id,
    )
    output_path = Path(args.output) if args.output else default_trace_bundle_path(workspace, trace)
    save_json(output_path, trace)
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    upsert_trace(db_path, trace)
    _print_json({"saved_to": str(output_path), "trace_id": trace["trace_id"], "trace_bundle": trace})
    return 0


def _handle_auto_start(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    project_activity = load_project_policy(workspace)
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    feedback_cleanup = _auto_resolve_stale_activation_feedback(
        workspace=workspace,
        db_path=db_path,
    )
    view = activate_assets(
        task=args.task,
        workspace=workspace,
        constraints=args.constraints,
        assets_dir=workspace / ".agent-memory" / "assets",
        candidates_dir=workspace / ".agent-memory" / "candidates",
        db_path=db_path,
    )
    output_path = Path(args.output) if args.output else default_activation_view_path(workspace, view)
    save_json(output_path, view)
    log_activation(db_path, view)
    touch_assets_last_used(
        db_path,
        [item["asset_id"] for item in view.get("selected_assets", [])],
        view["created_at"],
    )
    _print_json(
        {
            "saved_to": str(output_path),
            "activation_id": view["activation_id"],
            "selected_count": len(view.get("selected_assets", [])),
            "project_activity": project_activity,
            "feedback_cleanup": feedback_cleanup,
            "activation_view": view,
        }
    )
    return 0


def _handle_review(args: argparse.Namespace) -> int:
    trace = load_json(Path(args.input))
    episode = review_trace_bundle(trace)
    workspace = workspace_from_payload(trace, Path.cwd())
    memory_root = memory_root_for_workspace(workspace)
    output_path = Path(args.output) if args.output else memory_root / "episodes" / f"{episode['episode_id']}.json"
    save_json(output_path, episode)
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    upsert_episode(db_path, episode)
    _print_json({"saved_to": str(output_path), "episode_id": episode["episode_id"], "episode": episode})
    return 0


def _handle_install_project(args: argparse.Namespace) -> int:
    result = install_project_agents(
        Path(args.workspace),
        include_claude=args.include_claude,
        project_status=args.project_status,
    )
    _print_json(result)
    return 0


def _handle_auto_finish(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    memory_root = memory_root_for_workspace(workspace)
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    feedback_cleanup = _auto_resolve_stale_activation_feedback(
        workspace=workspace,
        db_path=db_path,
    )

    trace = build_trace_bundle(
        workspace=workspace,
        task=args.task,
        user_request=args.user_request,
        constraints=args.constraints,
        commands=args.commands,
        errors=args.errors,
        files_changed=args.files_changed,
        verification_status=args.verification_status,
        verification_summary=args.verification_summary,
        result_status=args.result_status,
        result_summary=args.result_summary,
        host=args.host,
        session_id=args.session_id,
        trace_id=args.trace_id,
    )
    trace_path = default_trace_bundle_path(workspace, trace)
    save_json(trace_path, trace)
    upsert_trace(db_path, trace)

    episode = review_trace_bundle(trace)
    episode_path = memory_root / "episodes" / f"{episode['episode_id']}.json"
    save_json(episode_path, episode)
    upsert_episode(db_path, episode)

    activation_feedback = None
    latest_activation = find_latest_activation(
        db_path,
        workspace=str(workspace),
        unresolved_only=True,
    )
    if latest_activation and latest_activation.get("selected_assets"):
        feedback = {
            "help_signal": _infer_activation_help_signal(
                verification_status=args.verification_status,
                result_status=args.result_status,
            ),
            "signal_source": "auto_finish_outcome",
            "feedback_summary": (
                args.result_summary
                or args.verification_summary
                or f"result={args.result_status}, verification={args.verification_status}"
            ),
            "feedback_at": episode.get("created_at"),
            "trace_id": trace["trace_id"],
            "episode_id": episode["episode_id"],
        }
        updated_activation = record_activation_feedback(
            db_path,
            activation_id=latest_activation["activation_id"],
            feedback=feedback,
        )
        if updated_activation:
            _update_activation_view_file(workspace, updated_activation)
        if updated_activation:
            linked_asset_ids = updated_activation.get("selected_asset_ids") or [
                item["asset_id"]
                for item in updated_activation.get("selected_assets", [])
                if isinstance(item, dict) and item.get("asset_id")
            ]
            feedback_stats = summarize_asset_feedback(
                db_path,
                asset_ids=linked_asset_ids,
            )
            for selected_asset in updated_activation.get("selected_assets", []):
                asset_id = selected_asset.get("asset_id")
                if not asset_id:
                    continue
                selected_scope = selected_asset.get("knowledge_scope", "project")
                asset_db_path = shared_db_path() if selected_scope == "cross-project" else db_path
                asset = get_asset(asset_db_path, asset_id=asset_id)
                asset_path = (
                    default_shared_asset_path(
                        {
                            "asset_type": selected_asset["asset_type"],
                            "asset_id": asset_id,
                        }
                    )
                    if selected_scope == "cross-project"
                    else memory_root / "assets" / f"{selected_asset['asset_type']}s" / f"{asset_id}.json"
                )
                if not asset and asset_path.exists():
                    asset = load_json(asset_path)
                if not asset:
                    continue
                asset = apply_asset_effectiveness(
                    asset,
                    feedback_stats.get(asset_id, {}),
                    updated_at=feedback["feedback_at"],
                )
                upsert_asset(asset_db_path, asset)
                save_json(asset_path, asset)
            activation_feedback = {
                "activation_id": updated_activation["activation_id"],
                "help_signal": feedback["help_signal"],
                "linked_asset_ids": linked_asset_ids,
                "feedback_summary": feedback["feedback_summary"],
            }

    saved_candidates = []
    promoted_assets = []
    for candidate in extract_candidates(episode):
        candidate = apply_candidate_promotion_feedback(
            candidate,
            activation_feedback=activation_feedback,
        )
        candidate_path = memory_root / "candidates" / f"{candidate['candidate_id']}.json"
        save_json(candidate_path, candidate)
        upsert_candidate(db_path, candidate)

        if not args.no_promote and should_promote_candidate(
            candidate,
            verification_status=args.verification_status,
            result_status=args.result_status,
            min_score=args.promote_threshold,
        ):
            candidate["status"] = "promoted"
            save_json(candidate_path, candidate)
            upsert_candidate(db_path, candidate)
            asset = promote_candidate(
                candidate,
                knowledge_scope=args.knowledge_scope,
                knowledge_kind=args.knowledge_kind,
            )
            asset_path = (
                default_shared_asset_path(asset)
                if args.knowledge_scope == "cross-project"
                else memory_root / "assets" / f"{asset['asset_type']}s" / f"{asset['asset_id']}.json"
            )
            save_json(asset_path, asset)
            asset_db_path = shared_db_path() if args.knowledge_scope == "cross-project" else db_path
            ensure_db(asset_db_path)
            upsert_asset(asset_db_path, asset)
            asset_milvus_db = shared_milvus_db_path() if args.knowledge_scope == "cross-project" else default_milvus_db_path(workspace)
            upsert_asset_vector(asset_milvus_db, asset)
            promoted_assets.append({"asset_id": asset["asset_id"], "path": str(asset_path)})
        elif candidate.get("promotion_readiness") in {"boosted", "encouraging"}:
            candidate["status"] = "needs_review"
            save_json(candidate_path, candidate)
            upsert_candidate(db_path, candidate)

        saved_candidates.append(
            {
                "candidate_id": candidate["candidate_id"],
                "path": str(candidate_path),
                "status": candidate["status"],
            }
        )

    _print_json(
        {
            "trace": {"trace_id": trace["trace_id"], "path": str(trace_path)},
            "episode": {"episode_id": episode["episode_id"], "path": str(episode_path)},
            "candidates": saved_candidates,
            "promoted_assets": promoted_assets,
            "activation_feedback": activation_feedback,
            "feedback_cleanup": feedback_cleanup,
            "auto_promote_enabled": not args.no_promote,
            "promote_threshold": args.promote_threshold,
        }
    )
    return 0


def _handle_extract(args: argparse.Namespace) -> int:
    episode = load_json(Path(args.episode))
    candidates = extract_candidates(episode)
    workspace = workspace_from_payload(episode, Path.cwd())
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else memory_root_for_workspace(workspace) / "candidates"
    )
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    saved_paths = []
    for candidate in candidates:
        path = output_dir / f"{candidate['candidate_id']}.json"
        save_json(path, candidate)
        upsert_candidate(db_path, candidate)
        saved_paths.append(str(path))
    _print_json({"saved": saved_paths, "candidates": candidates})
    return 0


def _handle_promote(args: argparse.Namespace) -> int:
    candidate = load_json(Path(args.candidate))
    candidate["status"] = "promoted"
    workspace = workspace_from_payload(candidate, Path.cwd())
    save_json(Path(args.candidate), candidate)
    candidate_db_path = default_db_path(workspace)
    ensure_db(candidate_db_path)
    upsert_candidate(candidate_db_path, candidate)
    asset, generated_path = _persist_promoted_asset(
        candidate,
        knowledge_scope=args.knowledge_scope,
        knowledge_kind=args.knowledge_kind,
    )
    output_path = Path(args.output) if args.output else generated_path
    if args.output:
        save_json(output_path, asset)
    _print_json({"saved_to": str(output_path), "asset_id": asset["asset_id"], "asset": asset})
    return 0


def _handle_sync_milvus(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    local_assets_dir = memory_root_for_workspace(workspace) / "assets"
    local_report = sync_assets_directory_with_report(
        default_milvus_db_path(workspace),
        local_assets_dir,
        prune=args.prune,
    )
    shared_report = {"synced": 0, "pruned": 0}
    if args.include_shared:
        shared_report = sync_assets_directory_with_report(
            shared_milvus_db_path(),
            shared_memory_root() / "assets",
            prune=args.prune,
        )
    _print_json(
        {
            "milvus_available": milvus_available(),
            "workspace": str(workspace),
            "local_synced": local_report["synced"],
            "local_pruned": local_report["pruned"],
            "shared_synced": shared_report["synced"],
            "shared_pruned": shared_report["pruned"],
            "prune": args.prune,
            "local_milvus_db": str(default_milvus_db_path(workspace)),
            "shared_milvus_db": str(shared_milvus_db_path()) if args.include_shared else None,
        }
    )
    return 0


def _handle_activate(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    assets_dir = Path(args.assets_dir) if args.assets_dir else workspace / ".agent-memory" / "assets"
    candidates_dir = (
        Path(args.candidates_dir)
        if args.candidates_dir
        else workspace / ".agent-memory" / "candidates"
    )
    view = activate_assets(
        task=args.task,
        workspace=workspace,
        constraints=args.constraints,
        assets_dir=assets_dir,
        candidates_dir=candidates_dir,
        db_path=db_path,
    )
    output_path = Path(args.output) if args.output else default_activation_view_path(workspace, view)
    save_json(output_path, view)
    log_activation(db_path, view)
    touch_assets_last_used(
        db_path,
        [item["asset_id"] for item in view.get("selected_assets", [])],
        view["created_at"],
    )
    _print_json({"saved_to": str(output_path), "activation_id": view["activation_id"], "activation_view": view})
    return 0


def _handle_explain(args: argparse.Namespace) -> int:
    payload = load_json(Path(args.input))
    _print_json(explain_object(payload))
    return 0


def _handle_review_candidates(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    action_result = _apply_review_candidate_action(args=args, workspace=workspace, db_path=db_path)
    statuses = tuple(args.statuses or DEFAULT_REVIEW_QUEUE_STATUSES)
    candidates = list_candidates(
        db_path,
        workspace=str(workspace),
        statuses=statuses,
    )
    if not candidates:
        candidates_dir = memory_root_for_workspace(workspace) / "candidates"
        candidates = [
            candidate
            for candidate in iter_json_objects(candidates_dir) or []
            if candidate.get("status") in statuses
        ]
    queue = build_candidate_review_queue(candidates, workspace=str(workspace))
    output_path = (
        Path(args.output)
        if args.output
        else memory_root_for_workspace(workspace) / "reviews" / "candidate_review_queue.json"
    )
    save_json(output_path, queue)
    _print_json(
        {
            "saved_to": str(output_path),
            "candidate_count": queue["candidate_count"],
            "review_queue": queue,
            "action_result": action_result,
        }
    )
    return 0


def _build_status_payload(
    *,
    workspace: Path,
    limit: int,
    deep_retrieval_check: bool,
    feedback_cleanup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    memory_root = memory_root_for_workspace(workspace)
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    backend_config = resolve_backend_config()
    project_activity = load_project_policy(workspace)

    assets = list_assets(db_path, workspace=str(workspace))
    candidates = list_candidates(
        db_path,
        workspace=str(workspace),
        statuses=ALL_CANDIDATE_STATUSES,
    )
    activations = list_activation_logs(db_path, workspace=str(workspace))
    traces = list(iter_json_objects(memory_root / "traces" / "bundles"))
    episodes = list(iter_json_objects(memory_root / "episodes"))

    feedback_summary = _summarize_activation_feedback(activations)
    unresolved_activations = _build_unresolved_activation_items(activations, limit=limit)

    temperature_summary = {"hot": 0, "warm": 0, "neutral": 0, "cool": 0}
    review_status_summary = {"healthy": 0, "watch": 0, "needs_review": 0, "unproven": 0}
    for asset in assets:
        temperature_summary[asset.get("temperature", "neutral")] = (
            temperature_summary.get(asset.get("temperature", "neutral"), 0) + 1
        )
        review_status_summary[asset.get("review_status", "unproven")] = (
            review_status_summary.get(asset.get("review_status", "unproven"), 0) + 1
        )
    asset_review_backlog = _build_asset_review_backlog(
        review_status_summary,
        total_assets=len(assets),
    )

    candidate_status_summary = {status: 0 for status in ALL_CANDIDATE_STATUSES}
    for candidate in candidates:
        candidate_status_summary[candidate.get("status", "new")] = (
            candidate_status_summary.get(candidate.get("status", "new"), 0) + 1
        )

    review_queue = build_candidate_review_queue(
        [
            candidate
            for candidate in candidates
            if candidate.get("status") in DEFAULT_REVIEW_QUEUE_STATUSES
        ],
        workspace=str(workspace),
    )

    recent_assets = [
        {
            "asset_id": asset.get("asset_id"),
            "title": asset.get("title"),
            "knowledge_scope": asset.get("knowledge_scope", "project"),
            "knowledge_kind": asset.get("knowledge_kind", asset.get("asset_type", "pattern")),
            "temperature": asset.get("temperature", "neutral"),
            "review_status": asset.get("review_status", "unproven"),
            "updated_at": asset.get("updated_at") or asset.get("created_at"),
        }
        for asset in sorted(
            assets,
            key=lambda item: item.get("updated_at") or item.get("created_at") or "",
            reverse=True,
        )[:limit]
    ]
    recent_candidates = [
        {
            "candidate_id": candidate.get("candidate_id"),
            "title": candidate.get("title"),
            "status": candidate.get("status"),
            "promotion_readiness": candidate.get("promotion_readiness"),
            "help_signal": candidate.get("promotion_feedback", {}).get("help_signal"),
            "updated_at": candidate.get("updated_at") or candidate.get("created_at"),
            "created_at": candidate.get("created_at"),
        }
        for candidate in sorted(
            candidates,
            key=lambda item: item.get("updated_at") or item.get("created_at") or "",
            reverse=True,
        )[:limit]
    ]
    recent_activations = [
        {
            "activation_id": activation.get("activation_id"),
            "task_query": activation.get("task_query"),
            "selected_count": len(activation.get("selected_assets", [])),
            "help_signal": activation.get("feedback", {}).get("help_signal"),
            "created_at": activation.get("created_at"),
        }
        for activation in activations[:limit]
    ]
    local_milvus = milvus_backend_summary(
        default_milvus_db_path(workspace),
        deep_check=deep_retrieval_check,
    )
    shared_milvus = milvus_backend_summary(
        shared_milvus_db_path(),
        deep_check=deep_retrieval_check,
    )
    milvus_indexed_entities = local_milvus.get("indexed_entities")
    possible_stale_entities = (
        max(milvus_indexed_entities - len(assets), 0)
        if isinstance(milvus_indexed_entities, int)
        else None
    )
    milvus_asset_coverage = (
        round(min(float(milvus_indexed_entities), float(len(assets))) / len(assets), 4)
        if isinstance(milvus_indexed_entities, int) and assets
        else None
    )

    return {
        "workspace": str(workspace),
        "generated_at": now_utc(),
        "backend_configuration": backend_config,
        "project_activity": project_activity,
        "feedback_cleanup": feedback_cleanup
        or {
            "auto_resolved_count": 0,
            "auto_resolved_activation_ids": [],
            "resolution_help_signal": STALE_FEEDBACK_HELP_SIGNAL,
            "pending_hours": _feedback_pending_hours(),
        },
        "storage_layout": storage_layout_for_workspace(workspace),
        "retrieval_backends": {
            "sqlite": {
                "backend": "sqlite",
                "role": "lightweight-state-index",
                "core_retrieval": False,
                "available": True,
                "db_path": str(db_path),
                "db_exists": db_path.exists(),
                "asset_rows": len(assets),
                "candidate_rows": len(candidates),
                "activation_log_rows": len(activations),
            },
            "milvus": {
                "role": "core-semantic-retrieval",
                "core_retrieval": backend_config["retrieval"] in {"milvus-lite", "milvus"},
                "available": milvus_available(),
                "local": local_milvus,
                "shared": shared_milvus,
                "asset_coverage": {
                    "indexed_entities": milvus_indexed_entities,
                    "asset_rows": len(assets),
                    "coverage_ratio": milvus_asset_coverage,
                    "possible_stale_entities": possible_stale_entities,
                    "deep_check_required": milvus_asset_coverage is None,
                },
            },
        },
        "counts": {
            "traces": len(traces),
            "episodes": len(episodes),
            "candidates": len(candidates),
            "assets": len(assets),
            "activation_logs": len(activations),
        },
        "activation_feedback_summary": feedback_summary,
        "unresolved_activations": unresolved_activations,
        "asset_effectiveness_summary": {
            "temperature": temperature_summary,
            "review_status": review_status_summary,
        },
        "asset_review_backlog": asset_review_backlog,
        "candidate_status_summary": candidate_status_summary,
        "candidate_review_queue": {
            "candidate_count": review_queue["candidate_count"],
            "status_summary": review_queue["status_summary"],
            "top_items": review_queue["items"][:limit],
        },
        "recent_assets": recent_assets,
        "recent_candidates": recent_candidates,
        "recent_activations": recent_activations,
    }


def _diagnostic_check(name: str, status: str, summary: str, recommendation: str | None = None) -> dict[str, Any]:
    payload = {
        "name": name,
        "status": status,
        "summary": summary,
    }
    if recommendation:
        payload["recommendation"] = recommendation
    return payload


def _build_doctor_payload(
    *,
    workspace: Path,
    limit: int,
    deep_retrieval_check: bool,
    feedback_cleanup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status_payload = _build_status_payload(
        workspace=workspace,
        limit=limit,
        deep_retrieval_check=deep_retrieval_check,
        feedback_cleanup=feedback_cleanup,
    )
    memory_root = memory_root_for_workspace(workspace)
    local_milvus_path = default_milvus_db_path(workspace)
    shared_milvus_path = shared_milvus_db_path()
    local_lock = milvus_lock_summary(local_milvus_path)
    shared_lock = milvus_lock_summary(shared_milvus_path)

    checks: list[dict[str, Any]] = []
    counts = status_payload["counts"]
    sqlite_backend = status_payload["retrieval_backends"]["sqlite"]
    milvus_backend = status_payload["retrieval_backends"]["milvus"]
    feedback = status_payload["activation_feedback_summary"]
    unresolved_activations = status_payload["unresolved_activations"]
    queue = status_payload["candidate_review_queue"]
    asset_health = status_payload["asset_effectiveness_summary"]["review_status"]
    asset_backlog = status_payload["asset_review_backlog"]

    checks.append(
        _diagnostic_check(
            "sqlite_index",
            "pass" if sqlite_backend["db_exists"] else "fail",
            f"SQLite index has {sqlite_backend['asset_rows']} assets, {sqlite_backend['candidate_rows']} candidates, and {sqlite_backend['activation_log_rows']} activation logs.",
            None if sqlite_backend["db_exists"] else "Run any expcap command with --workspace to initialize the local index.",
        )
    )
    checks.append(
        _diagnostic_check(
            "candidate_review_queue",
            "pass" if queue["candidate_count"] == 0 else "warn",
            f"Candidate review queue has {queue['candidate_count']} pending items.",
            None if queue["candidate_count"] == 0 else "Run expcap review-candidates and approve, reject, or promote the top items.",
        )
    )
    checks.append(
        _diagnostic_check(
            "asset_review_health",
            "pass" if asset_health.get("needs_review", 0) == 0 else "warn",
            f"Asset review status: healthy={asset_health.get('healthy', 0)}, watch={asset_health.get('watch', 0)}, needs_review={asset_health.get('needs_review', 0)}, unproven={asset_health.get('unproven', 0)}.",
            None if asset_health.get("needs_review", 0) == 0 else "Review needs_review assets before allowing them to dominate activation.",
        )
    )
    unproven_count = int(asset_backlog.get("unproven_count", 0) or 0)
    unproven_ratio = float(asset_backlog.get("unproven_ratio", 0.0) or 0.0)
    unproven_warn = unproven_count >= 10 and unproven_ratio >= 0.4
    checks.append(
        _diagnostic_check(
            "asset_proof_coverage",
            "warn" if unproven_warn else "pass",
            f"Asset proof coverage: {asset_backlog.get('healthy_count', 0)}/{asset_backlog.get('total_assets', 0)} healthy, {unproven_count} unproven ({unproven_ratio:.0%}).",
            None
            if not unproven_warn
            else "Promote proof for recurring hot assets or prune low-value unproven assets so review coverage stays credible.",
        )
    )
    missing = int(feedback.get("missing", 0) or 0)
    pending = int(feedback.get("pending", 0) or 0)
    oldest_unresolved = unresolved_activations[0] if unresolved_activations else None
    oldest_unresolved_hint = ""
    if oldest_unresolved:
        oldest_unresolved_hint = (
            f" Oldest unresolved: {oldest_unresolved.get('activation_id')} "
            f"({oldest_unresolved.get('state')}, age_hours={oldest_unresolved.get('age_hours')})."
        )
    checks.append(
        _diagnostic_check(
            "activation_feedback",
            "pass" if missing == 0 else "warn",
            f"Activation feedback: strong={feedback.get('supported_strong', 0)}, weak={feedback.get('supported_weak', 0)}, pending={pending}, stale_missing={missing}.{oldest_unresolved_hint}",
            None if missing == 0 else "Finish or annotate older unresolved activations so help-rate metrics stay meaningful.",
        )
    )

    local_milvus = milvus_backend["local"]
    local_milvus_status = "pass" if local_milvus["status"] == "ready" else "warn"
    checks.append(
        _diagnostic_check(
            "local_milvus",
            local_milvus_status,
            f"Local Milvus Lite is {local_milvus['status']} ({local_milvus.get('degraded_reason') or 'no degraded reason'}).",
            None
            if local_milvus_status == "pass"
            else "If it remains locked, stop the stale process or switch retrieval to a shared/cloud Milvus backend.",
        )
    )
    if local_lock["locked"]:
        checks.append(
            _diagnostic_check(
                "local_milvus_lock",
                "warn",
                f"Local Milvus lock is held at {local_lock['lock_path']} with metadata: {local_lock['metadata_raw'] or 'empty'}.",
                "Do not delete the lock while a live process owns it. If pid_exists is false, a future reset command can safely remove it.",
            )
        )

    severity_order = {"fail": 2, "warn": 1, "pass": 0}
    overall_status = "pass"
    if any(check["status"] == "fail" for check in checks):
        overall_status = "fail"
    elif any(check["status"] == "warn" for check in checks):
        overall_status = "warn"

    recommendations = [
        check["recommendation"]
        for check in checks
        if check.get("recommendation")
    ]
    recommendations = list(dict.fromkeys(recommendations))

    return {
        "workspace": str(workspace),
        "generated_at": now_utc(),
        "overall_status": overall_status,
        "checks": sorted(checks, key=lambda item: severity_order[item["status"]], reverse=True),
        "recommendations": recommendations,
        "milvus_locks": {
            "local": local_lock,
            "shared": shared_lock,
        },
        "status": status_payload,
        "memory_root": str(memory_root),
        "local_milvus_db": str(local_milvus_path),
        "shared_milvus_db": str(shared_milvus_path),
        "counts": counts,
    }


def _handle_status(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    memory_root = memory_root_for_workspace(workspace)
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    feedback_cleanup = _auto_resolve_stale_activation_feedback(
        workspace=workspace,
        db_path=db_path,
    )
    payload = _build_status_payload(
        workspace=workspace,
        limit=args.limit,
        deep_retrieval_check=args.deep_retrieval_check,
        feedback_cleanup=feedback_cleanup,
    )
    output_path = (
        Path(args.output)
        if args.output
        else memory_root / "reviews" / "workspace_status.json"
    )
    save_json(output_path, payload)
    _print_json({"saved_to": str(output_path), "status": payload})
    return 0


def _handle_doctor(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    memory_root = memory_root_for_workspace(workspace)
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    feedback_cleanup = _auto_resolve_stale_activation_feedback(
        workspace=workspace,
        db_path=db_path,
    )
    payload = _build_doctor_payload(
        workspace=workspace,
        limit=args.limit,
        deep_retrieval_check=args.deep_retrieval_check,
        feedback_cleanup=feedback_cleanup,
    )
    output_path = (
        Path(args.output)
        if args.output
        else memory_root / "reviews" / "doctor.json"
    )
    save_json(output_path, payload)
    _print_json({"saved_to": str(output_path), "doctor": payload})
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "ingest":
        return _handle_ingest(args)
    if args.command == "auto-start":
        return _handle_auto_start(args)
    if args.command == "auto-finish":
        return _handle_auto_finish(args)
    if args.command == "install-project":
        return _handle_install_project(args)
    if args.command == "sync-milvus":
        return _handle_sync_milvus(args)
    if args.command == "review":
        return _handle_review(args)
    if args.command == "extract":
        return _handle_extract(args)
    if args.command == "promote":
        return _handle_promote(args)
    if args.command == "activate":
        return _handle_activate(args)
    if args.command == "explain":
        return _handle_explain(args)
    if args.command == "review-candidates":
        return _handle_review_candidates(args)
    if args.command == "status":
        return _handle_status(args)
    if args.command == "doctor":
        return _handle_doctor(args)

    parser.error(f"unknown command: {args.command}")
    return 2


def entrypoint() -> None:
    raise SystemExit(main())
