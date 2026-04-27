import argparse
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
import json
import os
from pathlib import Path
import re
import tempfile
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
    legacy_milvus_db_path,
    legacy_shared_milvus_db_path,
    default_trace_bundle_path,
    iter_json_objects,
    load_json,
    memory_root_for_workspace,
    save_json,
    default_shared_asset_path,
    project_storage_key,
    shared_db_path,
    shared_memory_root,
    shared_milvus_db_path,
    storage_layout_for_workspace,
    workspace_from_payload,
)
from runtime.storage.embeddings import embedding_provider_config
from runtime.storage.milvus_store import (
    milvus_available,
    milvus_backend_summary,
    milvus_lock_summary,
    search_asset_vectors,
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


def _asset_validation_priority(asset: dict[str, Any]) -> float:
    confidence = float(asset.get("confidence", 0.0) or 0.0)
    kind = str(asset.get("knowledge_kind", asset.get("asset_type", "pattern")) or "pattern")
    kind_bonus = 0.08 if kind == "pattern" else 0.05 if kind == "context" else 0.0
    updated_at = _parse_datetime(asset.get("updated_at") or asset.get("created_at"))
    recency_bonus = 0.0
    if updated_at:
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_days = max((datetime.now(timezone.utc) - updated_at).total_seconds() / 86400.0, 0.0)
        recency_bonus = max(0.0, 0.2 - min(age_days, 30.0) * 0.01)
    return round(confidence + kind_bonus + recency_bonus, 4)


def _validation_tokens(*values: str) -> list[str]:
    tokens: list[str] = []
    for value in values:
        if not value:
            continue
        tokens.extend(
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]{3,}", value)
            if len(token) >= 3 and not token.isdigit()
        )
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _recent_validation_topics(activations: list[dict[str, Any]], *, limit: int = 8) -> list[str]:
    topics: list[str] = []
    for activation in activations[: max(limit, 0)]:
        topics.extend(_validation_tokens(str(activation.get("task_query") or "")))
    return topics[:24]


