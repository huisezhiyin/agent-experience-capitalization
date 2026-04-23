from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

from runtime.backends import resolve_backend_config


def _slugify_path_part(value: str) -> str:
    cleaned = []
    for ch in value.lower():
        if ch.isalnum():
            cleaned.append(ch)
        elif cleaned and cleaned[-1] != "-":
            cleaned.append("-")
    return "".join(cleaned).strip("-") or "project"


def expcap_home() -> Path:
    configured = os.environ.get("EXPCAP_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".expcap").resolve()


def project_storage_key(workspace: Path) -> str:
    project_id = os.environ.get("EXPCAP_PROJECT_ID")
    raw_value = project_id.strip() if project_id else str(workspace.expanduser().resolve())
    digest = hashlib.sha1(raw_value.encode("utf-8")).hexdigest()[:10]
    return f"{_slugify_path_part(raw_value)[:72]}-{digest}"


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".codex").resolve()


def shared_memory_root() -> Path:
    return codex_home() / "expcap-memory"


def memory_root_for_workspace(workspace: Path) -> Path:
    profile = str(resolve_backend_config().get("storage_profile", "local"))
    if profile == "local":
        return workspace / ".agent-memory"
    if profile == "user-cache":
        return expcap_home() / "projects" / project_storage_key(workspace)
    return expcap_home() / "cache" / project_storage_key(workspace)


def storage_layout_for_workspace(workspace: Path) -> dict[str, Any]:
    config = resolve_backend_config()
    memory_root = memory_root_for_workspace(workspace)
    shared_root = shared_memory_root()
    return {
        "storage_profile": config["storage_profile"],
        "data_source_mode": config["data_source_mode"],
        "project_owned_assets": config["project_owned_assets"],
        "local_runtime_data_in_project": config["local_runtime_data_in_project"],
        "project_storage_key": project_storage_key(workspace),
        "memory_root": str(memory_root),
        "asset_root": str(memory_root / "assets"),
        "state_index_path": str(memory_root / "index.sqlite3"),
        "retrieval_index_path": str(memory_root / "milvus.db"),
        "shared_memory_root": str(shared_root),
        "shared_asset_root": str(shared_root / "assets"),
        "shared_state_index_path": str(shared_root / "index.sqlite3"),
        "shared_retrieval_index_path": str(shared_root / "milvus.db"),
        "remote_uris": config["backend_uris"],
    }


def default_db_path(workspace: Path) -> Path:
    return memory_root_for_workspace(workspace) / "index.sqlite3"


def shared_db_path() -> Path:
    return shared_memory_root() / "index.sqlite3"


def default_milvus_db_path(workspace: Path) -> Path:
    return memory_root_for_workspace(workspace) / "milvus.db"


def shared_milvus_db_path() -> Path:
    return shared_memory_root() / "milvus.db"


def default_trace_bundle_path(workspace: Path, trace: dict[str, Any]) -> Path:
    return memory_root_for_workspace(workspace) / "traces" / "bundles" / f"{trace['trace_id']}.json"


def memory_root_from_path(path: Path) -> Path:
    for parent in [path, *path.parents]:
        if parent.name == ".agent-memory":
            return parent
    return Path(".agent-memory")


def workspace_from_payload(payload: dict[str, Any], fallback: Path) -> Path:
    workspace = payload.get("workspace")
    if workspace:
        return Path(workspace).resolve()
    return fallback.resolve()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def iter_json_objects(directory: Path) -> Iterator[dict[str, Any]]:
    if not directory.exists():
        return
    for path in sorted(directory.rglob("*.json")):
        yield load_json(path)


def default_episode_path(trace_path: Path, episode: dict[str, Any]) -> Path:
    memory_root = memory_root_from_path(trace_path)
    return memory_root / "episodes" / f"{episode['episode_id']}.json"


def default_candidate_path(episode_path: Path, candidate: dict[str, Any]) -> Path:
    memory_root = memory_root_from_path(episode_path)
    return memory_root / "candidates" / f"{candidate['candidate_id']}.json"


def default_asset_path(candidate_path: Path, asset: dict[str, Any]) -> Path:
    memory_root = memory_root_from_path(candidate_path)
    asset_type = asset["asset_type"]
    return memory_root / "assets" / f"{asset_type}s" / f"{asset['asset_id']}.json"


def default_shared_asset_path(asset: dict[str, Any]) -> Path:
    asset_type = asset["asset_type"]
    return shared_memory_root() / "assets" / f"{asset_type}s" / f"{asset['asset_id']}.json"


def default_activation_view_path(workspace: Path, view: dict[str, Any]) -> Path:
    return memory_root_for_workspace(workspace) / "views" / f"{view['activation_id']}.json"
