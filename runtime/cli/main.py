import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from html import escape as html_escape
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any

from runtime.backends import resolve_backend_config
from runtime.core.engine import (
    activate_assets,
    apply_asset_effectiveness,
    apply_candidate_promotion_feedback,
    build_candidate_review_queue,
    build_knowledge_kind_summary,
    build_trace_bundle,
    explain_object,
    extract_candidates,
    now_utc,
    promote_candidate,
    review_trace_bundle,
    should_promote_candidate,
)
from runtime.core.hook_activity import load_recent_hook_events
from runtime.core.injection_materializer import materialize_injection_artifacts
from runtime.core.injection_policy import (
    CHANNEL_TO_LAYER,
    CONTINUOUS_RUNTIME_RECALL_INJECTION,
    INJECTION_CHANNELS,
    INJECTION_LAYERS,
    SYSTEM_PROMPT_INJECTION,
    TASK_START_RUNTIME_INJECTION,
)
from runtime.core.knowledge_kinds import (
    CANONICAL_KNOWLEDGE_KINDS,
    CODEMAP,
    CONSTRAINT,
    DECISION_MEMORY,
    DONT_REPEAT,
    HIGH_PRIORITY_PRIOR_KINDS,
    PAST_WIN,
    PREFERENCE,
)
from runtime.core.project_install import (
    INTEGRATION_MODE_CODEX_HOOKS,
    INTEGRATION_MODE_CLAUDE_HOOKS,
    SUPPORTED_INTEGRATION_MODES,
    install_project_agents,
)
from runtime.core.project_policy import (
    DEFAULT_INTEGRATION_MODE,
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


def _activation_injection_channel_counts(activation: dict[str, Any]) -> dict[str, int]:
    counts = {channel: 0 for channel in INJECTION_CHANNELS}
    plan_counts = (activation.get("injection_plan") or {}).get("channel_counts")
    if isinstance(plan_counts, dict):
        for channel in INJECTION_CHANNELS:
            counts[channel] = int(plan_counts.get(channel, 0) or 0)
        return counts

    for asset in activation.get("selected_assets", []):
        channel = asset.get("injection_channel")
        if channel in counts:
            counts[str(channel)] += 1
    return counts


def _activation_injection_layer_counts(activation: dict[str, Any]) -> dict[str, int]:
    counts = {layer: 0 for layer in INJECTION_LAYERS}
    plan_counts = (activation.get("injection_plan") or {}).get("layer_counts")
    if isinstance(plan_counts, dict):
        for layer in INJECTION_LAYERS:
            counts[layer] = int(plan_counts.get(layer, 0) or 0)
        return counts

    for channel, count in _activation_injection_channel_counts(activation).items():
        layer = CHANNEL_TO_LAYER.get(channel)
        if layer:
            counts[layer] += int(count or 0)
    return counts


def _summarize_injection_policy(activations: list[dict[str, Any]]) -> dict[str, Any]:
    channel_counts = {channel: 0 for channel in INJECTION_CHANNELS}
    layer_counts = {layer: 0 for layer in INJECTION_LAYERS}
    activations_with_plan = 0
    activations_with_channels = {channel: 0 for channel in INJECTION_CHANNELS}
    activations_with_layers = {layer: 0 for layer in INJECTION_LAYERS}
    selected_with_channel = 0
    selected_without_channel = 0

    for activation in activations:
        has_plan = bool((activation.get("injection_plan") or {}).get("channel_counts"))
        if has_plan:
            activations_with_plan += 1
        counts = _activation_injection_channel_counts(activation)
        for channel in INJECTION_CHANNELS:
            count = int(counts.get(channel, 0) or 0)
            channel_counts[channel] += count
            if count:
                activations_with_channels[channel] += 1
        layer_activation_counts = _activation_injection_layer_counts(activation)
        for layer in INJECTION_LAYERS:
            count = int(layer_activation_counts.get(layer, 0) or 0)
            layer_counts[layer] += count
            if count:
                activations_with_layers[layer] += 1
        for asset in activation.get("selected_assets", []):
            if asset.get("injection_channel") in INJECTION_CHANNELS:
                selected_with_channel += 1
            else:
                selected_without_channel += 1

    activation_count = len(activations)
    total_injected_items = sum(channel_counts.values())
    return {
        "policy": "layered_knowledge_injection_v1",
        "legacy_policy": "local_prior_injection_v1",
        "activation_count": activation_count,
        "activations_with_plan": activations_with_plan,
        "plan_coverage_ratio": round(activations_with_plan / activation_count, 4) if activation_count else 0.0,
        "channel_counts": channel_counts,
        "layer_counts": layer_counts,
        "activations_with_channels": activations_with_channels,
        "activations_with_layers": activations_with_layers,
        "total_injected_items": total_injected_items,
        "avg_items_per_activation": round(total_injected_items / activation_count, 4) if activation_count else 0.0,
        "selected_with_channel": selected_with_channel,
        "selected_without_channel": selected_without_channel,
        "layers": {
            TASK_START_RUNTIME_INJECTION: {
                "purpose": "任务开始运行时注入：在 SessionStart/UserPromptSubmit/auto-start 时增强任务输入。",
                "legacy_channels": [channel for channel, layer in CHANNEL_TO_LAYER.items() if layer == TASK_START_RUNTIME_INJECTION],
            },
            SYSTEM_PROMPT_INJECTION: {
                "purpose": "系统提示词注入：沉淀到 AGENTS.md / AGENTS.expcap.md 的项目级稳定先验。",
                "legacy_channels": [channel for channel, layer in CHANNEL_TO_LAYER.items() if layer == SYSTEM_PROMPT_INJECTION],
            },
            CONTINUOUS_RUNTIME_RECALL_INJECTION: {
                "purpose": "持续运行时召回注入：对话中出现新错误、新文件、新阶段或 topic drift 时按需 progressive-recall。",
                "legacy_channels": [channel for channel, layer in CHANNEL_TO_LAYER.items() if layer == CONTINUOUS_RUNTIME_RECALL_INJECTION],
            },
        },
    }


def _build_knowledge_save_layers(
    *,
    workspace: Path,
    memory_root: Path,
    db_path: Path,
    sqlite_backend: dict[str, Any],
    milvus_backend: dict[str, Any],
    counts: dict[str, int],
) -> dict[str, Any]:
    injection_markdown_count = len(list((memory_root / "injections").glob("*.md")))
    project_markdown_paths = [
        path
        for path in [
            workspace / "AGENTS.md",
            workspace / "AGENTS.expcap.md",
            workspace / "README.md",
            workspace / "README.zh-CN.md",
        ]
        if path.exists()
    ]
    docs_markdown_count = len(list((workspace / "docs").glob("*.md"))) if (workspace / "docs").exists() else 0
    hook_event_count = len(list((memory_root / "hooks").glob("*.json"))) if (memory_root / "hooks").exists() else 0
    activation_view_count = len(list((memory_root / "views").glob("*.json"))) if (memory_root / "views").exists() else 0
    local_milvus = milvus_backend.get("local") if isinstance(milvus_backend.get("local"), dict) else {}

    return {
        "milvus": {
            "role": "semantic_retrieval_index",
            "purpose": "语义召回层：根据任务语义查找相似经验资产。",
            "path": local_milvus.get("db_path") or str(default_milvus_db_path(workspace)),
            "available": bool(milvus_backend.get("available")),
            "status": local_milvus.get("status"),
            "indexed_entities": local_milvus.get("indexed_entities"),
        },
        "sqlite": {
            "role": "lightweight_state_index",
            "purpose": "状态索引层：保存候选状态、反馈、review queue、activation log 与健康指标。",
            "path": str(db_path),
            "available": bool(sqlite_backend.get("available", sqlite_backend.get("db_exists", False))),
            "asset_rows": sqlite_backend.get("asset_rows", 0),
            "candidate_rows": sqlite_backend.get("candidate_rows", 0),
            "activation_log_rows": sqlite_backend.get("activation_log_rows", 0),
        },
        "markdown_files": {
            "role": "human_readable_knowledge_assets",
            "purpose": "可读知识层：面向人和项目提示词的 MD 资产、注入快照和项目文档。",
            "project_prompt_paths": [str(path) for path in project_markdown_paths],
            "injection_markdown_count": injection_markdown_count,
            "docs_markdown_count": docs_markdown_count,
            "latest_injection_markdown": str(memory_root / "injections" / "latest.md"),
        },
        "logs": {
            "role": "raw_execution_evidence",
            "purpose": "原始证据层：trace、episode、hook event、activation view，用于后续 extract/review/promote。",
            "trace_count": counts.get("traces", 0),
            "episode_count": counts.get("episodes", 0),
            "hook_event_count": hook_event_count,
            "activation_view_count": activation_view_count,
            "trace_dir": str(memory_root / "traces" / "bundles"),
            "episode_dir": str(memory_root / "episodes"),
            "hook_event_dir": str(memory_root / "hooks"),
            "activation_view_dir": str(memory_root / "views"),
        },
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


def _safe_feedback_cleanup(
    *,
    workspace: Path,
    db_path: Path,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    try:
        return _auto_resolve_stale_activation_feedback(workspace=workspace, db_path=db_path), None
    except (OSError, sqlite3.Error) as error:
        return None, _sqlite_degraded_warning(db_path=db_path, error=error)


def _find_unresolved_activation_for_task(
    *,
    db_path: Path,
    workspace: Path,
    task: str,
) -> dict[str, Any] | None:
    task_key = " ".join(task.casefold().split())
    for activation in list_activation_logs(db_path, workspace=str(workspace)):
        if activation.get("feedback", {}).get("help_signal"):
            continue
        activation_task_key = " ".join(str(activation.get("task_query") or "").casefold().split())
        if activation_task_key == task_key:
            return activation
    return None


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

    ingest_docs = subparsers.add_parser(
        "ingest-docs",
        help="Import project markdown docs as faithful codemap/context assets.",
    )
    ingest_docs.add_argument("--workspace", required=True, help="Workspace path whose docs should be imported.")
    ingest_docs.add_argument(
        "--path",
        dest="paths",
        action="append",
        default=[],
        help="Specific markdown file or directory to import. Defaults to README/AGENTS/CLAUDE and docs/*.md.",
    )
    ingest_docs.add_argument(
        "--max-chars",
        type=int,
        default=4000,
        help="Maximum characters per imported chunk. Defaults to 4000.",
    )
    ingest_docs.add_argument("--output", help="Optional output path for the ingestion report JSON.")

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
        "--integration-mode",
        choices=SUPPORTED_INTEGRATION_MODES,
        help="How expcap should integrate with the target host. Defaults to docs-only.",
    )
    install_project.add_argument(
        "--include-claude",
        action="store_true",
        help=f"Backward-compatible alias for --integration-mode {INTEGRATION_MODE_CLAUDE_HOOKS}.",
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
    benchmark_milvus.add_argument(
        "--expect-kind",
        dest="expected_kinds",
        action="append",
        default=[],
        choices=list(CANONICAL_KNOWLEDGE_KINDS),
        help="Expected knowledge kind that should appear in each result set. May be passed multiple times.",
    )
    benchmark_milvus.add_argument(
        "--expect-source-document",
        dest="expected_source_documents",
        action="append",
        default=[],
        help="Expected source_document path that should appear in each result set. May be passed multiple times.",
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
        choices=list(CANONICAL_KNOWLEDGE_KINDS),
        help="Filter queue by kind; also used as an override when --action promote is selected.",
    )
    review_candidates.add_argument("--output", help="Optional output path for the review queue JSON.")

    save_prior = subparsers.add_parser(
        "save-prior",
        help="Save an explicit active local prior such as a preference, constraint, or dont_repeat instruction.",
    )
    save_prior.add_argument("--workspace", required=True, help="Workspace path that owns the prior.")
    save_prior.add_argument(
        "--knowledge-kind",
        required=True,
        choices=[PAST_WIN, PREFERENCE, CONSTRAINT, DECISION_MEMORY, DONT_REPEAT],
        help="Local-prior kind to save as an active asset.",
    )
    save_prior.add_argument("--title", help="Optional short title. Defaults to a compact content-derived title.")
    save_prior.add_argument("--content", required=True, help="Durable prior content to inject in future tasks.")
    save_prior.add_argument("--scope-level", default="workspace", help="Scope level for the prior. Defaults to workspace.")
    save_prior.add_argument(
        "--scope-value",
        default="general-coding-task",
        help="Scope value for the prior. Defaults to general-coding-task.",
    )
    save_prior.add_argument("--confidence", type=float, default=0.9, help="Confidence for the explicit prior.")
    save_prior.add_argument("--source-note", help="Optional note explaining why this prior was saved.")

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


_DOC_INGEST_EXCLUDED_PARTS = {
    ".agent-memory",
    ".claude",
    ".expcap",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}


def _doc_asset_slug(value: str) -> str:
    cleaned = []
    for ch in value.lower():
        if ch.isalnum():
            cleaned.append(ch)
        elif cleaned and cleaned[-1] != "-":
            cleaned.append("-")
    return "".join(cleaned).strip("-")[:64] or "doc"


def _prior_asset_type(knowledge_kind: str) -> str:
    if knowledge_kind in HIGH_PRIORITY_PRIOR_KINDS:
        return "rule"
    return "context"


def _build_prior_asset(
    *,
    workspace: Path,
    knowledge_kind: str,
    title: str | None,
    content: str,
    scope_level: str,
    scope_value: str,
    confidence: float,
    source_note: str | None,
) -> dict[str, Any]:
    created_at = now_utc()
    asset_type = _prior_asset_type(knowledge_kind)
    digest = hashlib.sha1(f"{knowledge_kind}\n{content}".encode("utf-8")).hexdigest()[:10]
    slug = _doc_asset_slug(title or content)
    review_status = "healthy" if knowledge_kind in HIGH_PRIORITY_PRIOR_KINDS else "unproven"
    return {
        "asset_id": f"{asset_type}_{knowledge_kind}_{slug}_{digest}",
        "workspace": str(workspace),
        "asset_type": asset_type,
        "knowledge_scope": "project",
        "knowledge_kind": knowledge_kind,
        "title": title or _compact_text_for_cli(content, limit=72),
        "content": content,
        "scope": {"level": scope_level, "value": scope_value},
        "source_episode_ids": [],
        "source_candidate_ids": [],
        "source": {
            "kind": "explicit_prior",
            "note": source_note,
        },
        "confidence": round(max(0.0, min(float(confidence), 1.0)), 4),
        "status": "active",
        "temperature": "warm" if knowledge_kind in HIGH_PRIORITY_PRIOR_KINDS else "neutral",
        "review_status": review_status,
        "last_used_at": None,
        "created_at": created_at,
        "updated_at": created_at,
    }


def _compact_text_for_cli(value: str, limit: int = 96) -> str:
    cleaned = " ".join(str(value).split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def _handle_save_prior(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    asset = _build_prior_asset(
        workspace=workspace,
        knowledge_kind=args.knowledge_kind,
        title=args.title,
        content=args.content,
        scope_level=args.scope_level,
        scope_value=args.scope_value,
        confidence=args.confidence,
        source_note=args.source_note,
    )
    asset_path = memory_root_for_workspace(workspace) / "assets" / f"{asset['asset_type']}s" / f"{asset['asset_id']}.json"
    save_json(asset_path, asset)
    upsert_asset(db_path, asset)
    upsert_asset_vector(default_milvus_db_path(workspace), asset)
    _print_json(
        {
            "asset": {
                "asset_id": asset["asset_id"],
                "path": str(asset_path),
                "knowledge_kind": asset["knowledge_kind"],
                "knowledge_scope": asset["knowledge_scope"],
                "review_status": asset["review_status"],
                "temperature": asset["temperature"],
            }
        }
    )
    return 0


def _is_ingestable_doc_path(workspace: Path, path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(workspace)
    except ValueError:
        return False
    if path.suffix.lower() != ".md":
        return False
    if any(part in _DOC_INGEST_EXCLUDED_PARTS for part in relative.parts):
        return False
    if path.name.startswith(".env"):
        return False
    return path.is_file()


def _default_doc_ingest_paths(workspace: Path) -> list[Path]:
    paths = [
        workspace / "README.md",
        workspace / "README.zh-CN.md",
        workspace / "AGENTS.md",
        workspace / "CLAUDE.md",
    ]
    docs_dir = workspace / "docs"
    if docs_dir.exists():
        paths.extend(sorted(docs_dir.rglob("*.md")))
    return [path for path in paths if _is_ingestable_doc_path(workspace, path)]


def _expand_doc_ingest_paths(workspace: Path, raw_paths: list[str]) -> list[Path]:
    if not raw_paths:
        return _default_doc_ingest_paths(workspace)
    expanded: list[Path] = []
    for raw_path in raw_paths:
        path = (workspace / raw_path).resolve() if not Path(raw_path).is_absolute() else Path(raw_path).resolve()
        if path.is_dir():
            expanded.extend(sorted(path.rglob("*.md")))
        else:
            expanded.append(path)
    seen: set[Path] = set()
    result: list[Path] = []
    for path in expanded:
        if path in seen or not _is_ingestable_doc_path(workspace, path):
            continue
        seen.add(path)
        result.append(path)
    return result


def _chunk_doc_text(text: str, *, max_chars: int) -> list[str]:
    max_chars = max(max_chars, 1000)
    soft_heading_boundary = int(max_chars * 0.55)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        starts_heading = line.startswith("#") and current_len >= soft_heading_boundary
        would_overflow = current_len + line_len > max_chars and current
        if starts_heading or would_overflow:
            chunks.append("\n".join(current).strip())
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def _build_doc_asset(
    *,
    workspace: Path,
    relative_path: Path,
    chunk_text: str,
    chunk_index: int,
    chunk_count: int,
    generated_at: str,
) -> dict[str, Any]:
    relative_text = relative_path.as_posix()
    digest = hashlib.sha1(f"{relative_text}:{chunk_index}:{chunk_text}".encode("utf-8")).hexdigest()[:10]
    slug = _doc_asset_slug(relative_text)
    asset_id = f"context_doc_{slug}_{chunk_index:03d}_{digest}"
    return {
        "asset_id": asset_id,
        "workspace": str(workspace),
        "asset_type": "context",
        "knowledge_scope": "project",
        "knowledge_kind": CODEMAP,
        "title": _compact_doc_title(relative_text, chunk_index, chunk_count),
        "content": f"Document: {relative_text}\nChunk: {chunk_index}/{chunk_count}\n\n{chunk_text}",
        "scope": {"level": "workspace", "value": "general-coding-task"},
        "source_workspace": str(workspace),
        "source_episode_ids": [],
        "source_candidate_ids": [],
        "source_document": relative_text,
        "doc_chunk": {
            "relative_path": relative_text,
            "chunk_index": chunk_index,
            "chunk_count": chunk_count,
            "preserved_raw_text": True,
        },
        "confidence": 0.72,
        "status": "active",
        "review_status": "unproven",
        "temperature": "neutral",
        "last_used_at": None,
        "created_at": generated_at,
        "updated_at": generated_at,
    }


def _compact_doc_title(relative_text: str, chunk_index: int, chunk_count: int) -> str:
    title = f"Doc codemap: {relative_text}"
    if chunk_count > 1:
        title = f"{title} #{chunk_index}/{chunk_count}"
    return title if len(title) <= 96 else title[:95].rstrip() + "…"


def _prune_existing_doc_assets(*, workspace: Path, memory_root: Path, db_path: Path) -> int:
    contexts_dir = memory_root / "assets" / "contexts"
    pruned_asset_ids: list[str] = []
    if contexts_dir.exists():
        for path in sorted(contexts_dir.glob("context_doc_*.json")):
            try:
                asset = load_json(path)
                asset_id = str(asset.get("asset_id") or path.stem)
            except (OSError, json.JSONDecodeError):
                asset_id = path.stem
            path.unlink(missing_ok=True)
            pruned_asset_ids.append(asset_id)
    if pruned_asset_ids:
        ensure_db(db_path)
        placeholders = ", ".join("?" for _ in pruned_asset_ids)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                f"DELETE FROM assets WHERE workspace = ? AND asset_id IN ({placeholders})",
                (str(workspace), *pruned_asset_ids),
            )
    return len(pruned_asset_ids)


def _handle_ingest_docs(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    memory_root = memory_root_for_workspace(workspace)
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    pruned_existing_assets = _prune_existing_doc_assets(
        workspace=workspace,
        memory_root=memory_root,
        db_path=db_path,
    )
    docs = _expand_doc_ingest_paths(workspace, args.paths)
    generated_at = now_utc()
    assets: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for doc_path in docs:
        relative_path = doc_path.relative_to(workspace)
        text = doc_path.read_text(encoding="utf-8")
        chunks = _chunk_doc_text(text, max_chars=int(args.max_chars or 4000))
        if not chunks:
            skipped.append({"path": relative_path.as_posix(), "reason": "empty"})
            continue
        for index, chunk in enumerate(chunks, start=1):
            asset = _build_doc_asset(
                workspace=workspace,
                relative_path=relative_path,
                chunk_text=chunk,
                chunk_index=index,
                chunk_count=len(chunks),
                generated_at=generated_at,
            )
            asset_path = memory_root / "assets" / "contexts" / f"{asset['asset_id']}.json"
            save_json(asset_path, asset)
            upsert_asset(db_path, asset)
            vector_synced = upsert_asset_vector(default_milvus_db_path(workspace), asset)
            assets.append(
                {
                    "asset_id": asset["asset_id"],
                    "knowledge_kind": asset["knowledge_kind"],
                    "source_document": asset["source_document"],
                    "chunk_index": index,
                    "chunk_count": len(chunks),
                    "saved_to": str(asset_path),
                    "vector_synced": bool(vector_synced),
                }
            )
    milvus_sync_report = sync_assets_directory_with_report(
        default_milvus_db_path(workspace),
        memory_root / "assets",
        prune=True,
    )
    report = {
        "kind": "doc_ingestion_report",
        "workspace": str(workspace),
        "generated_at": generated_at,
        "document_count": len(docs),
        "asset_count": len(assets),
        "vector_synced_count": sum(1 for item in assets if item["vector_synced"]),
        "pruned_existing_assets": pruned_existing_assets,
        "milvus_sync_report": milvus_sync_report,
        "skipped": skipped,
        "assets": assets,
    }
    output_path = Path(args.output) if args.output else memory_root / "reviews" / "doc_ingestion.json"
    save_json(output_path, report)
    _print_json({"saved_to": str(output_path), "ingestion": report})
    return 0


def _fallback_activation_view_path(workspace: Path, view: dict[str, Any]) -> Path:
    return (
        Path(tempfile.gettempdir())
        / "expcap-activation-views"
        / project_storage_key(workspace)
        / f"{view['activation_id']}.json"
    )


def _fallback_review_output_path(workspace: Path, requested_path: Path) -> Path:
    return Path(tempfile.gettempdir()) / "expcap-reviews" / project_storage_key(workspace) / requested_path.name


def _fallback_warning(
    *,
    reason: str,
    requested_path: Path,
    fallback_path: Path,
    error: OSError,
) -> dict[str, str]:
    return {
        "kind": "fallback_output",
        "reason": reason,
        "requested_path": str(requested_path),
        "fallback_path": str(fallback_path),
        "error": str(error),
    }


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
        return fallback_path, _fallback_warning(
            reason="default_activation_view_unwritable",
            requested_path=output_path,
            fallback_path=fallback_path,
            error=error,
        )


def _safe_materialize_injection_artifacts(
    *,
    workspace: Path,
    view: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str] | None]:
    try:
        return materialize_injection_artifacts(workspace=workspace, activation=view), None
    except OSError as error:
        return {}, {
            "reason": "injection_artifact_unwritable",
            "error": str(error),
        }


def _save_review_json(
    *,
    workspace: Path,
    output_path: Path,
    payload: dict[str, Any],
    requested_output: str | None,
    reason: str,
) -> tuple[Path, dict[str, str] | None]:
    try:
        save_json(output_path, payload)
        return output_path, None
    except OSError as error:
        if requested_output:
            raise
        fallback_path = _fallback_review_output_path(workspace, output_path)
        save_json(fallback_path, payload)
        return fallback_path, _fallback_warning(
            reason=reason,
            requested_path=output_path,
            fallback_path=fallback_path,
            error=error,
        )


def _record_activation_usage(
    *,
    db_path: Path,
    view: dict[str, Any],
) -> dict[str, str] | None:
    try:
        log_activation(db_path, view)
        touch_assets_last_used(
            db_path,
            [item["asset_id"] for item in view.get("selected_assets", [])],
            view["created_at"],
        )
    except (OSError, sqlite3.Error) as error:
        return {
            "kind": "activation_log_unwritable",
            "reason": "sqlite_activation_log_unwritable",
            "db_path": str(db_path),
            "error": str(error),
        }
    return None


def _sqlite_degraded_warning(*, db_path: Path, error: BaseException) -> dict[str, str]:
    return {
        "kind": "storage_degraded",
        "reason": "sqlite_index_unavailable",
        "db_path": str(db_path),
        "fallback": "filesystem_json",
        "error": str(error),
    }


def _filesystem_status_records(
    *,
    memory_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    assets = [
        asset
        for asset in iter_json_objects(memory_root / "assets")
        if asset.get("status", "active") == "active"
    ]
    candidates = [
        candidate
        for candidate in iter_json_objects(memory_root / "candidates")
        if candidate.get("status", "new") in ALL_CANDIDATE_STATUSES
    ]
    activations = sorted(
        list(iter_json_objects(memory_root / "views")),
        key=lambda item: item.get("created_at") or "",
        reverse=True,
    )
    return assets, candidates, activations


def _load_status_records(
    *,
    workspace: Path,
    db_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[dict[str, str]]]:
    memory_root = memory_root_for_workspace(workspace)
    try:
        ensure_db(db_path)
        assets = list_assets(db_path, workspace=str(workspace))
        candidates = list_candidates(
            db_path,
            workspace=str(workspace),
            statuses=ALL_CANDIDATE_STATUSES,
        )
        activations = list_activation_logs(db_path, workspace=str(workspace))
        sqlite_backend = {
            "backend": "sqlite",
            "role": "lightweight-state-index",
            "core_retrieval": False,
            "available": True,
            "degraded": False,
            "db_path": str(db_path),
            "db_exists": db_path.exists(),
            "asset_rows": len(assets),
            "candidate_rows": len(candidates),
            "activation_log_rows": len(activations),
        }
        return assets, candidates, activations, sqlite_backend, []
    except (OSError, sqlite3.Error) as error:
        assets, candidates, activations = _filesystem_status_records(memory_root=memory_root)
        warning = _sqlite_degraded_warning(db_path=db_path, error=error)
        sqlite_backend = {
            "backend": "sqlite",
            "role": "lightweight-state-index",
            "core_retrieval": False,
            "available": False,
            "degraded": True,
            "degraded_reason": warning["reason"],
            "fallback": warning["fallback"],
            "db_path": str(db_path),
            "db_exists": db_path.exists(),
            "asset_rows": len(assets),
            "candidate_rows": len(candidates),
            "activation_log_rows": len(activations),
        }
        return assets, candidates, activations, sqlite_backend, [warning]


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
    feedback_cleanup, feedback_cleanup_warning = _safe_feedback_cleanup(
        workspace=workspace,
        db_path=db_path,
    )
    if feedback_cleanup is None:
        feedback_cleanup = {
            "auto_resolved_count": 0,
            "auto_resolved_activation_ids": [],
            "resolution_help_signal": STALE_FEEDBACK_HELP_SIGNAL,
            "pending_hours": _feedback_pending_hours(),
        }
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
    injection_artifacts, injection_artifact_warning = _safe_materialize_injection_artifacts(
        workspace=workspace,
        view=view,
    )
    if injection_artifacts:
        view["injection_artifacts"] = injection_artifacts
    save_json(output_path, view)
    log_warning = _record_activation_usage(db_path=db_path, view=view)
    payload = {
        "saved_to": str(output_path),
        "injection_artifacts": injection_artifacts,
        "activation_id": view["activation_id"],
        "selected_count": len(view.get("selected_assets", [])),
        "project_activity": project_activity,
        "feedback_cleanup": feedback_cleanup,
        "activation_view": view,
    }
    if save_warning:
        payload["save_warning"] = save_warning
    if log_warning:
        payload["log_warning"] = log_warning
    if injection_artifact_warning:
        payload["injection_artifact_warning"] = injection_artifact_warning
    if feedback_cleanup_warning:
        payload["feedback_cleanup_warning"] = feedback_cleanup_warning
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
        "injection_layer": CONTINUOUS_RUNTIME_RECALL_INJECTION,
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
    log_warning = _record_activation_usage(db_path=db_path, view=view)
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
    if log_warning:
        payload["log_warning"] = log_warning
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
        integration_mode=args.integration_mode,
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
    feedback_cleanup, feedback_cleanup_warning = _safe_feedback_cleanup(
        workspace=workspace,
        db_path=db_path,
    )
    if feedback_cleanup is None:
        feedback_cleanup = {
            "auto_resolved_count": 0,
            "auto_resolved_activation_ids": [],
            "resolution_help_signal": STALE_FEEDBACK_HELP_SIGNAL,
            "pending_hours": _feedback_pending_hours(),
        }

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
    target_activation = _find_unresolved_activation_for_task(
        db_path=db_path,
        workspace=workspace,
        task=args.task,
    )
    if target_activation and target_activation.get("selected_assets"):
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
            activation_id=target_activation["activation_id"],
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
            "feedback_cleanup_warning": feedback_cleanup_warning,
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
    expected_kinds: list[str] | None = None,
    expected_source_documents: list[str] | None = None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    bounded_sample_size = max(sample_size, 0)
    bounded_limit = max(limit, 1)
    milvus_is_available = milvus_available()
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
    expected_kinds = [item for item in (expected_kinds or []) if item]
    expected_source_documents = [item for item in (expected_source_documents or []) if item]
    expected_kind_hit_count = 0
    expected_kind_top_hit_count = 0
    expected_source_document_hit_count = 0
    expected_source_document_top_hit_count = 0
    fallback_sample_count = 0

    for sample in samples:
        fallback_used = False
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
        if not results and not milvus_is_available:
            results = _state_index_benchmark_fallback_results(
                db_path=db_path,
                workspace=workspace,
                query_text=sample["query"],
                limit=bounded_limit,
            )
            fallback_used = bool(results)
            if fallback_used:
                fallback_sample_count += 1
        result_ids = [str(item.get("asset_id")) for item in results if item.get("asset_id")]
        expected_ids = sample["expected_asset_ids"]
        hits = [asset_id for asset_id in expected_ids if asset_id in result_ids]
        kind_hits = [
            str(item.get("asset_id"))
            for item in results
            if str(item.get("knowledge_kind") or "") in expected_kinds
        ]
        source_document_hits = [
            str(item.get("asset_id"))
            for item in results
            if _result_matches_expected_source_document(item, expected_source_documents)
        ]
        if expected_kinds:
            if kind_hits:
                expected_kind_hit_count += 1
            if results and str(results[0].get("knowledge_kind") or "") in expected_kinds:
                expected_kind_top_hit_count += 1
        if expected_source_documents:
            if source_document_hits:
                expected_source_document_hit_count += 1
            if results and _result_matches_expected_source_document(results[0], expected_source_documents):
                expected_source_document_top_hit_count += 1
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
                "expected_kind_hit_asset_ids": kind_hits,
                "expected_kind_hit_count": len(kind_hits),
                "expected_source_document_hit_asset_ids": source_document_hits,
                "expected_source_document_hit_count": len(source_document_hits),
                "retrieval_fallback": "state-index" if fallback_used else None,
                "results": [
                    {
                        "asset_id": item.get("asset_id"),
                        "title": item.get("title"),
                        "knowledge_scope": item.get("knowledge_scope"),
                        "knowledge_kind": item.get("knowledge_kind"),
                        "source_document": item.get("source_document"),
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
        "milvus_available": milvus_is_available,
        "embedding": embedding_provider_config(),
        "preflight_sync": {
            "local": local_sync_report,
            "shared": shared_sync_report if include_shared else None,
        },
        "fallback_retrieval": {
            "used": fallback_sample_count > 0,
            "reason": "milvus_unavailable" if not milvus_is_available else None,
            "sample_count": fallback_sample_count,
        },
        "limit": bounded_limit,
        "sample_size": bounded_sample_size,
        "include_shared": include_shared,
        "expected_kinds": expected_kinds,
        "expected_source_documents": expected_source_documents,
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
            "queries_with_expected_kind": expected_kind_hit_count if expected_kinds else None,
            "expected_kind_hit_rate": round(expected_kind_hit_count / len(benchmark_items), 4)
            if expected_kinds and benchmark_items
            else None,
            "expected_kind_top_hit_rate": round(expected_kind_top_hit_count / len(benchmark_items), 4)
            if expected_kinds and benchmark_items
            else None,
            "queries_with_expected_source_document": expected_source_document_hit_count
            if expected_source_documents
            else None,
            "expected_source_document_hit_rate": round(expected_source_document_hit_count / len(benchmark_items), 4)
            if expected_source_documents and benchmark_items
            else None,
            "expected_source_document_top_hit_rate": round(
                expected_source_document_top_hit_count / len(benchmark_items),
                4,
            )
            if expected_source_documents and benchmark_items
            else None,
        },
        "samples": benchmark_items,
    }


def _result_matches_expected_source_document(item: dict[str, Any], expected_source_documents: list[str]) -> bool:
    if not expected_source_documents:
        return False
    source_document = str(item.get("source_document") or "")
    title = str(item.get("title") or "")
    content = str(item.get("content") or "")
    return any(
        expected in source_document or expected in title or expected in content
        for expected in expected_source_documents
    )


def _benchmark_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_]+", value.lower()) if len(token) > 1}


def _state_index_benchmark_fallback_results(
    *,
    db_path: Path,
    workspace: Path,
    query_text: str,
    limit: int,
) -> list[dict[str, Any]]:
    query_tokens = _benchmark_tokens(query_text)
    if not query_tokens:
        return []
    try:
        assets = list_assets(db_path, workspace=str(workspace))
    except sqlite3.Error:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for asset in assets:
        if asset.get("status", "active") != "active":
            continue
        text = " ".join(
            str(asset.get(key) or "")
            for key in ("title", "content", "source_document", "knowledge_kind", "asset_type")
        )
        overlap = query_tokens & _benchmark_tokens(text)
        if not overlap:
            continue
        score = len(overlap) / len(query_tokens)
        if str(asset.get("source_document") or "").lower() in query_text.lower():
            score += 0.1
        scored.append((score, asset))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "asset_id": asset.get("asset_id"),
            "title": asset.get("title"),
            "knowledge_scope": asset.get("knowledge_scope", "project"),
            "knowledge_kind": asset.get("knowledge_kind", asset.get("asset_type", "pattern")),
            "asset_type": asset.get("asset_type", "pattern"),
            "source_document": asset.get("source_document"),
            "content": asset.get("content", ""),
            "confidence": asset.get("confidence", 0.0),
            "milvus_index": "state-index-fallback",
            "vector_score": score,
            "embedding": {
                "provider": "state-index-fallback",
                "model": "lexical-overlap",
                "status": "fallback",
            },
        }
        for score, asset in scored[:limit]
    ]


def _handle_benchmark_milvus(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    payload = _build_milvus_benchmark_payload(
        workspace=workspace,
        queries=args.queries,
        sample_size=args.sample_size,
        limit=args.limit,
        include_shared=args.include_shared,
        expected_kinds=args.expected_kinds,
        expected_source_documents=args.expected_source_documents,
    )
    output_path = (
        Path(args.output)
        if args.output
        else memory_root_for_workspace(workspace) / "reviews" / "milvus_benchmark.json"
    )
    output_path, save_warning = _save_review_json(
        workspace=workspace,
        output_path=output_path,
        payload=payload,
        requested_output=args.output,
        reason="default_milvus_benchmark_output_unwritable",
    )
    result = {"saved_to": str(output_path), "benchmark": payload}
    if save_warning:
        result["save_warning"] = save_warning
    _print_json(result)
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
    injection_artifacts, injection_artifact_warning = _safe_materialize_injection_artifacts(
        workspace=workspace,
        view=view,
    )
    if injection_artifacts:
        view["injection_artifacts"] = injection_artifacts
    save_json(output_path, view)
    log_warning = _record_activation_usage(db_path=db_path, view=view)
    payload = {
        "saved_to": str(output_path),
        "injection_artifacts": injection_artifacts,
        "activation_id": view["activation_id"],
        "activation_view": view,
    }
    if save_warning:
        payload["save_warning"] = save_warning
    if log_warning:
        payload["log_warning"] = log_warning
    if injection_artifact_warning:
        payload["injection_artifact_warning"] = injection_artifact_warning
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
    if args.knowledge_kind:
        candidates = [
            candidate
            for candidate in candidates
            if candidate.get("knowledge_kind", candidate.get("candidate_type", "pattern")) == args.knowledge_kind
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
    runtime_warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    memory_root = memory_root_for_workspace(workspace)
    db_path = default_db_path(workspace)
    backend_config = resolve_backend_config()
    project_activity = load_project_policy(workspace)

    assets, candidates, activations, sqlite_backend, sqlite_warnings = _load_status_records(
        workspace=workspace,
        db_path=db_path,
    )
    runtime_warnings = [*(runtime_warnings or []), *sqlite_warnings]
    candidates = [
        candidate
        for candidate in candidates
        if candidate.get("status", "new") in ALL_CANDIDATE_STATUSES
    ]
    activations = sorted(
        activations,
        key=lambda item: item.get("created_at") or "",
        reverse=True,
    )
    traces = list(iter_json_objects(memory_root / "traces" / "bundles"))
    episodes = list(iter_json_objects(memory_root / "episodes"))
    recent_hook_events = load_recent_hook_events(workspace, limit=limit)

    feedback_summary = _summarize_activation_feedback(activations)
    unresolved_activations = _build_unresolved_activation_items(activations, limit=limit)
    milvus_effectiveness = _summarize_milvus_retrieval_effectiveness(activations)
    injection_policy_summary = _summarize_injection_policy(activations)

    proof_tracked_assets = [
        asset
        for asset in assets
        if str(asset.get("knowledge_kind", asset.get("asset_type", "pattern")) or "pattern") != CODEMAP
    ]
    temperature_summary = {"hot": 0, "warm": 0, "neutral": 0, "cool": 0}
    review_status_summary = {"healthy": 0, "watch": 0, "needs_review": 0, "unproven": 0}
    for asset in proof_tracked_assets:
        temperature_summary[asset.get("temperature", "neutral")] = (
            temperature_summary.get(asset.get("temperature", "neutral"), 0) + 1
        )
        review_status_summary[asset.get("review_status", "unproven")] = (
            review_status_summary.get(asset.get("review_status", "unproven"), 0) + 1
        )
    asset_review_backlog = _build_asset_review_backlog(
        review_status_summary,
        total_assets=len(proof_tracked_assets),
    )
    unproven_validation_queue = _build_unproven_validation_queue(
        proof_tracked_assets,
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
            "knowledge_kind": candidate.get("knowledge_kind", candidate.get("candidate_type", "pattern")),
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
            "injection_channel_counts": _activation_injection_channel_counts(activation),
            "injection_layer_counts": _activation_injection_layer_counts(activation),
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
    integration_mode = str(project_activity.get("integration_mode") or DEFAULT_INTEGRATION_MODE)
    claude_settings_path = workspace / ".claude" / "settings.json"
    claude_prompt_hook_path = workspace / ".claude" / "hooks" / "expcap_user_prompt_submit.sh"
    claude_stop_hook_path = workspace / ".claude" / "hooks" / "expcap_stop.sh"
    claude_hook_files_ok = (
        claude_settings_path.exists()
        and claude_prompt_hook_path.exists()
        and claude_stop_hook_path.exists()
    ) if integration_mode == INTEGRATION_MODE_CLAUDE_HOOKS else False
    codex_hooks_path = workspace / ".codex" / "hooks.json"
    codex_prompt_hook_path = workspace / ".codex" / "hooks" / "expcap_user_prompt_submit.sh"
    codex_stop_hook_path = workspace / ".codex" / "hooks" / "expcap_stop.sh"
    codex_hook_files_ok = (
        codex_hooks_path.exists()
        and codex_prompt_hook_path.exists()
        and codex_stop_hook_path.exists()
    ) if integration_mode == INTEGRATION_MODE_CODEX_HOOKS else False
    last_hook_event = recent_hook_events[0] if recent_hook_events else None
    milvus_backend_payload = {
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
    }
    counts_payload = {
        "traces": len(traces),
        "episodes": len(episodes),
        "candidates": len(candidates),
        "assets": len(assets),
        "activation_logs": len(activations),
    }

    return {
        "workspace": str(workspace),
        "generated_at": now_utc(),
        "backend_configuration": backend_config,
        "project_activity": project_activity,
        "hook_integration": {
            "integration_mode": integration_mode,
            "codex": {
                "configured": integration_mode == INTEGRATION_MODE_CODEX_HOOKS,
                "hooks_path": str(codex_hooks_path),
                "prompt_hook_path": str(codex_prompt_hook_path),
                "stop_hook_path": str(codex_stop_hook_path),
                "files_present": codex_hook_files_ok,
            },
            "claude": {
                "configured": integration_mode == INTEGRATION_MODE_CLAUDE_HOOKS,
                "settings_path": str(claude_settings_path),
                "prompt_hook_path": str(claude_prompt_hook_path),
                "stop_hook_path": str(claude_stop_hook_path),
                "files_present": claude_hook_files_ok,
            },
            "event_count": len(recent_hook_events),
            "last_event": last_hook_event,
            "recent_events": recent_hook_events,
        },
        "feedback_cleanup": feedback_cleanup
        or {
            "auto_resolved_count": 0,
            "auto_resolved_activation_ids": [],
            "resolution_help_signal": STALE_FEEDBACK_HELP_SIGNAL,
            "pending_hours": _feedback_pending_hours(),
        },
        "storage_layout": storage_layout_for_workspace(workspace),
        "runtime_warnings": runtime_warnings,
        "retrieval_backends": {
            "sqlite": sqlite_backend,
            "milvus": milvus_backend_payload,
        },
        "knowledge_save_layers": _build_knowledge_save_layers(
            workspace=workspace,
            memory_root=memory_root,
            db_path=db_path,
            sqlite_backend=sqlite_backend,
            milvus_backend=milvus_backend_payload,
            counts=counts_payload,
        ),
        "milvus_retrieval_effectiveness": milvus_effectiveness,
        "injection_policy_summary": injection_policy_summary,
        "counts": counts_payload,
        "activation_feedback_summary": feedback_summary,
        "unresolved_activations": unresolved_activations,
        "asset_effectiveness_summary": {
            "temperature": temperature_summary,
            "review_status": review_status_summary,
        },
        "knowledge_kind_summary": {
            "assets": build_knowledge_kind_summary(assets),
            "candidates": build_knowledge_kind_summary(candidates),
            "review_queue": review_queue["knowledge_kind_summary"],
        },
        "asset_review_backlog": asset_review_backlog,
        "unproven_validation_queue": unproven_validation_queue,
        "candidate_status_summary": candidate_status_summary,
        "candidate_review_queue": {
            "candidate_count": review_queue["candidate_count"],
            "status_summary": review_queue["status_summary"],
            "knowledge_kind_summary": review_queue["knowledge_kind_summary"],
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
    runtime_warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    status_payload = _build_status_payload(
        workspace=workspace,
        limit=limit,
        deep_retrieval_check=deep_retrieval_check,
        feedback_cleanup=feedback_cleanup,
        runtime_warnings=runtime_warnings,
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
    hook_integration = status_payload.get("hook_integration") or {
        "integration_mode": DEFAULT_INTEGRATION_MODE,
        "recent_events": [],
        "last_event": None,
        "codex": {"files_present": False},
        "claude": {"files_present": False},
    }

    checks.append(
        _diagnostic_check(
            "sqlite_index",
            "pass" if sqlite_backend.get("available", sqlite_backend.get("db_exists", False)) else "warn",
            (
                f"SQLite index has {sqlite_backend['asset_rows']} assets, "
                f"{sqlite_backend['candidate_rows']} candidates, and "
                f"{sqlite_backend['activation_log_rows']} activation logs."
                if sqlite_backend.get("available", sqlite_backend.get("db_exists", False))
                else (
                    "SQLite index is unavailable; status used filesystem JSON fallback with "
                    f"{sqlite_backend['asset_rows']} assets, {sqlite_backend['candidate_rows']} candidates, "
                    f"and {sqlite_backend['activation_log_rows']} activation views."
                )
            ),
            None
            if sqlite_backend.get("available", sqlite_backend.get("db_exists", False))
            else "Check SQLite permissions/locks; Milvus recall can continue, but state metrics are degraded.",
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
    hook_mode = str(hook_integration.get("integration_mode") or DEFAULT_INTEGRATION_MODE)
    recent_hook_events = hook_integration.get("recent_events") or []
    last_hook_event = hook_integration.get("last_event") or {}
    if hook_mode in {INTEGRATION_MODE_CODEX_HOOKS, INTEGRATION_MODE_CLAUDE_HOOKS}:
        host_key = "codex" if hook_mode == INTEGRATION_MODE_CODEX_HOOKS else "claude"
        host_label = "Codex" if hook_mode == INTEGRATION_MODE_CODEX_HOOKS else "Claude"
        hook_files_present = bool(hook_integration.get(host_key, {}).get("files_present"))
        last_hook_status = str(last_hook_event.get("status") or "unknown")
        hook_status = "pass" if hook_files_present and last_hook_status != "error" else "warn"
        hook_summary = (
            f"{host_label} hook integration is configured; files_present={hook_files_present}, "
            f"recent_events={len(recent_hook_events)}, last_status={last_hook_status}."
        )
        hook_recommendation = None
        if not hook_files_present:
            hook_recommendation = f"Re-run install-project with --integration-mode {hook_mode} to restore missing hook files."
        elif last_hook_status == "error":
            hook_recommendation = "Inspect the latest hook event and wrapper stderr; the integration is installed but the last hook run failed."
        checks.append(
            _diagnostic_check(
                "hook_runtime",
                hook_status,
                hook_summary,
                hook_recommendation,
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
    runtime_warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    db_path = default_db_path(workspace)
    bounded_limit = max(limit, 1)
    bounded_days = max(days, 1)
    status_payload = _build_status_payload(
        workspace=workspace,
        limit=bounded_limit,
        deep_retrieval_check=deep_retrieval_check,
        runtime_warnings=runtime_warnings,
    )

    assets, candidates, activations, _, _ = _load_status_records(
        workspace=workspace,
        db_path=db_path,
    )
    assets = sorted(
        assets,
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )
    candidates = sorted(
        candidates,
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )
    activations = sorted(
        activations,
        key=lambda item: item.get("created_at") or "",
        reverse=True,
    )

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
            "knowledge_kind": candidate.get("knowledge_kind", candidate.get("candidate_type", "pattern")),
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
            "injection_channel_counts": _activation_injection_channel_counts(activation),
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
    total_assets = int(status_payload["asset_review_backlog"]["total_assets"] or 0)
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
            "local_prior_assets": status_payload["knowledge_kind_summary"]["assets"]["local_prior_count"],
            "high_priority_prior_assets": status_payload["knowledge_kind_summary"]["assets"]["high_priority_count"],
            "system_prompt_items": status_payload["injection_policy_summary"]["channel_counts"]["system_prompt"],
            "reference_summary_items": status_payload["injection_policy_summary"]["channel_counts"]["reference_summary"],
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
        "knowledge_kind_summary": status_payload["knowledge_kind_summary"],
        "injection_policy_summary": status_payload["injection_policy_summary"],
        "knowledge_save_layers": status_payload["knowledge_save_layers"],
        "retrieval": {
            "milvus": status_payload["retrieval_backends"]["milvus"],
            "effectiveness": status_payload["milvus_retrieval_effectiveness"],
        },
        "quality": {
            "asset_effectiveness_summary": status_payload["asset_effectiveness_summary"],
            "asset_review_backlog": status_payload["asset_review_backlog"],
            "activation_feedback_summary": status_payload["activation_feedback_summary"],
            "knowledge_kind_summary": status_payload["knowledge_kind_summary"],
            "injection_policy_summary": status_payload["injection_policy_summary"],
            "knowledge_save_layers": status_payload["knowledge_save_layers"],
        },
    }


def _render_count_cards(payload: dict[str, Any]) -> str:
    cards = payload["cards"]
    items = [
        ("Assets", cards["assets"], "project-owned reusable knowledge"),
        ("Activations", cards["activation_logs"], "recent get attempts"),
        ("Healthy", cards["healthy_assets"], "assets with positive proof"),
        ("Unproven", cards["unproven_assets"], "assets still needing evidence"),
        ("Local Priors", cards["local_prior_assets"], "assets carrying local behavior/context priors"),
        ("High Priority Priors", cards["high_priority_prior_assets"], "preference, constraint, and dont_repeat assets"),
        ("System Prompt", cards["system_prompt_items"], "tiny durable priors routed to system prompt"),
        ("Reference", cards["reference_summary_items"], "codemap/raw evidence routed for LLM re-analysis"),
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


def _render_runtime_warnings(payload: dict[str, Any]) -> str:
    warnings = payload.get("status", {}).get("runtime_warnings", [])
    if not warnings:
        return ""
    rows = "".join(
        f"<li><strong>{_safe_text(item.get('reason'))}</strong>: {_safe_text(item.get('error'))}</li>"
        for item in warnings
    )
    return f"""
    <section class="warning-banner">
      <h2>Degraded Mode</h2>
      <p>Some state-index data came from fallback storage. Milvus semantic recall can continue, but SQLite-derived metrics may be incomplete.</p>
      <ul>{rows}</ul>
    </section>
    """


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


def _render_injection_policy_panel(payload: dict[str, Any]) -> str:
    summary = payload["injection_policy_summary"]
    channel_counts = summary.get("channel_counts", {})
    layer_counts = summary.get("layer_counts", {})
    activations_with_channels = summary.get("activations_with_channels", {})
    rows = [
        [
            channel,
            channel_counts.get(channel, 0),
            activations_with_channels.get(channel, 0),
        ]
        for channel in INJECTION_CHANNELS
    ]
    layer_rows = [
        [
            layer,
            layer_counts.get(layer, 0),
            (summary.get("activations_with_layers") or {}).get(layer, 0),
        ]
        for layer in INJECTION_LAYERS
    ]
    return f"""
    <section class="panel">
      <h2>Injection Layers</h2>
      <div class="metric-line"><span>Policy</span><strong>{_safe_text(summary.get("policy"))}</strong></div>
      <div class="metric-line"><span>Plan coverage</span><strong>{_safe_text(f"{float(summary.get('plan_coverage_ratio', 0.0) or 0.0):.0%}")}</strong></div>
      <div class="metric-line"><span>Average injected items</span><strong>{_safe_text(summary.get("avg_items_per_activation"))}</strong></div>
      {_render_dashboard_table(["Layer", "Items", "Activations"], layer_rows)}
      <h3>Legacy Injection Channels</h3>
      {_render_dashboard_table(["Channel", "Items", "Activations"], rows)}
    </section>
    """


def _render_dashboard_html(payload: dict[str, Any]) -> str:
    retrieval = payload["retrieval"]["effectiveness"]
    quality = payload["quality"]
    kind_summary = payload["knowledge_kind_summary"]
    injection_summary = payload["injection_policy_summary"]
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
            item.get("injection_channel_counts", {}).get("system_prompt", 0),
            item.get("injection_channel_counts", {}).get("runtime_context", 0),
            item.get("injection_channel_counts", {}).get("reference_summary", 0),
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
            item["knowledge_kind"],
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
            item.get("knowledge_kind"),
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
    prior_kind_rows = [
        [
            section,
            summary.get("local_prior_count"),
            summary.get("high_priority_count"),
            summary.get("by_kind"),
            summary.get("high_priority_by_kind"),
        ]
        for section, summary in [
            ("Assets", kind_summary["assets"]),
            ("Candidates", kind_summary["candidates"]),
            ("Review queue", kind_summary["review_queue"]),
        ]
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
    .warning-banner {{ margin: 0 0 18px; padding: 16px 18px; border: 1px solid #d7a742; border-radius: 18px; background: #fff0c2; color: #4e3300; }}
    .warning-banner h2 {{ margin: 0 0 6px; color: #6e4700; }}
    .warning-banner p {{ margin: 0 0 8px; }}
    .warning-banner ul {{ margin: 0; padding-left: 20px; }}
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

  {_render_runtime_warnings(payload)}

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
      <div class="metric-line"><span>Injection policy</span><strong>{_safe_text(injection_summary.get("policy"))}</strong></div>
    </div>
  </section>

  <section class="panel">
    <h2>Write Frequency</h2>
    {_render_dashboard_table(["Date", "Assets", "Candidates", "Activations"], write_rows)}
  </section>

  <section class="panel">
    <h2>Local Prior Distribution</h2>
    {_render_dashboard_table(["Section", "Local priors", "High priority", "By kind", "High priority by kind"], prior_kind_rows)}
  </section>

  {_render_injection_policy_panel(payload)}

  <section class="panel">
    <h2>Assets</h2>
    {_render_dashboard_table(["Title", "Kind", "Scope", "Temp", "Review", "Confidence", "Updated"], asset_rows)}
  </section>

  <section class="panel">
    <h2>Recent Activations</h2>
    {_render_dashboard_table(["Task", "Selected", "Milvus selected", "System", "Runtime", "Reference", "Milvus project", "Milvus shared", "Help", "Created"], activation_rows)}
  </section>

  <section class="panel">
    <h2>Candidate Review Queue</h2>
    {_render_dashboard_table(["Title", "Kind", "Status", "Readiness", "Priority", "Candidate ID"], queue_rows)}
  </section>

  <section class="panel">
    <h2>Unproven Validation Queue</h2>
    {_render_dashboard_table(["Title", "Kind", "Confidence", "Priority", "Validation Hint", "Updated"], unproven_rows)}
  </section>

  <section class="panel">
    <h2>Recent Candidates</h2>
    {_render_dashboard_table(["Title", "Kind", "Status", "Readiness", "Help", "Updated"], candidate_rows)}
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
    db_path = default_db_path(workspace)
    feedback_cleanup, cleanup_warning = _safe_feedback_cleanup(workspace=workspace, db_path=db_path)
    payload = _build_dashboard_payload(
        workspace=workspace,
        limit=args.limit,
        days=args.days,
        deep_retrieval_check=args.deep_retrieval_check,
        runtime_warnings=[cleanup_warning] if cleanup_warning else None,
    )
    if feedback_cleanup is not None:
        payload["status"]["feedback_cleanup"] = feedback_cleanup
    output_path = (
        Path(args.output)
        if args.output
        else memory_root_for_workspace(workspace) / "reviews" / "dashboard.html"
    )
    save_warning = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(_render_dashboard_html(payload), encoding="utf-8")
        data_output_path = output_path.with_suffix(".json")
        save_json(data_output_path, payload)
    except OSError as error:
        if args.output:
            raise
        fallback_path = _fallback_review_output_path(workspace, output_path)
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        fallback_path.write_text(_render_dashboard_html(payload), encoding="utf-8")
        data_output_path = fallback_path.with_suffix(".json")
        save_json(data_output_path, payload)
        save_warning = _fallback_warning(
            reason="default_dashboard_output_unwritable",
            requested_path=output_path,
            fallback_path=fallback_path,
            error=error,
        )
        output_path = fallback_path
    result = {
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
    if save_warning:
        result["save_warning"] = save_warning
    _print_json(result)
    return 0


def _handle_status(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    memory_root = memory_root_for_workspace(workspace)
    db_path = default_db_path(workspace)
    feedback_cleanup, cleanup_warning = _safe_feedback_cleanup(workspace=workspace, db_path=db_path)
    payload = _build_status_payload(
        workspace=workspace,
        limit=args.limit,
        deep_retrieval_check=args.deep_retrieval_check,
        feedback_cleanup=feedback_cleanup,
        runtime_warnings=[cleanup_warning] if cleanup_warning else None,
    )
    output_path = (
        Path(args.output)
        if args.output
        else memory_root / "reviews" / "workspace_status.json"
    )
    output_path, save_warning = _save_review_json(
        workspace=workspace,
        output_path=output_path,
        payload=payload,
        requested_output=args.output,
        reason="default_status_output_unwritable",
    )
    result = {"saved_to": str(output_path), "status": payload}
    if save_warning:
        result["save_warning"] = save_warning
    _print_json(result)
    return 0


def _handle_doctor(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    memory_root = memory_root_for_workspace(workspace)
    db_path = default_db_path(workspace)
    feedback_cleanup, cleanup_warning = _safe_feedback_cleanup(workspace=workspace, db_path=db_path)
    payload = _build_doctor_payload(
        workspace=workspace,
        limit=args.limit,
        deep_retrieval_check=args.deep_retrieval_check,
        feedback_cleanup=feedback_cleanup,
        runtime_warnings=[cleanup_warning] if cleanup_warning else None,
    )
    output_path = (
        Path(args.output)
        if args.output
        else memory_root / "reviews" / "doctor.json"
    )
    output_path, save_warning = _save_review_json(
        workspace=workspace,
        output_path=output_path,
        payload=payload,
        requested_output=args.output,
        reason="default_doctor_output_unwritable",
    )
    result = {"saved_to": str(output_path), "doctor": payload}
    if save_warning:
        result["save_warning"] = save_warning
    _print_json(result)
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "ingest":
        return _handle_ingest(args)
    if args.command == "save-prior":
        return _handle_save_prior(args)
    if args.command == "ingest-docs":
        return _handle_ingest_docs(args)
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