def _build_unproven_validation_queue(
    assets: list[dict[str, Any]],
    *,
    activations: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    recent_topics = _recent_validation_topics(activations)
    queue_items: list[dict[str, Any]] = []
    for asset in assets:
        if asset.get("review_status", "unproven") != "unproven":
            continue
        title = str(asset.get("title") or "")
        content = str(asset.get("content") or "")
        topic_hits = [token for token in recent_topics if token in title.lower() or token in content.lower()]
        relevance_bonus = round(min(len(topic_hits), 4) * 0.08, 4)
        priority_score = round(_asset_validation_priority(asset) + relevance_bonus, 4)
        queue_items.append(
            {
                "asset_id": asset.get("asset_id"),
                "title": asset.get("title"),
                "knowledge_kind": asset.get("knowledge_kind", asset.get("asset_type", "pattern")),
                "knowledge_scope": asset.get("knowledge_scope", "project"),
                "confidence": asset.get("confidence"),
                "updated_at": asset.get("updated_at") or asset.get("created_at"),
                "priority_score": priority_score,
                "recent_topic_hits": topic_hits[:6],
                "validation_hint": (
                    f"Recent task overlap: {', '.join(topic_hits[:3])}."
                    if topic_hits
                    else "Needs first real activation with explicit help feedback."
                ),
            }
        )
    queue_items.sort(
        key=lambda item: (
            -(float(item.get("priority_score", 0.0) or 0.0)),
            item.get("updated_at") or "",
            item.get("asset_id") or "",
        )
    )
    return {
        "asset_count": len(queue_items),
        "recent_topics": recent_topics,
        "top_items": queue_items[:limit],
    }


def _summarize_milvus_retrieval_effectiveness(activations: list[dict[str, Any]]) -> dict[str, Any]:
    activation_count = len(activations)
    with_milvus_candidates = 0
    with_milvus_selected = 0
    selected_from_milvus = 0
    selected_total = 0
    project_candidates = 0
    shared_candidates = 0
    vector_scores: list[float] = []

    for activation in activations:
        retrieval_summary = activation.get("retrieval_summary") or {}
        project_candidate_count = int(retrieval_summary.get("milvus_project_candidates", 0) or 0)
        shared_candidate_count = int(retrieval_summary.get("milvus_shared_candidates", 0) or 0)
        milvus_selected_count = int(retrieval_summary.get("selected_from_milvus", 0) or 0)
        selected_count = len(activation.get("selected_assets", []))

        project_candidates += project_candidate_count
        shared_candidates += shared_candidate_count
        selected_from_milvus += milvus_selected_count
        selected_total += selected_count
        if project_candidate_count or shared_candidate_count:
            with_milvus_candidates += 1
        if milvus_selected_count:
            with_milvus_selected += 1

        for asset in activation.get("selected_assets", []):
            if "milvus" not in asset.get("retrieval_sources", []):
                continue
            try:
                vector_scores.append(float(asset.get("vector_score", 0.0) or 0.0))
            except (TypeError, ValueError):
                continue

    denominator = selected_total if selected_total > 0 else 1
    activation_denominator = activation_count if activation_count > 0 else 1
    return {
        "activation_count": activation_count,
        "activations_with_milvus_candidates": with_milvus_candidates,
        "activations_with_milvus_selected": with_milvus_selected,
        "milvus_project_candidates": project_candidates,
        "milvus_shared_candidates": shared_candidates,
        "selected_from_milvus": selected_from_milvus,
        "selected_total": selected_total,
        "milvus_selected_ratio": round(selected_from_milvus / denominator, 4) if selected_total else 0.0,
        "activation_selected_ratio": round(with_milvus_selected / activation_denominator, 4) if activation_count else 0.0,
        "avg_selected_vector_score": round(sum(vector_scores) / len(vector_scores), 4) if vector_scores else 0.0,
        "max_selected_vector_score": round(max(vector_scores), 4) if vector_scores else 0.0,
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

    feedback = subparsers.add_parser(
        "feedback",
        help="Record help feedback for a stored activation and refresh linked asset effectiveness.",
    )
    feedback.add_argument("--workspace", required=True, help="Workspace path for the activation.")
    feedback.add_argument("--activation-id", help="Activation id to update. Defaults to the latest unresolved activation.")
    feedback.add_argument(
        "--help-signal",
        required=True,
        choices=["supported_strong", "supported_weak", "unclear"],
        help="Help signal to record for the activation.",
    )
    feedback.add_argument(
        "--feedback-summary",
        help="Optional feedback summary. Defaults to a short summary derived from the help signal.",
    )
    feedback.add_argument(
        "--feedback-at",
        help="Optional explicit feedback timestamp. Defaults to now in UTC.",
    )
    feedback.add_argument(
        "--signal-source",
        default="manual_feedback",
        help="Feedback source label. Defaults to manual_feedback.",
    )

    progressive_recall = subparsers.add_parser(
        "progressive-recall",
        help="Conditionally run event-driven delta recall during an ongoing conversation.",
    )
    progressive_recall.add_argument("--workspace", required=True, help="Workspace path for the task.")
    progressive_recall.add_argument("--task", required=True, help="Current task or conversation summary.")
    progressive_recall.add_argument(
        "--message",
        dest="messages",
        action="append",
        default=[],
        help="New user/agent message or conversation delta. May be passed multiple times.",
    )
    progressive_recall.add_argument(
        "--constraint",
        dest="constraints",
        action="append",
        default=[],
        help="Explicit task constraint. May be passed multiple times.",
    )
    progressive_recall.add_argument(
        "--file",
        dest="files",
        action="append",
        default=[],
        help="Newly relevant file path or module. May be passed multiple times.",
    )
    progressive_recall.add_argument(
        "--error",
        dest="errors",
        action="append",
        default=[],
        help="New error or failure signal. May be passed multiple times.",
    )
    progressive_recall.add_argument(
        "--phase",
        choices=["discussion", "implementation", "test", "fix", "review"],
        help="Current task phase. Phase changes can trigger delta recall.",
    )
    progressive_recall.add_argument(
        "--cooldown-minutes",
        type=float,
        default=10.0,
        help="Minimum minutes between non-forced progressive recalls. Defaults to 10.",
    )
    progressive_recall.add_argument(
        "--lookback",
        type=int,
        default=5,
        help="Recent activation count used for drift detection and asset de-duplication. Defaults to 5.",
    )
    progressive_recall.add_argument(
        "--force",
        action="store_true",
        help="Bypass trigger and cooldown checks.",
    )
    progressive_recall.add_argument("--output", help="Optional path for the progressive activation view JSON.")

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

    benchmark_milvus = subparsers.add_parser(
        "benchmark-milvus",
        help="Run a lightweight Milvus retrieval benchmark from recent activations or explicit queries.",
    )
    benchmark_milvus.add_argument("--workspace", required=True, help="Workspace path to benchmark.")
    benchmark_milvus.add_argument(
        "--query",
        dest="queries",
        action="append",
        default=[],
        help="Explicit query to benchmark. May be passed multiple times.",
    )
    benchmark_milvus.add_argument(
        "--sample-size",
        type=int,
        default=10,
        help="Number of recent activation queries to sample when --query is omitted.",
    )
    benchmark_milvus.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Milvus candidates to retrieve per query.",
    )
    benchmark_milvus.add_argument(
        "--include-shared",
        action="store_true",
        help="Also query the shared cross-project Milvus index.",
    )
    benchmark_milvus.add_argument("--output", help="Optional output JSON path.")

    dashboard = subparsers.add_parser(
        "dashboard",
        help="Generate a local read-only HTML dashboard for assets, retrieval, and write activity.",
    )
    dashboard.add_argument("--workspace", required=True, help="Workspace path to summarize.")
    dashboard.add_argument(
        "--limit",
        type=int,
        default=50,
        help="How many recent rows to include in dashboard tables. Defaults to 50.",
    )
    dashboard.add_argument(
        "--days",
        type=int,
        default=14,
        help="How many recent days to include in write-frequency charts. Defaults to 14.",
    )
    dashboard.add_argument(
        "--deep-retrieval-check",
        action="store_true",
        help="Open retrieval backends for deeper health checks. Defaults to lightweight checks only.",
    )
    dashboard.add_argument("--output", help="Optional output HTML path.")

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


def _fallback_activation_view_path(workspace: Path, view: dict[str, Any]) -> Path:
    return (
        Path(tempfile.gettempdir())
        / "expcap-activation-views"
        / project_storage_key(workspace)
        / f"{view['activation_id']}.json"
    )


def _save_activation_view(
    *,
    workspace: Path,
    view: dict[str, Any],
    requested_output: str | None,
) -> tuple[Path, dict[str, Any] | None]:
    output_path = Path(requested_output) if requested_output else default_activation_view_path(workspace, view)
    try:
        save_json(output_path, view)
        return output_path, None
    except OSError as error:
        if requested_output:
            raise
        fallback_path = _fallback_activation_view_path(workspace, view)
        save_json(fallback_path, view)
        return fallback_path, {
            "kind": "fallback_output",
            "reason": "default_activation_view_unwritable",
            "requested_path": str(output_path),
            "fallback_path": str(fallback_path),
            "error": str(error),
        }


def _activation_source_dirs(
    workspace: Path,
    *,
    assets_dir: str | None = None,
    candidates_dir: str | None = None,
) -> tuple[Path, Path]:
    memory_root = memory_root_for_workspace(workspace)
    resolved_assets_dir = Path(assets_dir) if assets_dir else memory_root / "assets"
    resolved_candidates_dir = Path(candidates_dir) if candidates_dir else memory_root / "candidates"
    return resolved_assets_dir, resolved_candidates_dir


def _linked_asset_ids_from_activation(updated_activation: dict[str, Any]) -> list[str]:
    linked_asset_ids = updated_activation.get("selected_asset_ids") or [
        item["asset_id"]
        for item in updated_activation.get("selected_assets", [])
        if isinstance(item, dict) and item.get("asset_id")
    ]
    return [str(asset_id) for asset_id in linked_asset_ids if asset_id]


def _apply_activation_feedback(
    *,
    workspace: Path,
    db_path: Path,
    activation_id: str,
    feedback: dict[str, Any],
) -> dict[str, Any] | None:
    updated_activation = record_activation_feedback(
        db_path,
        activation_id=activation_id,
        feedback=feedback,
    )
    if not updated_activation:
        return None
    _update_activation_view_file(workspace, updated_activation)
    linked_asset_ids = _linked_asset_ids_from_activation(updated_activation)
    feedback_stats = summarize_asset_feedback(
        db_path,
        asset_ids=linked_asset_ids,
    )
    memory_root = memory_root_for_workspace(workspace)
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
            updated_at=feedback.get("feedback_at"),
        )
        upsert_asset(asset_db_path, asset)
        save_json(asset_path, asset)
    return {
        "activation_id": updated_activation["activation_id"],
        "help_signal": feedback["help_signal"],
        "linked_asset_ids": linked_asset_ids,
        "feedback_summary": feedback.get("feedback_summary"),
    }


def _handle_auto_start(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    project_activity = load_project_policy(workspace)
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    feedback_cleanup = _auto_resolve_stale_activation_feedback(
        workspace=workspace,
        db_path=db_path,
    )
    assets_dir, candidates_dir = _activation_source_dirs(workspace)
    view = activate_assets(
        task=args.task,
        workspace=workspace,
        constraints=args.constraints,
        assets_dir=assets_dir,
        candidates_dir=candidates_dir,
        db_path=db_path,
    )
    output_path, save_warning = _save_activation_view(
        workspace=workspace,
        view=view,
        requested_output=args.output,
    )
    log_activation(db_path, view)
    touch_assets_last_used(
        db_path,
        [item["asset_id"] for item in view.get("selected_assets", [])],
        view["created_at"],
    )
    payload = {
        "saved_to": str(output_path),
        "activation_id": view["activation_id"],
        "selected_count": len(view.get("selected_assets", [])),
        "project_activity": project_activity,
        "feedback_cleanup": feedback_cleanup,
        "activation_view": view,
    }
    if save_warning:
        payload["save_warning"] = save_warning
    _print_json(payload)
    return 0


_PROGRESSIVE_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "into",
    "what",
    "when",
    "where",
    "how",
    "why",
    "是否",
    "现在",
    "继续",
    "这个",
    "我们",
    "是不是",
}


