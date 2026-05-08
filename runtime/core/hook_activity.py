from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

from runtime.storage.fs_store import memory_root_for_workspace, save_json


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_hook_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _safe_slug(value: str) -> str:
    cleaned = []
    for ch in value.lower():
        if ch.isalnum():
            cleaned.append(ch)
        elif cleaned and cleaned[-1] != "-":
            cleaned.append("-")
    return "".join(cleaned).strip("-") or "hook-event"


def hook_events_dir(workspace: Path) -> Path:
    return memory_root_for_workspace(workspace) / "hooks" / "events"


def hook_latest_path(workspace: Path) -> Path:
    return memory_root_for_workspace(workspace) / "hooks" / "latest.json"


def record_hook_event(workspace: Path, payload: dict[str, Any]) -> Path:
    workspace = workspace.resolve()
    created_at = str(payload.get("created_at") or _now_utc())
    event_name = str(payload.get("event") or "hook-event")
    timestamp = created_at.replace(":", "").replace("-", "").replace("+00:00", "z")
    filename = f"{timestamp}_{_safe_slug(event_name)}.json"
    event_payload = {**payload, "workspace": str(workspace), "created_at": created_at}
    event_path = hook_events_dir(workspace) / filename
    save_json(event_path, event_payload)
    save_json(hook_latest_path(workspace), event_payload)
    return event_path


def load_recent_hook_events(workspace: Path, *, limit: int = 10) -> list[dict[str, Any]]:
    directory = hook_events_dir(workspace)
    if not directory.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            items.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
        if len(items) >= max(limit, 1):
            break
    return items
