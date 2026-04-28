from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.backends import resolve_backend_config
from runtime.storage.fs_store import (
    default_milvus_db_path,
    iter_json_objects,
    shared_memory_root,
    shared_milvus_db_path,
)
from runtime.storage.milvus_store import search_asset_vectors, sync_assets_directory
from runtime.storage.sqlite_store import get_asset, list_assets, list_candidates, summarize_asset_feedback


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def now_utc() -> str:
    return _now_utc()


def _slugify(value: str) -> str:
    cleaned = []
    for ch in value.lower():
        if ch.isalnum():
            cleaned.append(ch)
        elif cleaned and cleaned[-1] != "-":
            cleaned.append("-")
    return "".join(cleaned).strip("-") or "item"


def _infer_scope(task_text: str) -> dict[str, str]:
    lower = task_text.lower()
    if "import" in lower:
        return {"level": "task-family", "value": "python-import-error"}
    if "test" in lower or "pytest" in lower:
        return {"level": "task-family", "value": "test-failure"}
    return {"level": "workspace", "value": "general-coding-task"}


def _compact_text(value: str, limit: int = 96) -> str:
    cleaned = " ".join(value.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _same_workspace_path(left: Any, right: str) -> bool:
    if not left:
        return False
    try:
        return Path(str(left)).expanduser().resolve() == Path(right).expanduser().resolve()
    except (OSError, RuntimeError):
        return str(left) == right


def _task_tokens(value: str) -> list[str]:
    token = []
    tokens: list[str] = []
    for ch in value.lower():
        if ch.isalnum():
            token.append(ch)
            continue
        if token:
            tokens.append("".join(token))
            token = []
    if token:
        tokens.append("".join(token))
    return _unique_preserve_order([item for item in tokens if len(item) >= 3 and not item.isdigit()])


def _build_turning_points(
    *,
    errors: list[str],
    commands: list[str],
    verification: dict[str, Any],
    result: dict[str, Any],
) -> list[str]:
    turning_points: list[str] = []
    if errors:
        turning_points.append(f"发现关键错误信号：{_compact_text(errors[0])}")
    if commands:
        turning_points.append(f"通过关键命令推进排查：{_compact_text(commands[0])}")
    if verification.get("summary"):
        turning_points.append(f"验证结果：{verification['summary']}")
    if result.get("summary"):
        turning_points.append(f"最终结果：{result['summary']}")
    return turning_points


def _build_decision_rationale(
    *,
    constraints: list[str],
    commands: list[str],
    result: dict[str, Any],
) -> list[str]:
    rationale: list[str] = []
    if constraints:
        rationale.append(f"优先满足显式约束：{'；'.join(constraints[:2])}")
    if commands:
        rationale.append(f"先使用最小验证命令收敛问题：{_compact_text(commands[0], limit=72)}")
    if result.get("summary"):
        rationale.append(result["summary"])
    return _unique_preserve_order(rationale)


def _build_attempted_paths(commands: list[str], errors: list[str]) -> list[str]:
    paths = [f"执行命令：{_compact_text(command, limit=72)}" for command in commands[:3]]
    if errors:
        paths.append(f"围绕错误信号收敛：{_compact_text(errors[0], limit=72)}")
    return _unique_preserve_order(paths)


def _summarize_lesson(goal: str, commands: list[str], errors: list[str], result: dict[str, Any]) -> str:
    scope = _infer_scope(goal)["value"]
    if scope == "python-import-error":
        return "遇到 Python 导入错误时，优先检查真实包结构与导入路径，用最小测试验证修复结果，不要先依赖环境补丁。"
    if scope == "test-failure":
        return "遇到测试失败时，先用最小复现命令定位失败断点，再围绕失败信号逐步缩小修改范围。"
    if errors:
        return f"{goal} 时应优先围绕核心错误信号收敛，先解决 {errors[0]}，再做验证。"
    if commands:
        return f"{goal} 时先用最小验证命令快速定位问题，再扩大修改范围。"
    return result.get("summary") or f"{goal} 后应沉淀成可复用经验。"


def review_trace_bundle(trace: dict[str, Any]) -> dict[str, Any]:
    goal = trace.get("task_hint") or trace.get("user_request") or "untitled-task"
    events = trace.get("events", [])
    commands = [event["content"] for event in events if event.get("type") == "command"]
    errors = [event["content"] for event in events if event.get("type") == "error"]
    result = trace.get("result", {})
    verification = trace.get("verification", {})
    trace_id = trace.get("trace_id", f"trace-{_slugify(goal)}")
    episode_id = trace_id.replace("trace_", "ep_") if trace_id.startswith("trace_") else f"ep_{_slugify(goal)}"

    scope = _infer_scope(goal)
    turning_points = _build_turning_points(
        errors=errors,
        commands=commands,
        verification=verification,
        result=result,
    )
    lesson = _summarize_lesson(goal, commands, errors, result)
    episode = {
        "episode_id": episode_id,
        "trace_id": trace_id,
        "goal": goal,
        "constraints": trace.get("constraints", []),
        "workspace": trace.get("workspace"),
        "files_touched": trace.get("files_changed", []),
        "commands": commands,
        "turning_points": turning_points,
        "attempted_paths": _build_attempted_paths(commands, errors),
        "abandoned_paths": [],
        "decision_rationale": _build_decision_rationale(
            constraints=trace.get("constraints", []),
            commands=commands,
            result=result,
        ),
        "result": result.get("status", "unknown"),
        "verification": verification.get("summary") or verification.get("status", "unknown"),
        "user_feedback": "accepted" if result.get("status") == "success" else "unknown",
        "lesson": lesson,
        "scope_hint": scope["value"],
        "confidence_hint": 0.8 if verification.get("status") == "passed" else 0.55,
        "created_at": _now_utc(),
    }
    return episode


def _candidate_title(episode: dict[str, Any], candidate_type: str) -> str:
    scope_hint = episode.get("scope_hint", "general-coding-task")
    if scope_hint == "python-import-error":
        prefix = "Python 导入错误处理模式" if candidate_type == "pattern" else "Python 导入错误避坑提示"
        return prefix
    if scope_hint == "test-failure":
        prefix = "测试失败定位模式" if candidate_type == "pattern" else "测试失败反模式"
        return prefix
    goal = episode.get("goal", "任务")
    suffix = "可复用模式" if candidate_type == "pattern" else "失败警示"
    return _compact_text(f"{goal} {suffix}", limit=48)


def build_asset_effectiveness_summary(historical_help: dict[str, Any]) -> dict[str, Any]:
    activation_count = int(historical_help.get("activation_count", 0) or 0)
    supported_count = int(historical_help.get("supported_count", 0) or 0)
    supported_strong_count = int(historical_help.get("supported_strong_count", 0) or 0)
    supported_weak_count = int(historical_help.get("supported_weak_count", 0) or 0)
    weighted_support_score = round(float(historical_help.get("weighted_support_score", 0.0) or 0.0), 2)
    support_ratio = round(float(historical_help.get("support_ratio", 0.0) or 0.0), 2)

    if activation_count == 0:
        temperature = "neutral"
        review_status = "unproven"
    elif activation_count >= 4 and support_ratio < 0.2:
        temperature = "cool"
        review_status = "needs_review"
    elif activation_count >= 2 and (supported_strong_count >= 2 or support_ratio >= 0.75):
        temperature = "hot"
        review_status = "healthy"
    elif supported_count >= 1 or support_ratio >= 0.35:
        temperature = "warm"
        review_status = "healthy"
    else:
        temperature = "neutral"
        review_status = "watch"

    return {
        "activation_count": activation_count,
        "supported_count": supported_count,
        "supported_strong_count": supported_strong_count,
        "supported_weak_count": supported_weak_count,
        "weighted_support_score": weighted_support_score,
        "support_ratio": support_ratio,
        "temperature": temperature,
        "review_status": review_status,
    }


def apply_asset_effectiveness(
    asset: dict[str, Any],
    historical_help: dict[str, Any],
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    summary = build_asset_effectiveness_summary(historical_help)
    updated = dict(asset)
    updated["historical_help"] = {
        "activation_count": summary["activation_count"],
        "supported_count": summary["supported_count"],
        "supported_strong_count": summary["supported_strong_count"],
        "supported_weak_count": summary["supported_weak_count"],
        "weighted_support_score": summary["weighted_support_score"],
        "support_ratio": summary["support_ratio"],
    }
    updated["effectiveness_summary"] = summary
    updated["temperature"] = summary["temperature"]
    updated["review_status"] = summary["review_status"]
    if updated_at:
        updated["updated_at"] = updated_at
    return updated


def apply_candidate_promotion_feedback(
    candidate: dict[str, Any],
    *,
    activation_feedback: dict[str, Any] | None,
) -> dict[str, Any]:
    updated = dict(candidate)
    help_signal = (activation_feedback or {}).get("help_signal")
    signal_bonus_map = {
        "supported_strong": 0.05,
        "supported_weak": 0.02,
        "unclear": 0.0,
        None: 0.0,
    }
    signal_bonus = signal_bonus_map.get(help_signal, 0.0)
    updated["promotion_feedback"] = {
        "help_signal": help_signal,
        "signal_bonus": round(signal_bonus, 2),
        "activation_id": (activation_feedback or {}).get("activation_id"),
        "linked_asset_ids": (activation_feedback or {}).get("linked_asset_ids", []),
        "feedback_summary": (activation_feedback or {}).get("feedback_summary"),
    }
    if help_signal == "supported_strong":
        updated["promotion_readiness"] = "boosted"
    elif help_signal == "supported_weak":
        updated["promotion_readiness"] = "encouraging"
    elif help_signal == "unclear":
        updated["promotion_readiness"] = "neutral"
    else:
        updated["promotion_readiness"] = "unknown"
    return updated


def build_candidate_review_queue(
    candidates: list[dict[str, Any]],
    *,
    workspace: str,
) -> dict[str, Any]:
    readiness_weight = {
        "boosted": 0.35,
        "encouraging": 0.22,
        "neutral": 0.08,
        "unknown": 0.0,
    }
    status_weight = {
        "needs_review": 0.35,
        "approved": 0.28,
        "new": 0.18,
        "rejected": -0.8,
        "promoted": -0.5,
    }

    items = []
    for candidate in candidates:
        promotion_feedback = candidate.get("promotion_feedback", {})
        promotion_readiness = candidate.get("promotion_readiness", "unknown")
        signal_bonus = float(promotion_feedback.get("signal_bonus", 0.0) or 0.0)
        confidence_score = float(candidate.get("confidence_score", 0.0) or 0.0)
        reusability_score = float(candidate.get("reusability_score", 0.0) or 0.0)
        stability_score = float(candidate.get("stability_score", 0.0) or 0.0)
        constraint_value_score = float(candidate.get("constraint_value_score", 0.0) or 0.0)
        base_score = round(
            (
                confidence_score
                + reusability_score
                + stability_score
                + constraint_value_score
            )
            / 4,
            4,
        )
        queue_score = round(
            base_score
            + signal_bonus
            + readiness_weight.get(promotion_readiness, 0.0)
            + status_weight.get(candidate.get("status", "new"), 0.0),
            4,
        )
        reasons = []
        if candidate.get("status") == "needs_review":
            reasons.append("候选已进入 needs_review，适合优先人工审核")
        if candidate.get("status") == "approved":
            reasons.append("候选已人工通过审核，可直接进入 promote")
        if promotion_readiness in {"boosted", "encouraging"}:
            reasons.append(f"晋升准备度为 {promotion_readiness}")
        if promotion_feedback.get("help_signal"):
            reasons.append(f"最近帮助信号为 {promotion_feedback['help_signal']}")
        reasons.append(f"基础候选分为 {base_score:.2f}")

        if candidate.get("status") == "approved":
            suggested_action = "promote"
        elif candidate.get("status") == "needs_review" and promotion_readiness == "boosted":
            suggested_action = "promote"
        elif candidate.get("status") == "rejected":
            suggested_action = "ignore"
        elif candidate.get("status") == "needs_review":
            suggested_action = "review"
        elif promotion_readiness == "boosted":
            suggested_action = "review"
        else:
            suggested_action = "watch"

        items.append(
            {
                "candidate_id": candidate["candidate_id"],
                "candidate_type": candidate.get("candidate_type"),
                "knowledge_kind": candidate.get("knowledge_kind"),
                "title": candidate.get("title"),
                "status": candidate.get("status"),
                "promotion_readiness": promotion_readiness,
                "promotion_feedback": promotion_feedback,
                "review_score": round(queue_score, 2),
                "base_score": round(base_score, 2),
                "scope": candidate.get("scope"),
                "suggested_action": suggested_action,
                "reasons": reasons,
                "created_at": candidate.get("created_at"),
            }
        )

    items.sort(
        key=lambda item: (
            -float(item["review_score"]),
            item["created_at"] or "",
        )
    )

    return {
        "kind": "candidate_review_queue",
        "workspace": workspace,
        "generated_at": _now_utc(),
        "candidate_count": len(items),
        "status_summary": {
            "needs_review": sum(1 for item in items if item["status"] == "needs_review"),
            "approved": sum(1 for item in items if item["status"] == "approved"),
            "new": sum(1 for item in items if item["status"] == "new"),
            "rejected": sum(1 for item in items if item["status"] == "rejected"),
            "promoted": sum(1 for item in items if item["status"] == "promoted"),
        },
        "items": items,
    }


def extract_candidates(episode: dict[str, Any]) -> list[dict[str, Any]]:
    lesson = episode.get("lesson", "")
    success = episode.get("result") == "success"
    candidate_type = "pattern" if success else "anti_pattern"
    knowledge_kind = "pattern" if success else "anti_pattern"
    confidence = float(episode.get("confidence_hint", 0.6))
    scope = {"level": "task-family", "value": episode.get("scope_hint", "general-coding-task")}
    candidate_id = episode["episode_id"].replace("ep_", "cand_", 1)
    candidate = {
        "candidate_id": candidate_id,
        "source_episode_ids": [episode["episode_id"]],
        "workspace": episode.get("workspace"),
        "candidate_type": candidate_type,
        "knowledge_kind": knowledge_kind,
        "title": _candidate_title(episode, candidate_type),
        "content": lesson,
        "reusability_score": round(min(confidence + 0.05, 0.95), 2),
        "stability_score": round(0.7 if success else 0.62, 2),
        "confidence_score": round(confidence, 2),
        "constraint_value_score": round(0.78 if episode.get("constraints") else 0.66, 2),
        "scope": scope,
        "conflicts_with": [],
        "status": "new",
        "created_at": _now_utc(),
    }
    return [candidate]


def promote_candidate(
    candidate: dict[str, Any],
    *,
    knowledge_scope: str = "project",
    knowledge_kind: str | None = None,
) -> dict[str, Any]:
    asset_type = candidate["candidate_type"]
    asset_id = candidate["candidate_id"].replace("cand_", f"{asset_type}_", 1)
    backend_config = resolve_backend_config()
    project_identity = backend_config["project_identity"]
    backend_uris = backend_config["backend_uris"]
    project_id = candidate.get("project_id") or project_identity.get("project_id") or candidate.get("workspace")
    owning_team = candidate.get("owning_team") or project_identity.get("owning_team")
    source_project = candidate.get("source_project") or project_id
    score = (
        candidate.get("reusability_score", 0)
        + candidate.get("stability_score", 0)
        + candidate.get("confidence_score", 0)
        + candidate.get("constraint_value_score", 0)
    ) / 4
    return {
        "asset_id": asset_id,
        "workspace": candidate.get("workspace"),
        "project_id": project_id,
        "source_project": source_project,
        "owning_team": owning_team,
        "asset_type": asset_type,
        "knowledge_scope": knowledge_scope,
        "knowledge_kind": knowledge_kind or candidate.get("knowledge_kind", asset_type),
        "title": candidate["title"],
        "content": candidate["content"],
        "scope": candidate["scope"],
        "source_workspace": candidate.get("workspace"),
        "source_episode_ids": candidate["source_episode_ids"],
        "source_candidate_ids": [candidate["candidate_id"]],
        "asset_storage": candidate.get(
            "asset_storage",
            {
                "backend": backend_config["source_of_truth"],
                "uri": backend_uris.get("asset_store"),
                "portable": True,
            },
        ),
        "state_index": candidate.get(
            "state_index",
            {
                "backend": backend_config["state_index"],
                "uri": backend_uris.get("state_index"),
                "portable": True,
            },
        ),
        "retrieval_index": candidate.get(
            "retrieval_index",
            {
                "backend": backend_config["retrieval"],
                "uri": backend_uris.get("retrieval_index"),
                "portable": True,
            },
        ),
        "delivery": {
            "portable": True,
            "shareable": bool(backend_config["shareable_enabled"]) or knowledge_scope in {"project", "cross-project"},
            "owner": "project" if knowledge_scope == "project" else "team",
            "mode": backend_config["profile"],
        },
        "confidence": round(score, 2),
        "status": "active",
        "last_used_at": None,
        "created_at": _now_utc(),
        "updated_at": _now_utc(),
    }


def _candidate_as_asset(candidate: dict[str, Any]) -> dict[str, Any]:
    backend_config = resolve_backend_config()
    project_identity = backend_config["project_identity"]
    backend_uris = backend_config["backend_uris"]
    project_id = candidate.get("project_id") or project_identity.get("project_id") or candidate.get("workspace")
    owning_team = candidate.get("owning_team") or project_identity.get("owning_team")
    source_project = candidate.get("source_project") or project_id
    return {
        "asset_id": candidate["candidate_id"],
        "workspace": candidate.get("workspace"),
        "project_id": project_id,
        "source_project": source_project,
        "owning_team": owning_team,
        "asset_type": candidate["candidate_type"],
        "knowledge_scope": candidate.get("knowledge_scope", "project"),
        "knowledge_kind": candidate.get("knowledge_kind", candidate.get("candidate_type", "pattern")),
        "title": candidate["title"],
        "content": candidate["content"],
        "scope": candidate["scope"],
        "source_workspace": candidate.get("workspace"),
        "source_episode_ids": candidate.get("source_episode_ids", []),
        "source_candidate_ids": [candidate["candidate_id"]],
        "asset_storage": candidate.get(
            "asset_storage",
            {
                "backend": backend_config["source_of_truth"],
                "uri": backend_uris.get("asset_store"),
                "portable": True,
            },
        ),
        "state_index": candidate.get(
            "state_index",
            {
                "backend": backend_config["state_index"],
                "uri": backend_uris.get("state_index"),
                "portable": True,
            },
        ),
        "retrieval_index": candidate.get(
            "retrieval_index",
            {
                "backend": backend_config["retrieval"],
                "uri": backend_uris.get("retrieval_index"),
                "portable": True,
            },
        ),
        "delivery": {
            "portable": True,
            "shareable": True,
            "owner": "project",
            "mode": backend_config["profile"],
        },
        "confidence": candidate.get("confidence_score", 0.6),
        "status": candidate.get("status", "candidate"),
        "last_used_at": None,
        "created_at": candidate.get("created_at"),
        "updated_at": candidate.get("created_at"),
    }


def _match_score(task: str, scope: dict[str, str], asset: dict[str, Any], workspace: str) -> float:
    return _match_details(task, scope, asset, workspace)["score"]


def _match_details(task: str, scope: dict[str, str], asset: dict[str, Any], workspace: str) -> dict[str, Any]:
    base_score = float(asset.get("confidence", 0.5))
    score = base_score
    task_lower = task.lower()
    title = asset.get("title", "").lower()
    content = asset.get("content", "").lower()
    scope_value = scope.get("value", "")
    asset_scope = asset.get("scope", {})
    knowledge_scope = asset.get("knowledge_scope", "project")
    knowledge_kind = asset.get("knowledge_kind", asset.get("asset_type", "pattern"))
    vector_score = float(asset.get("vector_score", 0.0))
    task_tokens = _task_tokens(task)
    title_hits = [token for token in task_tokens if token in title]
    content_hits = [token for token in task_tokens if token in content]
    evidence: list[str] = []
    risk_flags: list[str] = []
    evidence_bonus = 0.0
    penalty_score = 0.0
    historical_help = asset.get("historical_help", {})
    effectiveness_summary = asset.get("effectiveness_summary") or build_asset_effectiveness_summary(historical_help)
    temperature = effectiveness_summary.get("temperature", "neutral")
    review_status = effectiveness_summary.get("review_status", "unproven")

    if scope_value and scope_value in (asset_scope.get("value") or ""):
        score += 0.25
        evidence_bonus += 0.25
        evidence.append(f"作用域值命中 {scope['level']}::{scope_value}")
    if asset_scope.get("level") == scope.get("level"):
        score += 0.08
        evidence_bonus += 0.08
        evidence.append(f"作用域层级对齐 {scope['level']}")
    if _same_workspace_path(asset.get("workspace"), workspace) or _same_workspace_path(
        asset.get("source_workspace"), workspace
    ):
        score += 0.22
        evidence_bonus += 0.22
        evidence.append("来源 workspace 与当前项目一致")
    if knowledge_scope == "project":
        score += 0.2
        evidence_bonus += 0.2
        evidence.append("项目内经验在当前任务中优先排序")
    elif knowledge_scope == "cross-project":
        score += 0.08
        evidence_bonus += 0.08
        evidence.append("跨项目经验参与补充召回")
        risk_flags.append("跨项目经验可能缺少当前项目上下文，使用时应核对适用边界。")
    activation_count = int(historical_help.get("activation_count", 0) or 0)
    supported_count = int(historical_help.get("supported_count", 0) or 0)
    supported_strong_count = int(historical_help.get("supported_strong_count", 0) or 0)
    supported_weak_count = int(historical_help.get("supported_weak_count", 0) or 0)
    support_ratio = float(historical_help.get("support_ratio", 0.0) or 0.0)
    if activation_count:
        help_bonus = round(
            min(
                support_ratio * 0.16
                + min(supported_strong_count * 0.04 + supported_weak_count * 0.02, 0.1),
                0.24,
            ),
            4,
        )
        score += help_bonus
        evidence_bonus += help_bonus
        evidence.append(
            f"历史激活 {activation_count} 次，其中强帮助 {supported_strong_count} 次、弱帮助 {supported_weak_count} 次"
        )
        if activation_count >= 2 and supported_count == 0:
            risk_flags.append("已有多次激活记录，但尚未观察到正向帮助信号。")
            penalty_score += 0.08
    else:
        risk_flags.append("尚无历史帮助信号，当前仍以静态证据排序。")
    if temperature == "hot":
        score += 0.08
        evidence_bonus += 0.08
        evidence.append("资产处于 hot 温度区间，历史帮助信号稳定")
    elif temperature == "warm":
        score += 0.04
        evidence_bonus += 0.04
        evidence.append("资产处于 warm 温度区间，已有一定帮助记录")
    elif temperature == "cool":
        penalty_score += 0.08
        risk_flags.append("资产已进入 cool 温度区间，近期帮助信号偏弱。")
    if review_status == "needs_review":
        penalty_score += 0.12
        risk_flags.append("资产已标记为 needs_review，建议优先人工复核。")
    elif review_status == "watch":
        penalty_score += 0.04
        risk_flags.append("资产处于 watch 状态，仍需继续观察实际帮助效果。")
    if title_hits:
        score += 0.12
        evidence_bonus += 0.12
        evidence.append(f"标题命中关键词：{', '.join(title_hits[:3])}")
    if content_hits:
        score += 0.08
        evidence_bonus += 0.08
        evidence.append(f"内容命中关键词：{', '.join(content_hits[:3])}")
    distinctive_hits = _unique_preserve_order(
        [token for token in [*title_hits, *content_hits] if len(token) >= 6]
    )
    if distinctive_hits:
        distinctive_bonus = min(0.18 * len(distinctive_hits), 0.72)
        score += distinctive_bonus
        evidence_bonus += distinctive_bonus
        evidence.append(f"特征关键词命中：{', '.join(distinctive_hits[:3])}")
    if asset.get("status") == "active":
        score += 0.05
        evidence_bonus += 0.05
        evidence.append("资产状态为 active")
    else:
        risk_flags.append("当前命中对象尚未进入 active 资产层，可能仍偏候选经验。")
    if vector_score:
        vector_bonus = max(min(vector_score, 1.0), 0.0) * 0.35
        score += vector_bonus
        evidence_bonus += vector_bonus
        evidence.append(f"语义召回分数 {vector_score:.2f}")
    if "milvus" in asset.get("retrieval_sources", []):
        score += 0.18
        evidence_bonus += 0.18
        evidence.append("Milvus 语义召回来源优先于 SQLite 状态索引")

    type_weight = {"rule": 0.2, "context": 0.14, "pattern": 0.14, "checklist": 0.1, "anti_pattern": 0.08}
    type_bonus = type_weight.get(knowledge_kind, type_weight.get(asset.get("asset_type", ""), 0.0))
    score += type_bonus
    evidence_bonus += type_bonus
    evidence.append(f"知识类型 {knowledge_kind} 具有当前排序权重")

    if asset_scope.get("level") == "workspace" and asset_scope.get("value") == "general-coding-task":
        risk_flags.append("作用域较宽，仅能提供通用上下文，需防止泛化误召回。")
        penalty_score += 0.18
    if scope_value and asset_scope.get("value") not in ("", scope_value):
        risk_flags.append("资产作用域与当前任务不完全一致，命中更多依赖其他证据。")
        penalty_score += 0.16
    if not title_hits and not content_hits and vector_score <= 0:
        risk_flags.append("缺少标题、内容或语义召回证据，当前命中主要依赖作用域与基础权重。")
        penalty_score += 0.14
    if float(asset.get("confidence", 0.0)) < 0.75:
        risk_flags.append(f"资产置信度偏低（{float(asset.get('confidence', 0.0)):.2f}），建议人工复核。")
        penalty_score += round(min((0.75 - float(asset.get("confidence", 0.0))) * 0.6, 0.12), 2)
    if not scope_value and task_lower and knowledge_scope == "cross-project":
        risk_flags.append("任务作用域较模糊，跨项目经验更容易出现误召回。")
        penalty_score += 0.06
    if knowledge_scope == "cross-project" and asset_scope.get("value") == "general-coding-task":
        penalty_score += 0.06
    if asset.get("status") != "active":
        penalty_score += 0.1

    score -= penalty_score
    if penalty_score:
        evidence.append(f"排序惩罚 {penalty_score:.2f}，用于抑制宽 scope 或低证据命中")

    return {
        "score": round(score, 4),
        "base_score": round(base_score, 4),
        "evidence_bonus": round(evidence_bonus, 4),
        "penalty_score": round(penalty_score, 4),
        "historical_help": {
            "activation_count": activation_count,
            "supported_count": supported_count,
            "supported_strong_count": supported_strong_count,
            "supported_weak_count": supported_weak_count,
            "weighted_support_score": round(float(historical_help.get("weighted_support_score", 0.0) or 0.0), 2),
            "support_ratio": round(support_ratio, 2),
        },
        "title_hits": title_hits,
        "content_hits": content_hits,
        "distinctive_hits": distinctive_hits,
        "effectiveness_summary": effectiveness_summary,
        "evidence": _unique_preserve_order(evidence),
        "risk_flags": _unique_preserve_order(risk_flags),
    }


def _merge_assets(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for asset in [*secondary, *primary]:
        asset_id = asset.get("asset_id")
        if not asset_id:
            continue
        if asset_id not in merged:
            item = dict(asset)
            item["retrieval_sources"] = _unique_preserve_order(item.get("retrieval_sources", []))
            merged[asset_id] = item
            continue
        current = merged[asset_id]
        combined = dict(current)
        combined.update({key: value for key, value in asset.items() if value not in (None, "", [], {})})
        combined["vector_score"] = max(float(current.get("vector_score", 0.0)), float(asset.get("vector_score", 0.0)))
        combined["retrieval_sources"] = _unique_preserve_order(
            [
                *current.get("retrieval_sources", []),
                *asset.get("retrieval_sources", []),
            ]
        )
        merged[asset_id] = combined
    return list(merged.values())


def _tag_retrieval_source(assets: list[dict[str, Any]], source: str) -> None:
    for asset in assets:
        asset["retrieval_sources"] = _unique_preserve_order([*asset.get("retrieval_sources", []), source])


def _hydrate_assets_from_sqlite(db_path: Path | None, asset_ids: list[str]) -> list[dict[str, Any]]:
    if not db_path:
        return []
    hydrated = []
    for asset_id in asset_ids:
        asset = get_asset(db_path, asset_id=asset_id)
        if asset:
            hydrated.append(asset)
    _tag_retrieval_source(hydrated, "sqlite-hydration")
    return hydrated


def _hydrate_assets_from_json(assets_dir: Path, asset_ids: list[str], source: str) -> list[dict[str, Any]]:
    if not assets_dir.exists() or not asset_ids:
        return []
    wanted = set(asset_ids)
    hydrated = [asset for asset in iter_json_objects(assets_dir) if asset.get("asset_id") in wanted]
    _tag_retrieval_source(hydrated, source)
    return hydrated


def _source_provenance(asset: dict[str, Any], workspace: str) -> dict[str, Any]:
    source_workspace = asset.get("source_workspace") or asset.get("workspace")
    same_project = _same_workspace_path(source_workspace, workspace)
    retrieval_sources = _unique_preserve_order(asset.get("retrieval_sources", []))
    knowledge_scope = asset.get("knowledge_scope", "project")

    if "candidate-fallback" in retrieval_sources:
        source_kind = "candidate_fallback"
    elif same_project:
        source_kind = "current_project"
    elif knowledge_scope == "cross-project":
        source_kind = "cross_project"
    else:
        source_kind = "project_asset"

    return {
        "source_kind": source_kind,
        "knowledge_scope": knowledge_scope,
        "source_workspace": source_workspace,
        "same_project": same_project,
        "storage_sources": retrieval_sources,
        "source_episode_ids": asset.get("source_episode_ids", []),
        "source_candidate_ids": asset.get("source_candidate_ids", []),
        "data_source_confirmed": bool(retrieval_sources or source_workspace or asset.get("source_episode_ids")),
    }


def _llm_use_guidance(asset: dict[str, Any], details: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    risk_flags = details.get("risk_flags", [])
    review_status = details.get("effectiveness_summary", {}).get("review_status", "unproven")
    temperature = details.get("effectiveness_summary", {}).get("temperature", "neutral")
    evidence_count = len(details.get("evidence", []))

    if review_status == "needs_review" or temperature == "cool":
        suggested_action = "verify_before_use"
    elif provenance["source_kind"] == "current_project" and not risk_flags:
        suggested_action = "prefer_if_relevant"
    elif provenance["source_kind"] == "cross_project":
        suggested_action = "use_as_reference"
    else:
        suggested_action = "consider_with_context"

    checks = [
        "Use only if the current task, codebase, and constraints match the source evidence.",
        "Ignore this asset if its source project, scope, or risks do not fit the current context.",
    ]
    if provenance["source_kind"] == "cross_project":
        checks.append("Treat cross-project experience as inspiration, not a project rule.")
    if risk_flags:
        checks.append("Review risk_flags before applying the recommendation.")
    if evidence_count <= 2:
        checks.append("Evidence is thin; prefer local code inspection over this memory if they disagree.")

    return {
        "decision_owner": "llm",
        "suggested_action": suggested_action,
        "checks": _unique_preserve_order(checks),
    }


def _selected_activation_item(
    *,
    score: float,
    asset: dict[str, Any],
    details: dict[str, Any],
    provenance: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    evidence = details["evidence"]
    if "milvus" in asset.get("retrieval_sources", []):
        milvus_evidence = [item for item in evidence if "Milvus 语义召回来源优先" in item]
        evidence = _unique_preserve_order([*milvus_evidence, *evidence])
    return {
        "asset_id": asset["asset_id"],
        "asset_type": asset["asset_type"],
        "knowledge_scope": asset.get("knowledge_scope", "project"),
        "knowledge_kind": asset.get("knowledge_kind", asset.get("asset_type", "pattern")),
        "title": asset["title"],
        "reason": reason,
        "match_score": round(score, 2),
        "score_breakdown": {
            "base_score": round(details["base_score"], 2),
            "evidence_bonus": round(details["evidence_bonus"], 2),
            "penalty_score": round(details["penalty_score"], 2),
        },
        "historical_help": details["historical_help"],
        "effectiveness_summary": details["effectiveness_summary"],
        "temperature": details["effectiveness_summary"]["temperature"],
        "review_status": details["effectiveness_summary"]["review_status"],
        "retrieval_sources": asset.get("retrieval_sources", []),
        "source_provenance": provenance,
        "llm_use_guidance": _llm_use_guidance(asset, details, provenance),
        "vector_score": round(float(asset.get("vector_score", 0.0)), 4),
        "match_evidence": evidence[:8],
        "risk_flags": details["risk_flags"][:5],
    }


def _retrieve_activation_assets(
    *,
    workspace: Path,
    workspace_str: str,
    query_text: str,
    assets_dir: Path,
    candidates_dir: Path,
    db_path: Path | None = None,
) -> dict[str, Any]:
    assets_dir.mkdir(parents=True, exist_ok=True)
    shared_assets_dir = shared_memory_root() / "assets"
    sync_assets_directory(default_milvus_db_path(workspace), assets_dir)
    sync_assets_directory(shared_milvus_db_path(), shared_assets_dir)

    vector_project_assets = search_asset_vectors(
        default_milvus_db_path(workspace),
        query_text=query_text,
        limit=5,
        knowledge_scope="project",
        workspace=workspace_str,
    )
    _tag_retrieval_source(vector_project_assets, "milvus")
    vector_shared_assets = search_asset_vectors(
        shared_milvus_db_path(),
        query_text=query_text,
        limit=5,
        knowledge_scope="cross-project",
    )
    _tag_retrieval_source(vector_shared_assets, "milvus")

    vector_assets = [*vector_project_assets, *vector_shared_assets]
    used_sqlite_fallback = False
    used_json_fallback = False
    used_milvus_primary = bool(vector_assets)

    if used_milvus_primary:
        vector_asset_ids = [asset["asset_id"] for asset in vector_assets if asset.get("asset_id")]
        hydrated_assets = _hydrate_assets_from_sqlite(db_path, vector_asset_ids)
        if not hydrated_assets:
            hydrated_assets = _hydrate_assets_from_json(assets_dir, vector_asset_ids, "json-hydration")
        shared_hydrated_assets = _hydrate_assets_from_json(shared_assets_dir, vector_asset_ids, "shared-json-hydration")
        assets = _merge_assets([*hydrated_assets, *shared_hydrated_assets], vector_assets)
    else:
        assets = []
        if db_path:
            assets = list_assets(db_path, workspace=workspace_str)
            _tag_retrieval_source(assets, "sqlite")
            used_sqlite_fallback = bool(assets)
        if not assets:
            assets = list(iter_json_objects(assets_dir))
            _tag_retrieval_source(assets, "json")
            used_json_fallback = bool(assets)

        shared_assets = list(iter_json_objects(shared_assets_dir)) if shared_assets_dir.exists() else []
        _tag_retrieval_source(shared_assets, "shared-json")
        assets = _merge_assets(assets, shared_assets)

    for asset in assets:
        asset.setdefault("knowledge_scope", "project")
        asset.setdefault("knowledge_kind", asset.get("asset_type", "pattern"))
        if "shared-json" in asset.get("retrieval_sources", []) or "shared-json-hydration" in asset.get(
            "retrieval_sources", []
        ):
            asset.setdefault("knowledge_scope", "cross-project")
            asset.setdefault("workspace", None)

    used_candidate_fallback = False
    if not assets:
        used_candidate_fallback = True
        candidates: list[dict[str, Any]] = []
        if db_path:
            candidates = list_candidates(db_path, workspace=workspace_str)
        if not candidates:
            candidates = list(iter_json_objects(candidates_dir))
        assets = [_candidate_as_asset(candidate) for candidate in candidates]
        _tag_retrieval_source(assets, "candidate-fallback")

    return {
        "assets": assets,
        "vector_project_assets": vector_project_assets,
        "vector_shared_assets": vector_shared_assets,
        "used_sqlite_index": bool(db_path and db_path.exists()),
        "used_milvus_primary": used_milvus_primary,
        "used_sqlite_fallback": used_sqlite_fallback,
        "used_json_fallback": used_json_fallback,
        "used_candidate_fallback": used_candidate_fallback,
    }


def _rerank_activation_assets(
    *,
    task: str,
    scope: dict[str, str],
    workspace_str: str,
    assets: list[dict[str, Any]],
    db_path: Path | None = None,
) -> list[tuple[float, dict[str, Any], dict[str, Any]]]:
    scored_assets: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    feedback_stats = (
        summarize_asset_feedback(db_path, asset_ids=[asset.get("asset_id") for asset in assets if asset.get("asset_id")])
        if db_path
        else {}
    )

    for asset in assets:
        if asset.get("asset_id") in feedback_stats:
            asset = apply_asset_effectiveness(asset, feedback_stats[asset["asset_id"]])
        details = _match_details(task, scope, asset, workspace_str)
        scored_assets.append((details["score"], asset, details))

    scored_assets.sort(key=lambda item: item[0], reverse=True)
    return scored_assets


def _select_activation_assets(
    scored_assets: list[tuple[float, dict[str, Any], dict[str, Any]]],
    *,
    workspace_str: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    selected: list[dict[str, Any]] = []
    selection_risks = _unique_preserve_order(
        [
            risk
            for _, _, details in scored_assets[:5]
            for risk in details.get("risk_flags", [])
        ]
    )
    selection_adjustments: list[str] = []

    for score, asset, details in scored_assets[:5]:
        provenance = _source_provenance(asset, workspace_str)
        selected.append(
            _selected_activation_item(
                score=score,
                asset=asset,
                details=details,
                provenance=provenance,
                reason=f"候选来源已确认，匹配分数 {score:.2f}；是否采用应由模型结合当前上下文判断。",
            )
        )

    selected_ids = {item["asset_id"] for item in selected}
    milvus_first_candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for score, asset, details in scored_assets:
        if asset.get("asset_id") in selected_ids:
            continue
        if "milvus" not in asset.get("retrieval_sources", []):
            continue
        if details["effectiveness_summary"].get("review_status") == "needs_review":
            continue
        if details["effectiveness_summary"].get("temperature") == "cool":
            continue
        if float(asset.get("vector_score", 0.0) or 0.0) < 0.2:
            continue
        milvus_first_candidates.append((score, asset, details))

    selected_milvus_count = sum(1 for item in selected if "milvus" in item.get("retrieval_sources", []))
    desired_milvus_slots = min(3, len(selected), selected_milvus_count + len(milvus_first_candidates))
    while milvus_first_candidates and selected_milvus_count < desired_milvus_slots:
        replacement_index = None
        for index in range(len(selected) - 1, -1, -1):
            if "milvus" not in selected[index].get("retrieval_sources", []):
                replacement_index = index
                break
        if replacement_index is None:
            break
        score, asset, details = milvus_first_candidates.pop(0)
        replaced = selected[replacement_index]
        provenance = _source_provenance(asset, workspace_str)
        selected[replacement_index] = _selected_activation_item(
            score=score,
            asset=asset,
            details=details,
            provenance=provenance,
            reason=f"候选来源已确认，匹配分数 {score:.2f}；因 Milvus-first 默认策略进入最终 selected_assets。",
        )
        selected_ids.add(asset["asset_id"])
        selected_milvus_count += 1
        selection_adjustments.append(
            f"Milvus-first 默认策略保留 {asset['asset_id']}，替换掉 SQLite-only 候选 {replaced['asset_id']}。"
        )

    strongest_milvus_probe: tuple[float, dict[str, Any], dict[str, Any]] | None = None
    for score, asset, details in scored_assets:
        if asset.get("asset_id") in selected_ids:
            continue
        if "milvus" not in asset.get("retrieval_sources", []):
            continue
        if details["effectiveness_summary"].get("review_status") != "unproven":
            continue
        vector_score = float(asset.get("vector_score", 0.0) or 0.0)
        if vector_score < 0.7:
            continue
        if not details.get("title_hits"):
            continue
        strongest_milvus_probe = (score, asset, details)
        break

    if strongest_milvus_probe is not None and selected:
        replacement_index = len(selected) - 1
        for index in range(len(selected) - 1, -1, -1):
            item = selected[index]
            activation_count = int(item.get("historical_help", {}).get("activation_count", 0) or 0)
            if (
                "milvus" not in item.get("retrieval_sources", [])
                and item.get("review_status") != "unproven"
                and activation_count >= 3
            ):
                replacement_index = index
                break
        replaced = selected[replacement_index]
        score, asset, details = strongest_milvus_probe
        provenance = _source_provenance(asset, workspace_str)
        selected[replacement_index] = _selected_activation_item(
            score=score,
            asset=asset,
            details=details,
            provenance=provenance,
            reason=f"候选来源已确认，匹配分数 {score:.2f}；因 Milvus 强语义命中被保留为试用位。",
        )
        selection_adjustments.append(
            "保留了 1 个 Milvus 试用位：高语义命中的 unproven 资产可以进入最终 selected_assets，避免被高证据旧资产完全压制。"
        )
        selection_adjustments.append(
            f"Milvus 试用位命中 {asset['asset_id']}，替换掉 {replaced['asset_id']}。"
        )

    return selected, selection_risks, selection_adjustments


def _build_activation_why_selected(
    *,
    scope: dict[str, str],
    constraints: list[str],
    retrieval: dict[str, Any],
) -> list[str]:
    why_selected = [
        f"scope 命中 {scope['level']}::{scope['value']}",
        "召回结果作为带来源候选提供，最终是否采用由 LLM 基于当前上下文判断",
        "排序只表示候选优先级，不代表必须使用",
        "每条候选都携带 source_provenance、match_evidence 与 risk_flags",
        "宽 scope、低证据、低置信命中会被显式降权，但不会替代 LLM 判断",
        "默认优先保留 Milvus 语义召回候选，SQLite 主要作为状态索引、反馈统计与降级来源",
    ]
    if constraints:
        why_selected.append("显式约束被纳入激活说明")
    if retrieval["used_milvus_primary"]:
        why_selected.append("Milvus 已作为 primary retrieval 生成本次候选池")
    if retrieval["used_sqlite_index"]:
        why_selected.append("SQLite 作为轻量状态索引用于反馈、日志和 Milvus 命中的元数据补全")
    if retrieval["used_sqlite_fallback"] or retrieval["used_json_fallback"]:
        why_selected.append("Milvus 未返回可用候选，本次已降级到本地资产 fallback")
    if retrieval["used_candidate_fallback"]:
        why_selected.append("未找到 active asset，已回退到 candidate 经验层")
    return why_selected


def _assemble_activation_context(
    scored_assets: list[tuple[float, dict[str, Any], dict[str, Any]]],
    *,
    constraints: list[str],
) -> tuple[list[str], list[str]]:
    rendered_context = [
        f"[{asset.get('knowledge_scope', 'project')}/{asset.get('knowledge_kind', asset.get('asset_type', 'pattern'))}] {asset['content']}"
        for _, asset, _ in scored_assets[: min(3, len(scored_assets))]
    ]
    if constraints:
        rendered_context.append(f"当前约束：{'；'.join(constraints)}")

    fallback_episode_refs = [
        ref
        for _, asset, _ in scored_assets[:3]
        for ref in asset.get("source_episode_ids", [])
    ]
    return rendered_context, fallback_episode_refs


def _build_retrieval_summary(selected: list[dict[str, Any]], retrieval: dict[str, Any]) -> dict[str, int]:
    return {
        "milvus_project_candidates": len(retrieval["vector_project_assets"]),
        "milvus_shared_candidates": len(retrieval["vector_shared_assets"]),
        "selected_from_milvus": sum(1 for item in selected if "milvus" in item.get("retrieval_sources", [])),
        "selected_from_sqlite": sum(1 for item in selected if "sqlite" in item.get("retrieval_sources", [])),
        "selected_with_sqlite_hydration": sum(
            1 for item in selected if "sqlite-hydration" in item.get("retrieval_sources", [])
        ),
        "selected_from_json": sum(1 for item in selected if "json" in item.get("retrieval_sources", []) or "shared-json" in item.get("retrieval_sources", [])),
        "selected_with_json_hydration": sum(
            1
            for item in selected
            if "json-hydration" in item.get("retrieval_sources", [])
            or "shared-json-hydration" in item.get("retrieval_sources", [])
        ),
        "selected_from_candidate_fallback": sum(1 for item in selected if "candidate-fallback" in item.get("retrieval_sources", [])),
        "used_milvus_primary": int(bool(retrieval["used_milvus_primary"])),
        "used_sqlite_fallback": int(bool(retrieval["used_sqlite_fallback"])),
        "used_json_fallback": int(bool(retrieval["used_json_fallback"])),
    }


def activate_assets(
    *,
    task: str,
    workspace: Path,
    constraints: list[str],
    assets_dir: Path,
    candidates_dir: Path,
    db_path: Path | None = None,
) -> dict[str, Any]:
    scope = _infer_scope(task)
    workspace_str = str(workspace)
    query_text = task if not constraints else f"{task} {' '.join(constraints)}"
    retrieval = _retrieve_activation_assets(
        workspace=workspace,
        workspace_str=workspace_str,
        query_text=query_text,
        assets_dir=assets_dir,
        candidates_dir=candidates_dir,
        db_path=db_path,
    )
    scored_assets = _rerank_activation_assets(
        task=task,
        scope=scope,
        workspace_str=workspace_str,
        assets=retrieval["assets"],
        db_path=db_path,
    )
    selected, selection_risks, selection_adjustments = _select_activation_assets(
        scored_assets, workspace_str=workspace_str
    )
    rendered_context, fallback_episode_refs = _assemble_activation_context(scored_assets, constraints=constraints)

    return {
        "activation_id": f"act_{_slugify(task)}",
        "task_query": task,
        "workspace": str(workspace),
        "selected_assets": selected,
        "why_selected": _build_activation_why_selected(scope=scope, constraints=constraints, retrieval=retrieval),
        "selection_risks": selection_risks,
        "selection_adjustments": selection_adjustments,
        "retrieval_summary": _build_retrieval_summary(selected, retrieval),
        "pipeline": {
            "kind": "experience_rag_activation",
            "stages": ["retrieve", "rerank", "assemble"],
        },
        "rendered_context": rendered_context,
        "fallback_episode_refs": fallback_episode_refs,
        "created_at": _now_utc(),
    }


def should_promote_candidate(
    candidate: dict[str, Any],
    *,
    verification_status: str,
    result_status: str,
    min_score: float = 0.70,
) -> bool:
    if verification_status != "passed":
        return False
    if result_status != "success":
        return False
    scores = [
        float(candidate.get("reusability_score", 0.0)),
        float(candidate.get("stability_score", 0.0)),
        float(candidate.get("confidence_score", 0.0)),
        float(candidate.get("constraint_value_score", 0.0)),
    ]
    signal_bonus = float(candidate.get("promotion_feedback", {}).get("signal_bonus", 0.0))
    adjusted_scores = [round(score + signal_bonus, 4) for score in scores]
    return min(adjusted_scores) >= min_score


def explain_object(payload: dict[str, Any]) -> dict[str, Any]:
    if "episode_id" in payload:
        return {
            "kind": "episode",
            "id": payload["episode_id"],
            "explanation": [
                "episode 是从 trace bundle 提炼出的任务级案例。",
                f"它围绕目标“{payload.get('goal', 'unknown')}”记录约束、转折点与 lesson。",
            ],
            "source_refs": [payload.get("trace_id")],
        }
    if "candidate_id" in payload:
        return {
            "kind": "candidate",
            "id": payload["candidate_id"],
            "explanation": [
                "candidate 是进入长期资产层之前的缓冲层。",
                f"当前类型为 {payload.get('candidate_type')}，状态为 {payload.get('status')}",
            ],
            "source_refs": payload.get("source_episode_ids", []),
        }
    if "asset_id" in payload:
        return {
            "kind": "asset",
            "id": payload["asset_id"],
            "explanation": [
                "asset 是可以参与激活排序的长期经验对象。",
                f"当前类型为 {payload.get('asset_type')}，置信度为 {payload.get('confidence')}",
            ],
            "source_refs": payload.get("source_episode_ids", []) + payload.get("source_candidate_ids", []),
        }
    if "activation_id" in payload:
        selected_assets = payload.get("selected_assets", [])
        top_asset = selected_assets[0] if selected_assets else None
        explanation = [
            "activation view 是面向当前任务动态拼装的最小激活包。",
            f"本次共选择了 {len(selected_assets)} 条经验。",
        ]
        if top_asset:
            top_evidence = top_asset.get("match_evidence", [])
            explanation.append(
                f"首条经验 {top_asset.get('asset_id')} 的主要命中依据：{'；'.join(top_evidence[:2]) or top_asset.get('reason', '未记录')}。"
            )
            historical_help = top_asset.get("historical_help", {})
            if historical_help.get("activation_count"):
                explanation.append(
                    "它在历史上被激活 "
                    f"{historical_help['activation_count']} 次，其中强帮助 "
                    f"{historical_help.get('supported_strong_count', 0)} 次、弱帮助 "
                    f"{historical_help.get('supported_weak_count', 0)} 次。"
                )
        if payload.get("selection_risks"):
            explanation.append(f"当前激活需要重点留意：{payload['selection_risks'][0]}")
        if payload.get("feedback", {}).get("help_signal"):
            explanation.append(f"本次激活后续反馈信号为：{payload['feedback']['help_signal']}。")
        return {
            "kind": "activation_view",
            "id": payload["activation_id"],
            "explanation": explanation,
            "source_refs": payload.get("fallback_episode_refs", []),
        }
    return {
        "kind": "unknown",
        "id": None,
        "explanation": ["无法识别对象类型。"],
        "source_refs": [],
    }


def build_trace_bundle(
    *,
    workspace: Path,
    task: str,
    user_request: str | None,
    constraints: list[str],
    commands: list[str],
    errors: list[str],
    files_changed: list[str],
    verification_status: str,
    verification_summary: str | None,
    result_status: str,
    result_summary: str | None,
    host: str = "codex",
    session_id: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    task_hint = task.strip() or (user_request or "untitled-task")
    now = _now_utc()
    resolved_trace_id = trace_id or f"trace_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{_slugify(task_hint)[:24]}"
    events: list[dict[str, Any]] = []
    for command in commands:
        events.append({"type": "command", "content": command, "important": True})
    for error in errors:
        events.append({"type": "error", "content": error, "important": True})

    return {
        "trace_id": resolved_trace_id,
        "host": host,
        "workspace": str(workspace.resolve()),
        "session_id": session_id,
        "task_hint": task_hint,
        "user_request": user_request or task_hint,
        "constraints": constraints,
        "events": events,
        "files_changed": files_changed,
        "verification": {
            "commands": commands,
            "status": verification_status,
            "summary": verification_summary,
        },
        "result": {
            "status": result_status,
            "summary": result_summary,
        },
        "artifacts": {},
        "timestamps": {
            "started_at": now,
            "ended_at": now,
        },
    }