def _progressive_tokens(*values: str) -> set[str]:
    joined = " ".join(value for value in values if value)
    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_./:-]{3,}", joined)
        if token.lower() not in _PROGRESSIVE_STOPWORDS and not token.isdigit()
    }
    return tokens


def _activation_asset_ids(activation: dict[str, Any]) -> set[str]:
    selected_ids = activation.get("selected_asset_ids")
    if isinstance(selected_ids, list):
        return {str(asset_id) for asset_id in selected_ids if asset_id}
    return {
        str(item.get("asset_id"))
        for item in activation.get("selected_assets", [])
        if isinstance(item, dict) and item.get("asset_id")
    }


def _activation_created_at(activation: dict[str, Any]) -> datetime | None:
    parsed = _parse_datetime(activation.get("created_at"))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _latest_progressive_phase(activations: list[dict[str, Any]]) -> str | None:
    for activation in activations:
        progressive = activation.get("progressive_recall") or {}
        phase = progressive.get("phase")
        if phase:
            return str(phase)
    return None


def _progressive_trigger_decision(
    *,
    task: str,
    messages: list[str],
    files: list[str],
    errors: list[str],
    phase: str | None,
    recent_activations: list[dict[str, Any]],
    cooldown_minutes: float,
    force: bool,
) -> dict[str, Any]:
    if force:
        return {"triggered": True, "reasons": ["force"], "cooldown_active": False, "novel_tokens": []}

    now = datetime.now(timezone.utc)
    latest_activation = recent_activations[0] if recent_activations else None
    latest_created_at = _activation_created_at(latest_activation or {})
    minutes_since_latest = (
        round((now - latest_created_at).total_seconds() / 60.0, 2)
        if latest_created_at
        else None
    )
    cooldown_active = (
        minutes_since_latest is not None
        and minutes_since_latest < max(cooldown_minutes, 0.0)
    )

    reasons: list[str] = []
    if not recent_activations:
        reasons.append("no_prior_activation")
    if errors:
        reasons.append("new_error_signal")
    if files:
        reasons.append("file_scope_changed")
    if phase and phase != _latest_progressive_phase(recent_activations):
        reasons.append("phase_changed")

    baseline_text = " ".join(str(item.get("task_query") or "") for item in recent_activations)
    incoming_tokens = _progressive_tokens(task, *messages, *files, *errors, phase or "")
    baseline_tokens = _progressive_tokens(baseline_text)
    novel_tokens = sorted(incoming_tokens - baseline_tokens)
    if len(novel_tokens) >= 3:
        reasons.append("topic_drift")

    if cooldown_active and not errors:
        return {
            "triggered": False,
            "reasons": reasons,
            "skip_reason": "cooldown_active",
            "cooldown_active": True,
            "minutes_since_latest": minutes_since_latest,
            "cooldown_minutes": cooldown_minutes,
            "novel_tokens": novel_tokens[:12],
        }
    if not reasons:
        return {
            "triggered": False,
            "reasons": [],
            "skip_reason": "no_new_signal",
            "cooldown_active": False,
            "minutes_since_latest": minutes_since_latest,
            "cooldown_minutes": cooldown_minutes,
            "novel_tokens": novel_tokens[:12],
        }
    return {
        "triggered": True,
        "reasons": list(dict.fromkeys(reasons)),
        "cooldown_active": False,
        "minutes_since_latest": minutes_since_latest,
        "cooldown_minutes": cooldown_minutes,
        "novel_tokens": novel_tokens[:12],
    }


def _progressive_query_text(args: argparse.Namespace) -> str:
    parts = [args.task]
    parts.extend(args.messages)
    if args.phase:
        parts.append(f"phase:{args.phase}")
    parts.extend(f"file:{item}" for item in args.files)
    parts.extend(f"error:{item}" for item in args.errors)
    return " | ".join(part for part in parts if part)


