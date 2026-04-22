from __future__ import annotations

import os
from typing import Mapping


SOURCE_OF_TRUTH_BACKENDS = {"local-json", "object-storage"}
STATE_INDEX_BACKENDS = {"sqlite", "cloud-sql"}
RETRIEVAL_BACKENDS = {"sqlite-only", "milvus-lite", "milvus"}
SHARING_BACKENDS = {"local-shared", "cloud-shared"}


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


def resolve_backend_config(env: Mapping[str, str] | None = None) -> dict[str, object]:
    env_map = env or os.environ
    source_of_truth = _normalize_choice(
        env_map.get("EXPCAP_SOURCE_OF_TRUTH_BACKEND"),
        allowed=SOURCE_OF_TRUTH_BACKENDS,
        default="local-json",
    )
    state_index = _normalize_choice(
        env_map.get("EXPCAP_STATE_INDEX_BACKEND"),
        allowed=STATE_INDEX_BACKENDS,
        default="sqlite",
    )
    retrieval = _normalize_choice(
        env_map.get("EXPCAP_RETRIEVAL_BACKEND"),
        allowed=RETRIEVAL_BACKENDS,
        default="milvus-lite",
    )
    sharing = _normalize_choice(
        env_map.get("EXPCAP_SHARING_BACKEND"),
        allowed=SHARING_BACKENDS,
        default="local-shared",
    )

    local_mode = source_of_truth == "local-json" and state_index == "sqlite" and sharing == "local-shared"
    cloud_enabled = source_of_truth == "object-storage" or state_index == "cloud-sql" or retrieval == "milvus" or sharing == "cloud-shared"
    shareable_enabled = sharing == "cloud-shared" or source_of_truth == "object-storage" or retrieval == "milvus"
    project_id = _optional_value(env_map.get("EXPCAP_PROJECT_ID"))
    owning_team = _optional_value(env_map.get("EXPCAP_OWNING_TEAM"))
    backend_uris = {
        "asset_store": _optional_value(env_map.get("EXPCAP_ASSET_STORE_URI")),
        "state_index": _optional_value(env_map.get("EXPCAP_STATE_INDEX_URI")),
        "retrieval_index": _optional_value(env_map.get("EXPCAP_RETRIEVAL_INDEX_URI")),
        "shared_asset_store": _optional_value(env_map.get("EXPCAP_SHARED_ASSET_STORE_URI")),
    }

    return {
        "profile": "shareable" if shareable_enabled else "local-mode" if local_mode else "custom",
        "source_of_truth": source_of_truth,
        "state_index": state_index,
        "retrieval": retrieval,
        "sharing": sharing,
        "cloud_enabled": cloud_enabled,
        "local_mode": local_mode,
        "shareable_enabled": shareable_enabled,
        "asset_portability": "team-shareable" if shareable_enabled else "local-deliverable",
        "project_identity": {
            "project_id": project_id,
            "owning_team": owning_team,
        },
        "backend_uris": backend_uris,
        "env_overrides": {
            "EXPCAP_SOURCE_OF_TRUTH_BACKEND": env_map.get("EXPCAP_SOURCE_OF_TRUTH_BACKEND"),
            "EXPCAP_STATE_INDEX_BACKEND": env_map.get("EXPCAP_STATE_INDEX_BACKEND"),
            "EXPCAP_RETRIEVAL_BACKEND": env_map.get("EXPCAP_RETRIEVAL_BACKEND"),
            "EXPCAP_SHARING_BACKEND": env_map.get("EXPCAP_SHARING_BACKEND"),
            "EXPCAP_PROJECT_ID": env_map.get("EXPCAP_PROJECT_ID"),
            "EXPCAP_OWNING_TEAM": env_map.get("EXPCAP_OWNING_TEAM"),
            "EXPCAP_ASSET_STORE_URI": env_map.get("EXPCAP_ASSET_STORE_URI"),
            "EXPCAP_STATE_INDEX_URI": env_map.get("EXPCAP_STATE_INDEX_URI"),
            "EXPCAP_RETRIEVAL_INDEX_URI": env_map.get("EXPCAP_RETRIEVAL_INDEX_URI"),
            "EXPCAP_SHARED_ASSET_STORE_URI": env_map.get("EXPCAP_SHARED_ASSET_STORE_URI"),
        },
    }
