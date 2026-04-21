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

    local_first = source_of_truth == "local-json" and state_index == "sqlite" and sharing == "local-shared"
    cloud_enabled = source_of_truth == "object-storage" or state_index == "cloud-sql" or retrieval == "milvus" or sharing == "cloud-shared"

    return {
        "profile": "local-first" if local_first else "hybrid" if cloud_enabled else "custom",
        "source_of_truth": source_of_truth,
        "state_index": state_index,
        "retrieval": retrieval,
        "sharing": sharing,
        "cloud_enabled": cloud_enabled,
        "env_overrides": {
            "EXPCAP_SOURCE_OF_TRUTH_BACKEND": env_map.get("EXPCAP_SOURCE_OF_TRUTH_BACKEND"),
            "EXPCAP_STATE_INDEX_BACKEND": env_map.get("EXPCAP_STATE_INDEX_BACKEND"),
            "EXPCAP_RETRIEVAL_BACKEND": env_map.get("EXPCAP_RETRIEVAL_BACKEND"),
            "EXPCAP_SHARING_BACKEND": env_map.get("EXPCAP_SHARING_BACKEND"),
        },
    }
