from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.core.injection_policy import REFERENCE_SUMMARY, RUNTIME_CONTEXT, SYSTEM_PROMPT
from runtime.storage.fs_store import memory_root_for_workspace, save_json


CHANNEL_TITLES = {
    SYSTEM_PROMPT: "System Prompt Priors",
    RUNTIME_CONTEXT: "Runtime Context",
    REFERENCE_SUMMARY: "Reference Summary",
}

CHANNEL_ORDER = (SYSTEM_PROMPT, RUNTIME_CONTEXT, REFERENCE_SUMMARY)


def _compact(value: str, limit: int) -> str:
    cleaned = " ".join(str(value).split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "..."


def _plan_channels(activation: dict[str, Any]) -> dict[str, Any]:
    plan = activation.get("injection_plan") if isinstance(activation.get("injection_plan"), dict) else {}
    channels = plan.get("channels") if isinstance(plan.get("channels"), dict) else {}
    return channels if isinstance(channels, dict) else {}


def _channel_items(activation: dict[str, Any], channel: str) -> list[dict[str, Any]]:
    channel_payload = _plan_channels(activation).get(channel)
    if not isinstance(channel_payload, dict):
        return []
    items = channel_payload.get("items")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def injection_artifact_payload(activation: dict[str, Any]) -> dict[str, Any]:
    return {
        "activation_id": activation.get("activation_id"),
        "task_query": activation.get("task_query"),
        "workspace": activation.get("workspace"),
        "created_at": activation.get("created_at"),
        "policy": (activation.get("injection_plan") or {}).get("policy"),
        "channel_counts": (activation.get("injection_plan") or {}).get("channel_counts", {}),
        "channels": {
            channel: {
                "title": CHANNEL_TITLES[channel],
                "items": _channel_items(activation, channel),
            }
            for channel in CHANNEL_ORDER
        },
    }


def render_injection_markdown(activation: dict[str, Any], *, max_chars: int | None = None) -> str:
    lines = [
        "# expcap injection context",
        "",
        f"- activation_id: `{activation.get('activation_id', '')}`",
        f"- task: {_compact(str(activation.get('task_query') or ''), 240)}",
        "",
    ]
    for channel in CHANNEL_ORDER:
        items = _channel_items(activation, channel)
        lines.append(f"## {CHANNEL_TITLES[channel]}")
        if not items:
            lines.append("- None")
            lines.append("")
            continue
        for item in items:
            kind = item.get("knowledge_kind") or item.get("asset_type") or "unknown"
            title = _compact(str(item.get("title") or item.get("asset_id") or "Untitled"), 120)
            content = _compact(str(item.get("content") or ""), 900 if channel == REFERENCE_SUMMARY else 420)
            lines.append(f"- [{kind}] {title}")
            if content:
                lines.append(f"  {content}")
        lines.append("")
    rendered = "\n".join(lines).rstrip() + "\n"
    if max_chars is not None and len(rendered) > max_chars:
        return rendered[: max_chars - 4].rstrip() + "\n...\n"
    return rendered


def render_hook_additional_context(activation: dict[str, Any], *, max_chars: int = 6000) -> str:
    has_items = any(_channel_items(activation, channel) for channel in CHANNEL_ORDER)
    if has_items:
        return render_injection_markdown(activation, max_chars=max_chars)

    selected_assets = activation.get("selected_assets") or []
    titles = [
        str(item.get("title") or item.get("asset_id"))
        for item in selected_assets[:3]
        if isinstance(item, dict) and (item.get("title") or item.get("asset_id"))
    ]
    if titles:
        return _compact("expcap 已为当前任务激活相关经验。优先参考：" + "；".join(titles) + "。", max_chars)
    return "expcap 已检查当前任务的历史经验，当前没有直接可用的高优先级资产。"


def materialize_injection_artifacts(*, workspace: Path, activation: dict[str, Any]) -> dict[str, str]:
    injection_dir = memory_root_for_workspace(workspace) / "injections"
    activation_id = str(activation.get("activation_id") or "activation")
    payload = injection_artifact_payload(activation)
    markdown = render_injection_markdown(activation)

    json_path = injection_dir / f"{activation_id}.json"
    markdown_path = injection_dir / f"{activation_id}.md"
    latest_json_path = injection_dir / "latest.json"
    latest_markdown_path = injection_dir / "latest.md"

    save_json(json_path, payload)
    save_json(latest_json_path, payload)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")
    latest_markdown_path.write_text(markdown, encoding="utf-8")

    return {
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "latest_json_path": str(latest_json_path),
        "latest_markdown_path": str(latest_markdown_path),
    }
