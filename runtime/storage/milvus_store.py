from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any

from runtime.storage.fs_store import iter_json_objects


COLLECTION_NAME = "experience_assets"
EMBEDDING_DIM = 128


def milvus_available() -> bool:
    try:
        from pymilvus import MilvusClient  # noqa: F401
    except Exception:
        return False
    return True


def milvus_backend_summary(db_path: Path) -> dict[str, Any]:
    summary = {
        "backend": "milvus-lite",
        "available": milvus_available(),
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "collection_name": COLLECTION_NAME,
        "collection_exists": False,
        "indexed_entities": None,
    }
    client = _safe_client(db_path)
    if client is None:
        return summary

    try:
        summary["collection_exists"] = bool(client.has_collection(collection_name=COLLECTION_NAME))
    except Exception:
        return summary

    if not summary["collection_exists"]:
        return summary

    try:
        stats = client.get_collection_stats(collection_name=COLLECTION_NAME)
    except Exception:
        stats = None
    if isinstance(stats, dict):
        for key in ("row_count", "rows", "num_rows", "insert_count"):
            value = stats.get(key)
            if value is None:
                continue
            try:
                summary["indexed_entities"] = int(value)
                break
            except Exception:
                continue

    if summary["indexed_entities"] is None:
        try:
            rows = client.query(
                collection_name=COLLECTION_NAME,
                filter='asset_id != ""',
                output_fields=["asset_id"],
                limit=16384,
            )
            if isinstance(rows, list):
                summary["indexed_entities"] = len(rows)
        except Exception:
            pass

    return summary


def _client(db_path: Path) -> Any:
    from pymilvus import MilvusClient

    db_path.parent.mkdir(parents=True, exist_ok=True)
    return MilvusClient(str(db_path))


def _safe_client(db_path: Path) -> Any | None:
    if not milvus_available():
        return None
    try:
        return _client(db_path)
    except Exception:
        return None


def _ensure_collection(client: Any) -> None:
    if client.has_collection(collection_name=COLLECTION_NAME):
        return
    client.create_collection(
        collection_name=COLLECTION_NAME,
        dimension=EMBEDDING_DIM,
        primary_field_name="asset_id",
        id_type="string",
        vector_field_name="vector",
        metric_type="COSINE",
        auto_id=False,
        max_length=512,
        enable_dynamic_field=True,
    )


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower())
    return [token for token in tokens if token.strip()]


def embed_text(text: str, *, dim: int = EMBEDDING_DIM) -> list[float]:
    vector = [0.0] * dim
    tokens = _tokenize(text)
    if not tokens:
        return vector
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:2], "big") % dim
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        weight = 1.0 + (len(token) / 24.0)
        vector[bucket] += sign * weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [round(value / norm, 8) for value in vector]


def asset_embedding_text(asset: dict[str, Any]) -> str:
    fragments = [
        asset.get("title", ""),
        asset.get("content", ""),
        asset.get("asset_type", ""),
        asset.get("knowledge_kind", ""),
        asset.get("knowledge_scope", ""),
    ]
    scope = asset.get("scope", {})
    fragments.append(scope.get("value", ""))
    fragments.append(scope.get("level", ""))
    return " ".join(fragment for fragment in fragments if fragment)


def prepare_asset_document(asset: dict[str, Any]) -> dict[str, Any]:
    scope = asset.get("scope", {})
    return {
        "asset_id": asset["asset_id"],
        "vector": embed_text(asset_embedding_text(asset)),
        "workspace": asset.get("workspace"),
        "source_workspace": asset.get("source_workspace") or asset.get("workspace"),
        "knowledge_scope": asset.get("knowledge_scope", "project"),
        "knowledge_kind": asset.get("knowledge_kind", asset.get("asset_type", "pattern")),
        "asset_type": asset.get("asset_type", "pattern"),
        "scope_level": scope.get("level"),
        "scope_value": scope.get("value"),
        "title": asset.get("title", ""),
        "content": asset.get("content", ""),
        "confidence": float(asset.get("confidence", 0.0)),
        "updated_at": asset.get("updated_at"),
        "created_at": asset.get("created_at"),
    }


def upsert_asset_vector(db_path: Path, asset: dict[str, Any]) -> bool:
    client = _safe_client(db_path)
    if client is None:
        return False
    try:
        _ensure_collection(client)
        client.upsert(
            collection_name=COLLECTION_NAME,
            data=[prepare_asset_document(asset)],
        )
        return True
    except Exception:
        return False


def sync_assets_directory(db_path: Path, assets_dir: Path) -> int:
    client = _safe_client(db_path)
    if client is None or not assets_dir.exists():
        return 0
    try:
        _ensure_collection(client)
    except Exception:
        return 0
    count = 0
    for asset in iter_json_objects(assets_dir):
        if "asset_id" not in asset:
            continue
        if upsert_asset_vector(db_path, asset):
            count += 1
    return count


def _build_filter(
    *,
    knowledge_scope: str | None = None,
    workspace: str | None = None,
) -> str:
    clauses: list[str] = []
    if knowledge_scope:
        clauses.append(f'knowledge_scope == "{knowledge_scope}"')
    if workspace:
        clauses.append(f'workspace == "{workspace}"')
    return " and ".join(clauses)


def search_asset_vectors(
    db_path: Path,
    *,
    query_text: str,
    limit: int,
    knowledge_scope: str | None = None,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []

    client = _safe_client(db_path)
    if client is None:
        return []
    if not client.has_collection(collection_name=COLLECTION_NAME):
        return []

    output_fields = [
        "asset_id",
        "workspace",
        "source_workspace",
        "knowledge_scope",
        "knowledge_kind",
        "asset_type",
        "scope_level",
        "scope_value",
        "title",
        "content",
        "confidence",
        "updated_at",
        "created_at",
    ]
    filter_expr = _build_filter(knowledge_scope=knowledge_scope, workspace=workspace)
    try:
        result = client.search(
            collection_name=COLLECTION_NAME,
            data=[embed_text(query_text)],
            filter=filter_expr,
            limit=limit,
            output_fields=output_fields,
        )
    except Exception:
        return []
    return _normalize_search_results(result)


def _normalize_search_results(result: Any) -> list[dict[str, Any]]:
    if not result:
        return []

    rows = result[0] if isinstance(result, list) and result and isinstance(result[0], list) else result
    normalized: list[dict[str, Any]] = []
    for item in rows:
        if isinstance(item, dict):
            entity = item.get("entity") or item
            asset_id = entity.get("asset_id") or item.get("id")
            if not asset_id:
                continue
            normalized.append(
                {
                    "asset_id": asset_id,
                    "workspace": entity.get("workspace"),
                    "source_workspace": entity.get("source_workspace"),
                    "knowledge_scope": entity.get("knowledge_scope", "project"),
                    "knowledge_kind": entity.get("knowledge_kind", entity.get("asset_type", "pattern")),
                    "asset_type": entity.get("asset_type", "pattern"),
                    "scope": {
                        "level": entity.get("scope_level"),
                        "value": entity.get("scope_value"),
                    },
                    "title": entity.get("title", ""),
                    "content": entity.get("content", ""),
                    "confidence": float(entity.get("confidence", 0.0)),
                    "updated_at": entity.get("updated_at"),
                    "created_at": entity.get("created_at"),
                    "vector_score": float(item.get("distance", item.get("score", 0.0))),
                    "status": "active",
                }
            )
    return normalized
