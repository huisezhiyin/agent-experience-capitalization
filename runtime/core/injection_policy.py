from __future__ import annotations

from typing import Any

from runtime.core.knowledge_kinds import (
    CODEMAP,
    CONSTRAINT,
    DECISION_MEMORY,
    DONT_REPEAT,
    HIGH_PRIORITY_PRIOR_KINDS,
    PREFERENCE,
)


SYSTEM_PROMPT = "system_prompt"
RUNTIME_CONTEXT = "runtime_context"
REFERENCE_SUMMARY = "reference_summary"

INJECTION_CHANNELS = (SYSTEM_PROMPT, RUNTIME_CONTEXT, REFERENCE_SUMMARY)

_SYSTEM_PROMPT_LIMIT = 3
_RUNTIME_CONTEXT_LIMIT = 5
_REFERENCE_SUMMARY_LIMIT = 5


def _compact(value: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def _asset_content(asset: dict[str, Any], *, limit: int) -> str:
    return _compact(str(asset.get("content") or ""), limit)


def _policy_reason(asset: dict[str, Any], channel: str) -> str:
    kind = str(asset.get("knowledge_kind") or asset.get("asset_type") or "pattern")
    if channel == SYSTEM_PROMPT:
        return f"{kind} 是高优先级且体积较小的长期先验，适合减少重复提醒。"
    if channel == REFERENCE_SUMMARY:
        return f"{kind} 更适合作为按需参考材料，由 LLM 在当前任务中再分析。"
    return f"{kind} 与当前任务相关，适合作为运行时上下文注入。"


def _plan_item(asset: dict[str, Any], *, channel: str, content_limit: int) -> dict[str, Any]:
    return {
        "asset_id": asset.get("asset_id"),
        "knowledge_scope": asset.get("knowledge_scope", "project"),
        "knowledge_kind": asset.get("knowledge_kind", asset.get("asset_type", "pattern")),
        "asset_type": asset.get("asset_type"),
        "title": asset.get("title"),
        "content": _asset_content(asset, limit=content_limit),
        "source_episode_ids": asset.get("source_episode_ids", []),
        "retrieval_sources": asset.get("retrieval_sources", []),
        "review_status": asset.get("review_status", "unproven"),
        "temperature": asset.get("temperature", "neutral"),
        "policy_reason": _policy_reason(asset, channel),
    }


def _is_system_prompt_candidate(asset: dict[str, Any]) -> bool:
    kind = str(asset.get("knowledge_kind") or asset.get("asset_type") or "pattern")
    if kind not in HIGH_PRIORITY_PRIOR_KINDS:
        return False
    if len(str(asset.get("content") or "")) > 420:
        return False
    review_status = str(asset.get("review_status") or "unproven")
    confidence = float(asset.get("confidence", 0.0) or 0.0)
    source = asset.get("source") if isinstance(asset.get("source"), dict) else {}
    if source.get("kind") == "explicit_prior" and confidence >= 0.8:
        return True
    activation_count = int(asset.get("historical_help", {}).get("activation_count", 0) or 0)
    if review_status in {"healthy", "watch"}:
        return True
    return confidence >= 0.8 and activation_count > 0


def injection_channel_for_asset(asset: dict[str, Any]) -> str:
    kind = str(asset.get("knowledge_kind") or asset.get("asset_type") or "pattern")
    if _is_system_prompt_candidate(asset):
        return SYSTEM_PROMPT
    if kind == CODEMAP:
        return REFERENCE_SUMMARY
    if kind == DECISION_MEMORY and len(str(asset.get("content") or "")) > 420:
        return REFERENCE_SUMMARY
    if str(asset.get("asset_type") or "") == "context" and kind not in {
        CONSTRAINT,
        DONT_REPEAT,
        PREFERENCE,
    }:
        return REFERENCE_SUMMARY
    return RUNTIME_CONTEXT


def build_injection_plan(
    selected_assets: list[dict[str, Any]],
    *,
    constraints: list[str],
) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {channel: [] for channel in INJECTION_CHANNELS}
    for asset in selected_assets:
        channel = injection_channel_for_asset(asset)
        if channel == SYSTEM_PROMPT and len(buckets[channel]) >= _SYSTEM_PROMPT_LIMIT:
            channel = RUNTIME_CONTEXT
        elif channel == RUNTIME_CONTEXT and len(buckets[channel]) >= _RUNTIME_CONTEXT_LIMIT:
            channel = REFERENCE_SUMMARY
        elif channel == REFERENCE_SUMMARY and len(buckets[channel]) >= _REFERENCE_SUMMARY_LIMIT:
            continue
        content_limit = 220 if channel == SYSTEM_PROMPT else 700 if channel == RUNTIME_CONTEXT else 1200
        buckets[channel].append(_plan_item(asset, channel=channel, content_limit=content_limit))

    if constraints:
        buckets[RUNTIME_CONTEXT].append(
            {
                "asset_id": None,
                "knowledge_scope": "current_task",
                "knowledge_kind": CONSTRAINT,
                "asset_type": "constraint",
                "title": "Current explicit constraints",
                "content": "；".join(constraints),
                "source_episode_ids": [],
                "retrieval_sources": ["current-task"],
                "review_status": "current",
                "temperature": "current",
                "policy_reason": "当前任务显式约束必须进入运行时上下文，而不是长期资产。",
            }
        )

    return {
        "version": "2026-05-08",
        "policy": "local_prior_injection_v1",
        "principle": "Save repetition: inject habits, preferences, background, and raw references by stability and size.",
        "channels": {
            SYSTEM_PROMPT: {
                "purpose": "Tiny durable priors that should influence every compatible run.",
                "items": buckets[SYSTEM_PROMPT],
            },
            RUNTIME_CONTEXT: {
                "purpose": "Task-relevant priors and explicit constraints for the current run.",
                "items": buckets[RUNTIME_CONTEXT],
            },
            REFERENCE_SUMMARY: {
                "purpose": "Retrieved codemap/raw/background evidence for LLM re-analysis.",
                "items": buckets[REFERENCE_SUMMARY],
            },
        },
        "channel_counts": {channel: len(items) for channel, items in buckets.items()},
    }