def _progressive_delta_retrieval_summary(
    *,
    original_summary: dict[str, Any],
    delta_assets: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = dict(original_summary)
    summary["selected_from_milvus"] = sum(
        1 for asset in delta_assets if "milvus" in asset.get("retrieval_sources", [])
    )
    summary["selected_from_sqlite"] = sum(
        1 for asset in delta_assets if "sqlite" in asset.get("retrieval_sources", [])
    )
    summary["selected_from_json"] = sum(
        1
        for asset in delta_assets
        if "json" in asset.get("retrieval_sources", [])
        or "shared-json" in asset.get("retrieval_sources", [])
    )
    summary["selected_from_candidate_fallback"] = sum(
        1 for asset in delta_assets if "candidate-fallback" in asset.get("retrieval_sources", [])
    )
    return summary


def _handle_progressive_recall(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    recent_activations = list_activation_logs(
        db_path,
        workspace=str(workspace),
        limit=max(args.lookback, 1),
    )
    decision = _progressive_trigger_decision(
        task=args.task,
        messages=args.messages,
        files=args.files,
        errors=args.errors,
        phase=args.phase,
        recent_activations=recent_activations,
        cooldown_minutes=args.cooldown_minutes,
        force=args.force,
    )
    if not decision["triggered"]:
        _print_json(
            {
                "triggered": False,
                "workspace": str(workspace),
                "decision": decision,
                "selected_count": 0,
                "activation_view": None,
            }
        )
        return 0

    previously_seen_asset_ids: set[str] = set()
    for activation in recent_activations:
        previously_seen_asset_ids.update(_activation_asset_ids(activation))

    assets_dir, candidates_dir = _activation_source_dirs(workspace)
    view = activate_assets(
        task=_progressive_query_text(args),
        workspace=workspace,
        constraints=args.constraints,
        assets_dir=assets_dir,
        candidates_dir=candidates_dir,
        db_path=db_path,
    )
    original_selected_assets = list(view.get("selected_assets", []))
    delta_assets = [
        asset
        for asset in original_selected_assets
        if asset.get("asset_id") not in previously_seen_asset_ids
    ]
    view["selected_assets"] = delta_assets
    view["selected_asset_ids"] = [
        asset["asset_id"]
        for asset in delta_assets
        if isinstance(asset, dict) and asset.get("asset_id")
    ]
    view["retrieval_summary"] = _progressive_delta_retrieval_summary(
        original_summary=view.get("retrieval_summary") or {},
        delta_assets=delta_assets,
    )
    view["activation_id"] = f"{view['activation_id']}-progressive"
    view["progressive_recall"] = {
        "kind": "event_driven_delta",
        "trigger_reasons": decision["reasons"],
        "phase": args.phase,
        "cooldown_minutes": args.cooldown_minutes,
        "lookback": args.lookback,
        "novel_tokens": decision.get("novel_tokens", []),
        "deduped_asset_count": len(original_selected_assets) - len(delta_assets),
        "previous_activation_count": len(recent_activations),
    }
    view["why_selected"] = [
        "progressive recall only runs when new conversation signals justify a delta search",
        "already activated assets are de-duplicated from the returned delta",
        *view.get("why_selected", []),
    ]

    output_path, save_warning = _save_activation_view(
        workspace=workspace,
        view=view,
        requested_output=args.output,
    )
    log_activation(db_path, view)
    touch_assets_last_used(
        db_path,
        [item["asset_id"] for item in view.get("selected_assets", [])],
        view["created_at"],
    )
    payload = {
        "triggered": True,
        "saved_to": str(output_path),
        "activation_id": view["activation_id"],
        "selected_count": len(delta_assets),
        "deduped_asset_count": view["progressive_recall"]["deduped_asset_count"],
        "decision": decision,
        "activation_view": view,
    }
    if save_warning:
        payload["save_warning"] = save_warning
    _print_json(payload)
    return 0


def _handle_feedback(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    activation = None
    activation_id = args.activation_id
    if activation_id:
        activation = next(
            (
                item
                for item in list_activation_logs(db_path, workspace=str(workspace))
                if item.get("activation_id") == activation_id
            ),
            None,
        )
    else:
        activation = find_latest_activation(
            db_path,
            workspace=str(workspace),
            unresolved_only=True,
        )
        activation_id = activation.get("activation_id") if activation else None
    if not activation or not activation_id:
        _print_json(
            {
                "updated": False,
                "workspace": str(workspace),
                "activation_feedback": None,
                "reason": "activation_not_found",
            }
        )
        return 0

    feedback = {
        "help_signal": args.help_signal,
        "signal_source": args.signal_source,
        "feedback_summary": args.feedback_summary or f"Recorded {args.help_signal} via expcap feedback command.",
        "feedback_at": args.feedback_at or now_utc(),
    }
    activation_feedback = _apply_activation_feedback(
        workspace=workspace,
        db_path=db_path,
        activation_id=activation_id,
        feedback=feedback,
    )
    _print_json(
        {
            "updated": bool(activation_feedback),
            "workspace": str(workspace),
            "activation_feedback": activation_feedback,
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
        activation_feedback = _apply_activation_feedback(
            workspace=workspace,
            db_path=db_path,
            activation_id=latest_activation["activation_id"],
            feedback=feedback,
        )

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
            "embedding_profile": embedding_provider_config()["profile"],
            "local_milvus_db": str(default_milvus_db_path(workspace)),
            "local_legacy_milvus_db": str(legacy_milvus_db_path(workspace)),
            "shared_milvus_db": str(shared_milvus_db_path()) if args.include_shared else None,
            "shared_legacy_milvus_db": str(legacy_shared_milvus_db_path()) if args.include_shared else None,
        }
    )
    return 0


def _activation_expected_asset_ids(activation: dict[str, Any]) -> list[str]:
    selected_ids = activation.get("selected_asset_ids", [])
    if selected_ids:
        return [str(asset_id) for asset_id in selected_ids if asset_id]
    return [
        str(item["asset_id"])
        for item in activation.get("selected_assets", [])
        if isinstance(item, dict) and item.get("asset_id")
    ]


def _benchmark_samples_from_inputs(
    *,
    db_path: Path,
    workspace: Path,
    queries: list[str],
    sample_size: int,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    for query in queries:
        clean_query = query.strip()
        if not clean_query or clean_query in seen_queries:
            continue
        seen_queries.add(clean_query)
        samples.append(
            {
                "query": clean_query,
                "source": "explicit",
                "source_activation_id": None,
                "expected_asset_ids": [],
            }
        )

    if samples:
        return samples

    for activation in list_activation_logs(db_path, workspace=str(workspace), limit=max(sample_size * 3, sample_size)):
        query = str(activation.get("task_query") or "").strip()
        if not query or query in seen_queries:
            continue
        seen_queries.add(query)
        samples.append(
            {
                "query": query,
                "source": "activation_log",
                "source_activation_id": activation.get("activation_id"),
                "expected_asset_ids": _activation_expected_asset_ids(activation),
            }
        )
        if len(samples) >= sample_size:
            break
    return samples


def _score_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"avg": 0.0, "max": 0.0}
    return {
        "avg": round(sum(values) / len(values), 4),
        "max": round(max(values), 4),
    }


def _build_milvus_benchmark_payload(
    *,
    workspace: Path,
    queries: list[str],
    sample_size: int,
    limit: int,
    include_shared: bool,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    bounded_sample_size = max(sample_size, 0)
    bounded_limit = max(limit, 1)
    local_sync_report = sync_assets_directory_with_report(
        default_milvus_db_path(workspace),
        memory_root_for_workspace(workspace) / "assets",
    )
    shared_sync_report = {"synced": 0, "pruned": 0}
    if include_shared:
        shared_sync_report = sync_assets_directory_with_report(
            shared_milvus_db_path(),
            shared_memory_root() / "assets",
        )
    samples = _benchmark_samples_from_inputs(
        db_path=db_path,
        workspace=workspace,
        queries=queries,
        sample_size=bounded_sample_size,
    )

    benchmark_items: list[dict[str, Any]] = []
    top_scores: list[float] = []
    result_counts: list[int] = []
    hit_sample_count = 0
    comparable_sample_count = 0

    for sample in samples:
        local_results = search_asset_vectors(
            default_milvus_db_path(workspace),
            query_text=sample["query"],
            limit=bounded_limit,
            knowledge_scope="project",
            workspace=str(workspace),
        )
        shared_results: list[dict[str, Any]] = []
        if include_shared:
            shared_results = search_asset_vectors(
                shared_milvus_db_path(),
                query_text=sample["query"],
                limit=bounded_limit,
                knowledge_scope="cross-project",
            )
        results = [
            {**item, "milvus_index": "local"}
            for item in local_results
        ] + [
            {**item, "milvus_index": "shared"}
            for item in shared_results
        ]
        results = sorted(results, key=lambda item: float(item.get("vector_score", 0.0) or 0.0), reverse=True)[
            :bounded_limit
        ]
        result_ids = [str(item.get("asset_id")) for item in results if item.get("asset_id")]
        expected_ids = sample["expected_asset_ids"]
        hits = [asset_id for asset_id in expected_ids if asset_id in result_ids]
        if expected_ids:
            comparable_sample_count += 1
            if hits:
                hit_sample_count += 1
        scores = [float(item.get("vector_score", 0.0) or 0.0) for item in results]
        if scores:
            top_scores.append(scores[0])
        result_counts.append(len(results))
        benchmark_items.append(
            {
                **sample,
                "result_count": len(results),
                "top_score": round(scores[0], 4) if scores else 0.0,
                "hit_asset_ids": hits,
                "hit_count": len(hits),
                "results": [
                    {
                        "asset_id": item.get("asset_id"),
                        "title": item.get("title"),
                        "knowledge_scope": item.get("knowledge_scope"),
                        "knowledge_kind": item.get("knowledge_kind"),
                        "milvus_index": item.get("milvus_index"),
                        "vector_score": round(float(item.get("vector_score", 0.0) or 0.0), 4),
                        "embedding": item.get("embedding"),
                    }
                    for item in results
                ],
            }
        )

    result_count_summary = _score_summary([float(count) for count in result_counts])
    top_score_summary = _score_summary(top_scores)
    return {
        "workspace": str(workspace),
        "generated_at": now_utc(),
        "milvus_available": milvus_available(),
        "embedding": embedding_provider_config(),
        "preflight_sync": {
            "local": local_sync_report,
            "shared": shared_sync_report if include_shared else None,
        },
        "limit": bounded_limit,
        "sample_size": bounded_sample_size,
        "include_shared": include_shared,
        "sample_count": len(benchmark_items),
        "summary": {
            "queries_with_results": sum(1 for item in benchmark_items if item["result_count"] > 0),
            "comparable_queries": comparable_sample_count,
            "queries_with_expected_hit": hit_sample_count,
            "expected_hit_rate": round(hit_sample_count / comparable_sample_count, 4)
            if comparable_sample_count
            else None,
            "avg_result_count": result_count_summary["avg"],
            "avg_top_score": top_score_summary["avg"],
            "max_top_score": top_score_summary["max"],
        },
        "samples": benchmark_items,
    }


def _handle_benchmark_milvus(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    payload = _build_milvus_benchmark_payload(
        workspace=workspace,
        queries=args.queries,
        sample_size=args.sample_size,
        limit=args.limit,
        include_shared=args.include_shared,
    )
    output_path = (
        Path(args.output)
        if args.output
        else memory_root_for_workspace(workspace) / "reviews" / "milvus_benchmark.json"
    )
    save_json(output_path, payload)
    _print_json({"saved_to": str(output_path), "benchmark": payload})
    return 0


def _handle_activate(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    assets_dir, candidates_dir = _activation_source_dirs(
        workspace,
        assets_dir=args.assets_dir,
        candidates_dir=args.candidates_dir,
    )
    view = activate_assets(
        task=args.task,
        workspace=workspace,
        constraints=args.constraints,
        assets_dir=assets_dir,
        candidates_dir=candidates_dir,
        db_path=db_path,
    )
    output_path, save_warning = _save_activation_view(
        workspace=workspace,
        view=view,
        requested_output=args.output,
    )
    log_activation(db_path, view)
    touch_assets_last_used(
        db_path,
        [item["asset_id"] for item in view.get("selected_assets", [])],
        view["created_at"],
    )
    payload = {"saved_to": str(output_path), "activation_id": view["activation_id"], "activation_view": view}
    if save_warning:
        payload["save_warning"] = save_warning
    _print_json(payload)
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
    milvus_effectiveness = _summarize_milvus_retrieval_effectiveness(activations)

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
    unproven_validation_queue = _build_unproven_validation_queue(
        assets,
        activations=activations,
        limit=limit,
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
                "embedding": embedding_provider_config(),
                "legacy_local_path": str(legacy_milvus_db_path(workspace)),
                "legacy_local_exists": legacy_milvus_db_path(workspace).exists(),
                "legacy_shared_path": str(legacy_shared_milvus_db_path()),
                "legacy_shared_exists": legacy_shared_milvus_db_path().exists(),
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
        "milvus_retrieval_effectiveness": milvus_effectiveness,
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
        "unproven_validation_queue": unproven_validation_queue,
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
    milvus_effectiveness = status_payload["milvus_retrieval_effectiveness"]
    feedback = status_payload["activation_feedback_summary"]
    unresolved_activations = status_payload["unresolved_activations"]
    queue = status_payload["candidate_review_queue"]
    asset_health = status_payload["asset_effectiveness_summary"]["review_status"]
    asset_backlog = status_payload["asset_review_backlog"]
    unproven_validation_queue = status_payload["unproven_validation_queue"]

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
    checks.append(
        _diagnostic_check(
            "asset_proof_coverage",
            "pass",
            f"Asset proof coverage: {asset_backlog.get('healthy_count', 0)}/{asset_backlog.get('total_assets', 0)} healthy, {unproven_count} unproven ({unproven_ratio:.0%}).",
        )
    )
    if unproven_validation_queue.get("asset_count", 0):
        top_unproven = unproven_validation_queue.get("top_items", [{}])[0]
        checks.append(
            _diagnostic_check(
                "unproven_validation_queue",
                "pass",
                f"Top unproven validation queue has {unproven_validation_queue.get('asset_count', 0)} assets; highest-priority item is {top_unproven.get('asset_id')} ({top_unproven.get('priority_score')}).",
                "Use the unproven validation queue in status/dashboard to pick the next assets for real-task validation.",
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
    milvus_backend_label = "Hosted Milvus" if local_milvus.get("mode") == "remote" else "Local Milvus Lite"
    milvus_recommendation = (
        "Set EXPCAP_RETRIEVAL_INDEX_URI or switch EXPCAP_RETRIEVAL_BACKEND back to milvus-lite."
        if local_milvus.get("degraded_reason") == "missing_retrieval_index_uri"
        else "Lock metadata points to a dead pid; clear the stale lock or run a reset before retrying Milvus."
        if local_lock.get("stale_hint")
        else "If it remains locked, stop the stale process or switch retrieval to a shared/cloud Milvus backend."
    )
    checks.append(
        _diagnostic_check(
            "local_milvus",
            local_milvus_status,
            f"{milvus_backend_label} is {local_milvus['status']} ({local_milvus.get('degraded_reason') or 'no degraded reason'}).",
            None
            if local_milvus_status == "pass"
            else milvus_recommendation,
        )
    )
    milvus_selected_ratio = float(milvus_effectiveness.get("milvus_selected_ratio", 0.0) or 0.0)
    milvus_activation_ratio = float(milvus_effectiveness.get("activation_selected_ratio", 0.0) or 0.0)
    milvus_contribution_status = (
        "pass"
        if milvus_effectiveness.get("selected_from_milvus", 0) and milvus_activation_ratio >= 0.2
        else "warn"
        if milvus_effectiveness.get("activation_count", 0)
        else "pass"
    )
    checks.append(
        _diagnostic_check(
            "milvus_retrieval_contribution",
            milvus_contribution_status,
            f"Milvus selected {milvus_effectiveness.get('selected_from_milvus', 0)}/{milvus_effectiveness.get('selected_total', 0)} assets ({milvus_selected_ratio:.0%}) across {milvus_effectiveness.get('activations_with_milvus_selected', 0)}/{milvus_effectiveness.get('activation_count', 0)} activations ({milvus_activation_ratio:.0%}); avg selected vector score={milvus_effectiveness.get('avg_selected_vector_score', 0.0)}.",
            None
            if milvus_contribution_status == "pass"
            else "Check sync-milvus coverage and query quality; Milvus is available but not yet contributing enough selected assets.",
        )
    )
    if local_lock["locked"] or local_lock.get("stale_hint"):
        checks.append(
            _diagnostic_check(
                "local_milvus_lock",
                "warn",
                (
                    f"Local Milvus lock metadata points to a stale pid at {local_lock['lock_path']}: "
                    f"{local_lock['metadata_raw'] or 'empty'}."
                    if local_lock.get("stale_hint")
                    else f"Local Milvus lock is held at {local_lock['lock_path']} with metadata: "
                    f"{local_lock['metadata_raw'] or 'empty'}."
                ),
                (
                    "The recorded pid is no longer alive; safe cleanup/reset can remove this stale lock before retrying Milvus."
                    if local_lock.get("stale_hint")
                    else "Do not delete the lock while a live process owns it. If pid_exists is false, a future reset command can safely remove it."
                ),
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


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return html_escape(str(value), quote=True)


def _date_bucket(value: Any) -> str | None:
    parsed = _parse_datetime(str(value)) if value else None
    if parsed is None:
        return None
    return parsed.date().isoformat()


def _count_items_by_day(
    items: list[dict[str, Any]],
    *,
    timestamp_keys: tuple[str, ...],
    days: int,
) -> dict[str, int]:
    bounded_days = max(days, 1)
    today = datetime.now(timezone.utc).date()
    buckets = {
        (today - timedelta(days=offset)).isoformat(): 0
        for offset in range(bounded_days - 1, -1, -1)
    }
    for item in items:
        bucket = None
        for key in timestamp_keys:
            bucket = _date_bucket(item.get(key))
            if bucket:
                break
        if bucket in buckets:
            buckets[bucket] += 1
    return buckets


def _dashboard_item_rows(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    return items[: max(limit, 0)]


def _clamp_ratio(value: float) -> float:
    return max(0.0, min(value, 1.0))


def _build_dashboard_payload(
    *,
    workspace: Path,
    limit: int,
    days: int,
    deep_retrieval_check: bool,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    bounded_limit = max(limit, 1)
    bounded_days = max(days, 1)
    status_payload = _build_status_payload(
        workspace=workspace,
        limit=bounded_limit,
        deep_retrieval_check=deep_retrieval_check,
    )

    assets = sorted(
        list_assets(db_path, workspace=str(workspace)),
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )
    candidates = sorted(
        list_candidates(db_path, workspace=str(workspace), statuses=ALL_CANDIDATE_STATUSES),
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )
    activations = list_activation_logs(db_path, workspace=str(workspace))

    asset_rows = [
        {
            "asset_id": asset.get("asset_id"),
            "title": asset.get("title"),
            "knowledge_kind": asset.get("knowledge_kind", asset.get("asset_type", "pattern")),
            "knowledge_scope": asset.get("knowledge_scope", "project"),
            "temperature": asset.get("temperature", "neutral"),
            "review_status": asset.get("review_status", "unproven"),
            "confidence": asset.get("confidence"),
            "last_used_at": asset.get("last_used_at"),
            "updated_at": asset.get("updated_at") or asset.get("created_at"),
        }
        for asset in _dashboard_item_rows(assets, limit=bounded_limit)
    ]
    candidate_rows = [
        {
            "candidate_id": candidate.get("candidate_id"),
            "title": candidate.get("title"),
            "status": candidate.get("status"),
            "promotion_readiness": candidate.get("promotion_readiness"),
            "help_signal": candidate.get("promotion_feedback", {}).get("help_signal"),
            "updated_at": candidate.get("updated_at") or candidate.get("created_at"),
        }
        for candidate in _dashboard_item_rows(candidates, limit=bounded_limit)
    ]
    unproven_rows = [
        {
            "asset_id": item.get("asset_id"),
            "title": item.get("title"),
            "knowledge_kind": item.get("knowledge_kind"),
            "confidence": item.get("confidence"),
            "priority_score": item.get("priority_score"),
            "recent_topic_hits": item.get("recent_topic_hits"),
            "validation_hint": item.get("validation_hint"),
            "updated_at": item.get("updated_at"),
        }
        for item in status_payload["unproven_validation_queue"].get("top_items", [])
    ]
    activation_rows = [
        {
            "activation_id": activation.get("activation_id"),
            "task_query": activation.get("task_query"),
            "selected_count": len(activation.get("selected_assets", [])),
            "help_signal": activation.get("feedback", {}).get("help_signal"),
            "selected_from_milvus": activation.get("retrieval_summary", {}).get("selected_from_milvus", 0),
            "milvus_project_candidates": activation.get("retrieval_summary", {}).get("milvus_project_candidates", 0),
            "milvus_shared_candidates": activation.get("retrieval_summary", {}).get("milvus_shared_candidates", 0),
            "created_at": activation.get("created_at"),
        }
        for activation in _dashboard_item_rows(activations, limit=bounded_limit)
    ]

    asset_writes = _count_items_by_day(
        assets,
        timestamp_keys=("created_at", "updated_at"),
        days=bounded_days,
    )
    candidate_writes = _count_items_by_day(
        candidates,
        timestamp_keys=("created_at", "updated_at"),
        days=bounded_days,
    )
    activation_writes = _count_items_by_day(
        activations,
        timestamp_keys=("created_at",),
        days=bounded_days,
    )
    write_frequency = [
        {
            "date": day,
            "assets": asset_writes.get(day, 0),
            "candidates": candidate_writes.get(day, 0),
            "activations": activation_writes.get(day, 0),
        }
        for day in asset_writes
    ]
    feedback_summary = status_payload["activation_feedback_summary"]
    supported_count = int(feedback_summary.get("supported_strong", 0) or 0) + int(
        feedback_summary.get("supported_weak", 0) or 0
    )
    resolved_feedback_count = supported_count + int(feedback_summary.get("unclear", 0) or 0) + int(
        feedback_summary.get("missing", 0) or 0
    )
    total_assets = int(status_payload["counts"]["assets"] or 0)
    healthy_assets = int(status_payload["asset_review_backlog"]["healthy_count"] or 0)
    recent_writes = sum(item["assets"] + item["candidates"] + item["activations"] for item in write_frequency)
    asset_quality_ratio = _clamp_ratio(healthy_assets / total_assets) if total_assets else 0.0
    help_rate = _clamp_ratio(supported_count / resolved_feedback_count) if resolved_feedback_count else 0.0
    milvus_contribution_ratio = _clamp_ratio(
        float(status_payload["milvus_retrieval_effectiveness"]["activation_selected_ratio"] or 0.0)
    )
    write_activity_ratio = _clamp_ratio(recent_writes / max(bounded_days, 1) / 5.0)
    overall_score = round(
        (
            asset_quality_ratio * 0.30
            + help_rate * 0.30
            + milvus_contribution_ratio * 0.25
            + write_activity_ratio * 0.15
        )
        * 100
    )
    if overall_score >= 70:
        verdict = "healthy"
    elif overall_score >= 45:
        verdict = "watch"
    else:
        verdict = "early"

    return {
        "workspace": str(workspace),
        "generated_at": now_utc(),
        "limit": bounded_limit,
        "days": bounded_days,
        "status": status_payload,
        "cards": {
            "assets": status_payload["counts"]["assets"],
            "candidates": status_payload["counts"]["candidates"],
            "activation_logs": status_payload["counts"]["activation_logs"],
            "healthy_assets": status_payload["asset_review_backlog"]["healthy_count"],
            "unproven_assets": status_payload["asset_review_backlog"]["unproven_count"],
            "milvus_selected_ratio": status_payload["milvus_retrieval_effectiveness"]["milvus_selected_ratio"],
            "activation_selected_ratio": status_payload["milvus_retrieval_effectiveness"]["activation_selected_ratio"],
            "stale_missing_feedback": status_payload["activation_feedback_summary"]["missing"],
        },
        "effectiveness_snapshot": {
            "overall_score": overall_score,
            "verdict": verdict,
            "asset_quality_ratio": round(asset_quality_ratio, 4),
            "help_rate": round(help_rate, 4),
            "milvus_contribution_ratio": round(milvus_contribution_ratio, 4),
            "write_activity_ratio": round(write_activity_ratio, 4),
            "recent_writes": recent_writes,
            "days": bounded_days,
            "signals": [
                {
                    "label": "Asset quality",
                    "ratio": round(asset_quality_ratio, 4),
                    "value": f"{healthy_assets}/{total_assets} healthy",
                },
                {
                    "label": "Activation help",
                    "ratio": round(help_rate, 4),
                    "value": f"{supported_count}/{resolved_feedback_count} helpful",
                },
                {
                    "label": "Milvus contribution",
                    "ratio": round(milvus_contribution_ratio, 4),
                    "value": f"{milvus_contribution_ratio:.0%} activations",
                },
                {
                    "label": "Write activity",
                    "ratio": round(write_activity_ratio, 4),
                    "value": f"{recent_writes} writes / {bounded_days}d",
                },
            ],
        },
        "write_frequency": write_frequency,
        "assets": asset_rows,
        "candidates": candidate_rows,
        "unproven_validation_queue": status_payload["unproven_validation_queue"],
        "unproven_assets": unproven_rows,
        "activations": activation_rows,
        "review_queue": status_payload["candidate_review_queue"],
        "retrieval": {
            "milvus": status_payload["retrieval_backends"]["milvus"],
            "effectiveness": status_payload["milvus_retrieval_effectiveness"],
        },
        "quality": {
            "asset_effectiveness_summary": status_payload["asset_effectiveness_summary"],
            "asset_review_backlog": status_payload["asset_review_backlog"],
            "activation_feedback_summary": status_payload["activation_feedback_summary"],
        },
    }


def _render_count_cards(payload: dict[str, Any]) -> str:
    cards = payload["cards"]
    items = [
        ("Assets", cards["assets"], "project-owned reusable knowledge"),
        ("Activations", cards["activation_logs"], "recent get attempts"),
        ("Healthy", cards["healthy_assets"], "assets with positive proof"),
        ("Unproven", cards["unproven_assets"], "assets still needing evidence"),
        ("Milvus Selected", f"{cards['milvus_selected_ratio']:.0%}", "selected assets from semantic retrieval"),
        ("Feedback Missing", cards["stale_missing_feedback"], "stale activations without help signal"),
    ]
    return "\n".join(
        f"""
        <section class="card">
          <div class="card-label">{_safe_text(label)}</div>
          <div class="card-value">{_safe_text(value)}</div>
          <div class="card-hint">{_safe_text(hint)}</div>
        </section>
        """
        for label, value, hint in items
    )


def _render_dashboard_table(headers: list[str], rows: list[list[Any]]) -> str:
    header_html = "".join(f"<th>{_safe_text(header)}</th>" for header in headers)
    if not rows:
        body_html = f"<tr><td colspan=\"{len(headers)}\" class=\"empty\">No rows yet.</td></tr>"
    else:
        body_html = "\n".join(
            "<tr>" + "".join(f"<td>{_safe_text(cell)}</td>" for cell in row) + "</tr>"
            for row in rows
        )
    return f"<table><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table>"


def _render_effectiveness_snapshot(payload: dict[str, Any]) -> str:
    snapshot = payload["effectiveness_snapshot"]
    signals = snapshot["signals"]
    score = int(snapshot["overall_score"])
    gauge_width = max(0, min(score, 100)) * 3.6
    signal_rows = []
    for index, signal in enumerate(signals):
        y = 40 + index * 54
        ratio = _clamp_ratio(float(signal.get("ratio", 0.0) or 0.0))
        width = round(ratio * 310, 2)
        signal_rows.append(
            f"""
            <g>
              <text x="420" y="{y}" class="snapshot-label">{_safe_text(signal["label"])}</text>
              <text x="720" y="{y}" class="snapshot-value">{_safe_text(signal["value"])}</text>
              <rect x="420" y="{y + 14}" width="310" height="14" rx="7" class="snapshot-track" />
              <rect x="420" y="{y + 14}" width="{width}" height="14" rx="7" class="snapshot-bar snapshot-bar-{index}" />
            </g>
            """
        )
    return f"""
    <section class="panel snapshot-panel">
      <h2>Effectiveness Snapshot</h2>
      <svg class="snapshot-svg" viewBox="0 0 780 270" role="img" aria-label="expcap effectiveness snapshot">
        <defs>
          <linearGradient id="snapshotGauge" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stop-color="#b95f35" />
            <stop offset="55%" stop-color="#d7a742" />
            <stop offset="100%" stop-color="#0d6b57" />
          </linearGradient>
        </defs>
        <text x="32" y="42" class="snapshot-label">Overall</text>
        <text x="32" y="118" class="snapshot-score">{score}</text>
        <text x="158" y="112" class="snapshot-verdict">{_safe_text(snapshot["verdict"])}</text>
        <rect x="32" y="150" width="360" height="22" rx="11" class="snapshot-track" />
        <rect x="32" y="150" width="{gauge_width}" height="22" rx="11" fill="url(#snapshotGauge)" />
        <text x="32" y="204" class="snapshot-note">One-glance read: quality, actual help, Milvus contribution, and write activity.</text>
        {''.join(signal_rows)}
      </svg>
    </section>
    """


def _render_dashboard_html(payload: dict[str, Any]) -> str:
    retrieval = payload["retrieval"]["effectiveness"]
    quality = payload["quality"]
    raw_json = html_escape(json.dumps(payload, ensure_ascii=False, indent=2), quote=False)
    write_rows = [
        [item["date"], item["assets"], item["candidates"], item["activations"]]
        for item in payload["write_frequency"]
    ]
    asset_rows = [
        [
            item["title"],
            item["knowledge_kind"],
            item["knowledge_scope"],
            item["temperature"],
            item["review_status"],
            item["confidence"],
            item["updated_at"],
        ]
        for item in payload["assets"]
    ]
    activation_rows = [
        [
            item["task_query"],
            item["selected_count"],
            item["selected_from_milvus"],
            item["milvus_project_candidates"],
            item["milvus_shared_candidates"],
            item["help_signal"] or "pending",
            item["created_at"],
        ]
        for item in payload["activations"]
    ]
    candidate_rows = [
        [
            item["title"],
            item["status"],
            item["promotion_readiness"],
            item["help_signal"],
            item["updated_at"],
        ]
        for item in payload["candidates"]
    ]
    queue_items = payload["review_queue"].get("top_items", [])
    queue_rows = [
        [
            item.get("title"),
            item.get("status"),
            item.get("promotion_readiness"),
            item.get("priority_score"),
            item.get("candidate_id"),
        ]
        for item in queue_items
    ]
    unproven_rows = [
        [
            item.get("title"),
            item.get("knowledge_kind"),
            item.get("confidence"),
            item.get("priority_score"),
            item.get("validation_hint"),
            item.get("updated_at"),
        ]
        for item in payload["unproven_assets"]
    ]

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>expcap local dashboard</title>
  <style>
    :root {{
      --bg: #f5f1e8;
      --panel: #fffdf7;
      --ink: #1d241f;
      --muted: #667062;
      --line: #ddd3bf;
      --accent: #0d6b57;
      --accent-soft: #d7eee6;
      --warn: #9a5b00;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background:
        radial-gradient(circle at 12% 8%, rgba(13, 107, 87, 0.16), transparent 32rem),
        linear-gradient(135deg, #f5f1e8 0%, #ebe1cf 100%);
      color: var(--ink);
      font: 15px/1.5 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 36px 22px 60px; }}
    header {{ margin-bottom: 28px; }}
    h1 {{ margin: 0 0 8px; font-size: clamp(2rem, 5vw, 4.2rem); line-height: 0.95; letter-spacing: -0.06em; }}
    h2 {{ margin: 28px 0 12px; font-size: 1.2rem; }}
    .meta {{ color: var(--muted); max-width: 900px; }}
    .cards {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; }}
    .card, .panel {{
      background: rgba(255, 253, 247, 0.86);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 18px 50px rgba(50, 43, 31, 0.08);
    }}
    .card {{ padding: 16px; }}
    .card-label {{ color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em; }}
    .card-value {{ margin-top: 8px; font-size: 2rem; font-weight: 760; letter-spacing: -0.04em; }}
    .card-hint {{ color: var(--muted); font-size: 0.86rem; }}
    .panel {{ padding: 18px; overflow: hidden; }}
    .split {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .metric-line {{ display: flex; justify-content: space-between; gap: 16px; border-bottom: 1px solid var(--line); padding: 8px 0; }}
    .metric-line:last-child {{ border-bottom: 0; }}
    .metric-line strong {{ color: var(--accent); }}
    .snapshot-panel {{ margin: 16px 0; }}
    .snapshot-svg {{ display: block; width: 100%; height: auto; }}
    .snapshot-label {{ fill: var(--muted); font-size: 14px; text-transform: uppercase; letter-spacing: 0.08em; }}
    .snapshot-score {{ fill: var(--ink); font-size: 88px; font-weight: 820; letter-spacing: -0.07em; }}
    .snapshot-verdict {{ fill: var(--accent); font-size: 26px; font-weight: 760; text-transform: uppercase; letter-spacing: 0.04em; }}
    .snapshot-note {{ fill: var(--muted); font-size: 15px; }}
    .snapshot-value {{ fill: var(--ink); font-size: 15px; font-weight: 700; text-anchor: end; }}
    .snapshot-track {{ fill: rgba(13, 107, 87, 0.12); }}
    .snapshot-bar-0 {{ fill: #0d6b57; }}
    .snapshot-bar-1 {{ fill: #337b9b; }}
    .snapshot-bar-2 {{ fill: #d7a742; }}
    .snapshot-bar-3 {{ fill: #b95f35; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 10px 8px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; }}
    tr:hover td {{ background: rgba(13, 107, 87, 0.05); }}
    .empty {{ color: var(--muted); text-align: center; }}
    code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    details {{ margin-top: 24px; }}
    summary {{ cursor: pointer; color: var(--accent); font-weight: 700; }}
    pre {{ white-space: pre-wrap; background: #18211d; color: #ecf6ee; padding: 16px; border-radius: 14px; overflow: auto; }}
    @media (max-width: 980px) {{
      .cards {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .split {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>expcap local dashboard</h1>
    <div class="meta">
      Workspace: <code>{_safe_text(payload["workspace"])}</code><br>
      Generated: {_safe_text(payload["generated_at"])}.
      This dashboard is read-only and uses the same status metrics as the CLI.
    </div>
  </header>

  <section class="cards">
    {_render_count_cards(payload)}
  </section>

  {_render_effectiveness_snapshot(payload)}

  <section class="split">
    <div class="panel">
      <h2>Retrieval Effectiveness</h2>
      <div class="metric-line"><span>Milvus selected assets</span><strong>{_safe_text(retrieval.get("selected_from_milvus"))}/{_safe_text(retrieval.get("selected_total"))}</strong></div>
      <div class="metric-line"><span>Milvus selected ratio</span><strong>{_safe_text(f"{float(retrieval.get('milvus_selected_ratio', 0.0) or 0.0):.0%}")}</strong></div>
      <div class="metric-line"><span>Activation selected ratio</span><strong>{_safe_text(f"{float(retrieval.get('activation_selected_ratio', 0.0) or 0.0):.0%}")}</strong></div>
      <div class="metric-line"><span>Average selected vector score</span><strong>{_safe_text(retrieval.get("avg_selected_vector_score"))}</strong></div>
    </div>
    <div class="panel">
      <h2>Quality Signals</h2>
      <div class="metric-line"><span>Review status</span><strong>{_safe_text(quality["asset_effectiveness_summary"]["review_status"])}</strong></div>
      <div class="metric-line"><span>Temperature</span><strong>{_safe_text(quality["asset_effectiveness_summary"]["temperature"])}</strong></div>
      <div class="metric-line"><span>Activation feedback</span><strong>{_safe_text(quality["activation_feedback_summary"])}</strong></div>
      <div class="metric-line"><span>Candidate queue</span><strong>{_safe_text(payload["review_queue"].get("candidate_count"))}</strong></div>
    </div>
  </section>

  <section class="panel">
    <h2>Write Frequency</h2>
    {_render_dashboard_table(["Date", "Assets", "Candidates", "Activations"], write_rows)}
  </section>

  <section class="panel">
    <h2>Assets</h2>
    {_render_dashboard_table(["Title", "Kind", "Scope", "Temp", "Review", "Confidence", "Updated"], asset_rows)}
  </section>

  <section class="panel">
    <h2>Recent Activations</h2>
    {_render_dashboard_table(["Task", "Selected", "Milvus selected", "Milvus project", "Milvus shared", "Help", "Created"], activation_rows)}
  </section>

  <section class="panel">
    <h2>Candidate Review Queue</h2>
    {_render_dashboard_table(["Title", "Status", "Readiness", "Priority", "Candidate ID"], queue_rows)}
  </section>

  <section class="panel">
    <h2>Unproven Validation Queue</h2>
    {_render_dashboard_table(["Title", "Kind", "Confidence", "Priority", "Validation Hint", "Updated"], unproven_rows)}
  </section>

  <section class="panel">
    <h2>Recent Candidates</h2>
    {_render_dashboard_table(["Title", "Status", "Readiness", "Help", "Updated"], candidate_rows)}
  </section>

  <details>
    <summary>Raw dashboard JSON</summary>
    <pre>{raw_json}</pre>
  </details>
</main>
</body>
</html>
"""


def _handle_dashboard(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    payload = _build_dashboard_payload(
        workspace=workspace,
        limit=args.limit,
        days=args.days,
        deep_retrieval_check=args.deep_retrieval_check,
    )
    output_path = (
        Path(args.output)
        if args.output
        else memory_root_for_workspace(workspace) / "reviews" / "dashboard.html"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_render_dashboard_html(payload), encoding="utf-8")
    data_output_path = output_path.with_suffix(".json")
    save_json(data_output_path, payload)
    _print_json(
        {
            "saved_to": str(output_path),
            "data_saved_to": str(data_output_path),
            "dashboard": {
                "workspace": payload["workspace"],
                "generated_at": payload["generated_at"],
                "cards": payload["cards"],
                "effectiveness_snapshot": payload["effectiveness_snapshot"],
                "review_queue_count": payload["review_queue"]["candidate_count"],
                "unproven_validation_count": payload["unproven_validation_queue"]["asset_count"],
            },
        }
    )
    return 0


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
    if args.command == "feedback":
        return _handle_feedback(args)
    if args.command == "progressive-recall":
        return _handle_progressive_recall(args)
    if args.command == "auto-finish":
        return _handle_auto_finish(args)
    if args.command == "install-project":
        return _handle_install_project(args)
    if args.command == "sync-milvus":
        return _handle_sync_milvus(args)
    if args.command == "benchmark-milvus":
        return _handle_benchmark_milvus(args)
    if args.command == "dashboard":
        return _handle_dashboard(args)
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
