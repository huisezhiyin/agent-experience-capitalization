from __future__ import annotations

import os
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit


STORAGE_PROFILES = {"local", "user-cache", "shared", "hybrid"}
SOURCE_OF_TRUTH_BACKENDS = {"local-json", "object-storage"}
STATE_INDEX_BACKENDS = {"sqlite", "cloud-sql"}
RETRIEVAL_BACKENDS = {"sqlite-only", "milvus-lite", "milvus"}
SHARING_BACKENDS = {"local-shared", "cloud-shared"}
PROFILE_DEFAULTS = {
    "local": {
        "source_of_truth": "local-json",
        "state_index": "sqlite",
        "retrieval": "milvus-lite",
        "sharing": "local-shared",
    },
    "user-cache": {
        "source_of_truth": "local-json",
        "state_index": "sqlite",
        "retrieval": "milvus-lite",
        "sharing": "local-shared",
    },
    "shared": {
        "source_of_truth": "object-storage",
        "state_index": "cloud-sql",
        "retrieval": "milvus",
        "sharing": "cloud-shared",
    },
    "hybrid": {
        "source_of_truth": "object-storage",
        "state_index": "sqlite",
        "retrieval": "milvus",
        "sharing": "cloud-shared",
    },
}


def _normalize_choice(
    value: str | None,
    *,
    allowed: set[str],
    default: str,
) -> str:
    normalized = (value or "").strip().lower()
    if not normalized:
        return default
    if normalized in allowed:
        return normalized
    return default


def _optional_value(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def _safe_uri_value(value: str | None) -> str | None:
    uri = _optional_value(value)
    if not uri:
        return None
    try:
        parts = urlsplit(uri)
    except ValueError:
        return "<invalid-uri>"
    if not parts.scheme:
        return uri
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def resolve_backend_config(env: Mapping[str, str] | None = None) -> dict[str, object]:
    env_map = env or os.environ
    storage_profile = _normalize_choice(
        env_map.get("EXPCAP_STORAGE_PROFILE"),
        allowed=STORAGE_PROFILES,
        default="local",
    )
    profile_defaults = PROFILE_DEFAULTS[storage_profile]
    source_of_truth = _normalize_choice(
        env_map.get("EXPCAP_SOURCE_OF_TRUTH_BACKEND"),
        allowed=SOURCE_OF_TRUTH_BACKENDS,
        default=profile_defaults["source_of_truth"],
    )
    state_index = _normalize_choice(
        env_map.get("EXPCAP_STATE_INDEX_BACKEND"),
        allowed=STATE_INDEX_BACKENDS,
        default=profile_defaults["state_index"],
    )
    retrieval = _normalize_choice(
        env_map.get("EXPCAP_RETRIEVAL_BACKEND"),
        allowed=RETRIEVAL_BACKENDS,
        default=profile_defaults["retrieval"],
    )
    sharing = _normalize_choice(
        env_map.get("EXPCAP_SHARING_BACKEND"),
        allowed=SHARING_BACKENDS,
        default=profile_defaults["sharing"],
    )

    local_mode = (
        storage_profile == "local"
        and source_of_truth == "local-json"
        and state_index == "sqlite"
        and sharing == "local-shared"
    )
    cloud_enabled = (
        source_of_truth == "object-storage"
        or state_index == "cloud-sql"
        or retrieval == "milvus"
        or sharing == "cloud-shared"
    )
    shareable_enabled = (
        storage_profile in {"shared", "hybrid"}
        or sharing == "cloud-shared"
        or source_of_truth == "object-storage"
        or retrieval == "milvus"
    )
    effective_storage_profile = storage_profile
    if not _optional_value(env_map.get("EXPCAP_STORAGE_PROFILE")) and shareable_enabled:
        effective_storage_profile = "shared"
    project_id = _optional_value(env_map.get("EXPCAP_PROJECT_ID"))
    owning_team = _optional_value(env_map.get("EXPCAP_OWNING_TEAM"))
    backend_uris = {
        "asset_store": _safe_uri_value(env_map.get("EXPCAP_ASSET_STORE_URI")),
        "state_index": _safe_uri_value(env_map.get("EXPCAP_STATE_INDEX_URI")),
        "retrieval_index": _safe_uri_value(env_map.get("EXPCAP_RETRIEVAL_INDEX_URI")),
        "shared_asset_store": _safe_uri_value(env_map.get("EXPCAP_SHARED_ASSET_STORE_URI")),
    }

    return {
        "profile": effective_storage_profile,
        "storage_profile": effective_storage_profile,
        "source_of_truth": source_of_truth,
        "state_index": state_index,
        "retrieval": retrieval,
        "sharing": sharing,
        "state_index_role": "lightweight-state-index" if state_index == "sqlite" else "shared-state-index",
        "retrieval_role": (
            "core-semantic-retrieval"
            if retrieval in {"milvus-lite", "milvus"}
            else "lightweight-metadata-retrieval"
        ),
        "cloud_enabled": cloud_enabled,
        "local_mode": local_mode,
        "shareable_enabled": shareable_enabled,
        "asset_portability": (
            "team-shareable"
            if shareable_enabled
            else "user-cache"
            if effective_storage_profile == "user-cache"
            else "local-deliverable"
        ),
        "data_source_mode": {
            "local": "project-local",
            "user-cache": "user-cache",
            "shared": "shared-source",
            "hybrid": "shared-source-with-local-cache",
        }[effective_storage_profile],
        "project_owned_assets": True,
        "local_runtime_data_in_project": effective_storage_profile == "local",
        "project_identity": {
            "project_id": project_id,
            "owning_team": owning_team,
        },
        "backend_uris": backend_uris,
        "env_overrides": {
            "EXPCAP_STORAGE_PROFILE": env_map.get("EXPCAP_STORAGE_PROFILE"),
            "EXPCAP_HOME": env_map.get("EXPCAP_HOME"),
            "EXPCAP_SOURCE_OF_TRUTH_BACKEND": env_map.get("EXPCAP_SOURCE_OF_TRUTH_BACKEND"),
            "EXPCAP_STATE_INDEX_BACKEND": env_map.get("EXPCAP_STATE_INDEX_BACKEND"),
            "EXPCAP_RETRIEVAL_BACKEND": env_map.get("EXPCAP_RETRIEVAL_BACKEND"),
            "EXPCAP_SHARING_BACKEND": env_map.get("EXPCAP_SHARING_BACKEND"),
            "EXPCAP_PROJECT_ID": env_map.get("EXPCAP_PROJECT_ID"),
            "EXPCAP_OWNING_TEAM": env_map.get("EXPCAP_OWNING_TEAM"),
            "EXPCAP_ASSET_STORE_URI": _safe_uri_value(env_map.get("EXPCAP_ASSET_STORE_URI")),
            "EXPCAP_STATE_INDEX_URI": _safe_uri_value(env_map.get("EXPCAP_STATE_INDEX_URI")),
            "EXPCAP_RETRIEVAL_INDEX_URI": _safe_uri_value(env_map.get("EXPCAP_RETRIEVAL_INDEX_URI")),
            "EXPCAP_SHARED_ASSET_STORE_URI": _safe_uri_value(env_map.get("EXPCAP_SHARED_ASSET_STORE_URI")),
        },
    }
