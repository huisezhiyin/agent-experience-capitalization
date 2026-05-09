from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_POLICY_FILENAME = ".expcap-project.json"
DEFAULT_PROJECT_STATUS = "active"
PROJECT_STATUSES = {"active", "inactive"}
DEFAULT_INTEGRATION_MODE = "docs-only"


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def project_policy_path(workspace: Path) -> Path:
    return workspace.resolve() / PROJECT_POLICY_FILENAME


def normalize_project_status(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in PROJECT_STATUSES:
        return normalized
    return DEFAULT_PROJECT_STATUS


def load_project_policy(workspace: Path) -> dict[str, Any]:
    path = project_policy_path(workspace)
    policy = {
        "project_status": DEFAULT_PROJECT_STATUS,
        "integration_mode": DEFAULT_INTEGRATION_MODE,
        "auto_start_enabled": True,
        "auto_start_mode": "always_on_new_chat",
        "policy_source": "default",
        "policy_path": str(path),
    }
    if not path.exists():
        return policy

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}

    project_status = normalize_project_status(payload.get("project_status"))
    policy.update(
        {
            "project_status": project_status,
            "integration_mode": str(payload.get("integration_mode") or DEFAULT_INTEGRATION_MODE),
            "auto_start_enabled": True,
            "auto_start_mode": "always_on_new_chat",
            "updated_at": payload.get("updated_at"),
            "policy_source": "file",
        }
    )
    return policy


def write_project_policy(
    workspace: Path,
    *,
    project_status: str = DEFAULT_PROJECT_STATUS,
    integration_mode: str = DEFAULT_INTEGRATION_MODE,
) -> Path:
    workspace = workspace.resolve()
    path = project_policy_path(workspace)
    stable_payload = {
        "project_status": normalize_project_status(project_status),
        "integration_mode": integration_mode or DEFAULT_INTEGRATION_MODE,
        "auto_start_mode": "always_on_new_chat",
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        existing_stable = {key: existing.get(key) for key in stable_payload}
        if existing_stable == stable_payload:
            return path
    payload = {
        **stable_payload,
        "updated_at": _now_utc(),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
