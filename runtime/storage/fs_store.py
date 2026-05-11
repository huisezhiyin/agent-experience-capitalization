from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterator

from runtime.backends import resolve_backend_config
from runtime.storage.embeddings import embedding_provider_config


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


def configured_memory_root_for_workspace(workspace: Path) -> Path:
    profile = str(resolve_backend_config().get("storage_profile", "local"))
    if profile == "local":
        return workspace / ".agent-memory"
    if profile == "user-cache":
        return expcap_home() / "projects" / project_storage_key(workspace)
    return expcap_home() / "cache" / project_storage_key(workspace)


def memory_root_for_workspace(workspace: Path) -> Path:
    return configured_memory_root_for_workspace(workspace)


def fallback_memory_root_for_workspace(workspace: Path) -> Path:
    profile = str(resolve_backend_config().get("storage_profile", "local"))
    if profile == "local":
        return workspace / ".agent-memory"
    return Path(tempfile.gettempdir()) / "expcap-runtime" / "projects" / project_storage_key(workspace)


def memory_roots_for_workspace(workspace: Path) -> tuple[Path, ...]:
    primary_root = configured_memory_root_for_workspace(workspace)
    fallback_root = fallback_memory_root_for_workspace(workspace)
    if fallback_root == primary_root or not fallback_root.exists():
        return (primary_root,)
    return (primary_root, fallback_root)


def milvus_runtime_root() -> Path:
    return codex_home() / "expcap-milvus"


def _milvus_runtime_storage_key(db_path: Path) -> str:
    return hashlib.sha1(str(db_path.parent.expanduser().resolve()).encode("utf-8")).hexdigest()[:10]


def milvus_runtime_directory(db_path: Path) -> Path:
    return milvus_runtime_root() / _milvus_runtime_storage_key(db_path)


def milvus_runtime_db_path(db_path: Path) -> Path:
    if len(str(db_path.parent)) <= 60:
        return db_path

    actual_dir = db_path.parent.expanduser().resolve()
    runtime_dir = milvus_runtime_directory(db_path)
    try:
        actual_dir.mkdir(parents=True, exist_ok=True)
        runtime_dir.parent.mkdir(parents=True, exist_ok=True)
        if runtime_dir.exists() or runtime_dir.is_symlink():
            try:
                if runtime_dir.resolve() == actual_dir:
                    return runtime_dir / db_path.name
            except OSError:
                pass
            if runtime_dir.is_symlink():
                runtime_dir.unlink()
            else:
                return db_path
        os.symlink(actual_dir, runtime_dir, target_is_directory=True)
        return runtime_dir / db_path.name
    except OSError:
        return db_path


def storage_layout_for_workspace(workspace: Path) -> dict[str, Any]:
    config = resolve_backend_config()
    memory_root = memory_root_for_workspace(workspace)
    fallback_root = fallback_memory_root_for_workspace(workspace)
    shared_root = shared_memory_root()
    active_milvus_path = default_milvus_db_path(workspace)
    active_shared_milvus_path = shared_milvus_db_path()
    runtime_milvus_path = milvus_runtime_db_path(active_milvus_path)
    runtime_shared_milvus_path = milvus_runtime_db_path(active_shared_milvus_path)
    legacy_milvus_path = memory_root / "milvus.db"
    legacy_shared_milvus_path = shared_root / "milvus.db"
    return {
        "storage_profile": config["storage_profile"],
        "data_source_mode": config["data_source_mode"],
        "project_owned_assets": config["project_owned_assets"],
        "local_runtime_data_in_project": config["local_runtime_data_in_project"],
        "project_storage_key": project_storage_key(workspace),
        "memory_root": str(memory_root),
        "fallback_memory_root": str(fallback_root),
        "asset_root": str(memory_root / "assets"),
        "state_index_path": str(memory_root / "index.sqlite3"),
        "fallback_state_index_path": str(fallback_root / "index.sqlite3"),
        "retrieval_index_path": str(active_milvus_path),
        "retrieval_runtime_path": str(runtime_milvus_path),
        "retrieval_runtime_aliased": runtime_milvus_path != active_milvus_path,
        "retrieval_index_profile": embedding_provider_config()["profile"],
        "legacy_retrieval_index_path": str(legacy_milvus_path),
        "legacy_retrieval_index_exists": legacy_milvus_path.exists(),
        "shared_memory_root": str(shared_root),
        "shared_asset_root": str(shared_root / "assets"),
        "shared_state_index_path": str(shared_root / "index.sqlite3"),
        "shared_retrieval_index_path": str(active_shared_milvus_path),
        "shared_retrieval_runtime_path": str(runtime_shared_milvus_path),
        "shared_retrieval_runtime_aliased": runtime_shared_milvus_path != active_shared_milvus_path,
        "shared_legacy_retrieval_index_path": str(legacy_shared_milvus_path),
        "shared_legacy_retrieval_index_exists": legacy_shared_milvus_path.exists(),
        "remote_uris": config["backend_uris"],
    }


def default_db_path(workspace: Path) -> Path:
    return memory_root_for_workspace(workspace) / "index.sqlite3"


def fallback_db_path(workspace: Path) -> Path:
    return fallback_memory_root_for_workspace(workspace) / "index.sqlite3"


def shared_db_path() -> Path:
    return shared_memory_root() / "index.sqlite3"


def _milvus_db_filename() -> str:
    profile = str(embedding_provider_config()["profile"])
    filename = f"milvus.{profile}.db"
    if len(filename) < 36:
        return filename
    digest = hashlib.sha1(profile.encode("utf-8")).hexdigest()[:8]
    compact_profile = profile[:16].rstrip("-")
    return f"milvus.{compact_profile}-{digest}.db"


def default_milvus_db_path(workspace: Path) -> Path:
    return memory_root_for_workspace(workspace) / _milvus_db_filename()


def shared_milvus_db_path() -> Path:
    return shared_memory_root() / _milvus_db_filename()


def legacy_milvus_db_path(workspace: Path) -> Path:
    return memory_root_for_workspace(workspace) / "milvus.db"


def legacy_shared_milvus_db_path() -> Path:
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
