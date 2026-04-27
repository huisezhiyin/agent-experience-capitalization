from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import os
import socket
import tempfile
import time
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from runtime.backends import resolve_backend_config
from runtime.storage.embeddings import (
    DEFAULT_HASH_EMBEDDING_DIM,
    asset_embedding_text,
    embed_text,
    embedding_metadata,
    embedding_provider_config,
)
from runtime.storage.fs_store import iter_json_objects

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback.
    fcntl = None


COLLECTION_NAME = "experience_assets"
EMBEDDING_DIM = DEFAULT_HASH_EMBEDDING_DIM

os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GLOG_minloglevel", "2")


@lru_cache(maxsize=1)
def milvus_available() -> bool:
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="pkg_resources is deprecated as an API.*",
                category=UserWarning,
            )
            from pymilvus import MilvusClient  # noqa: F401
    except Exception:
        return False
    return True


def _compact_error(error: Exception) -> str:
    return " ".join(str(error).split())[:240] or error.__class__.__name__


def _milvus_collection_name() -> str:
    return os.environ.get("EXPCAP_MILVUS_COLLECTION", COLLECTION_NAME).strip() or COLLECTION_NAME


def _remote_milvus_requested() -> bool:
    config = resolve_backend_config()
    return config.get("retrieval") == "milvus"


def _remote_milvus_uri() -> str | None:
    if not _remote_milvus_requested():
        return None
    uri = os.environ.get("EXPCAP_RETRIEVAL_INDEX_URI", "").strip()
    return uri or None


def _redact_uri(uri: str | None) -> str | None:
    if not uri:
        return None
    try:
        parts = urlsplit(uri)
    except ValueError:
        return uri
    if not parts.username and not parts.password:
        return uri
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _remote_project_id() -> str | None:
    config = resolve_backend_config()
    project_identity = config.get("project_identity", {})
    if not isinstance(project_identity, dict):
        return None
    project_id = str(project_identity.get("project_id") or "").strip()
    return project_id or None


def _indexed_workspace_value(workspace: str | None) -> str | None:
    if _remote_milvus_requested():
        return _remote_project_id() or workspace
    return workspace


def _escape_filter_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _remote_milvus_client_kwargs() -> dict[str, str]:
    kwargs: dict[str, str] = {}
    token = (
        os.environ.get("EXPCAP_RETRIEVAL_INDEX_TOKEN")
        or os.environ.get("EXPCAP_MILVUS_TOKEN")
        or ""
    ).strip()
    user = os.environ.get("EXPCAP_MILVUS_USER", "").strip()
    password = os.environ.get("EXPCAP_MILVUS_PASSWORD", "").strip()
    db_name = os.environ.get("EXPCAP_MILVUS_DB_NAME", "").strip()
    if token:
        kwargs["token"] = token
    elif user or password:
        kwargs["user"] = user
        kwargs["password"] = password
    if db_name:
        kwargs["db_name"] = db_name
    return kwargs


def _milvus_lock_wait_seconds() -> float:
    raw_value = os.environ.get("EXPCAP_MILVUS_LOCK_WAIT_SECONDS", "0.25")
    try:
        return max(float(raw_value), 0.0)
    except ValueError:
        return 0.25


@lru_cache(maxsize=1)
def milvus_runtime_available() -> bool:
    if not milvus_available():
        return False
    if not hasattr(socket, "AF_UNIX"):
        return True

    probe_path = Path(tempfile.gettempdir()) / f"expcap_milvus_probe_{os.getpid()}.sock"
    try:
        if probe_path.exists():
            probe_path.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.bind(str(probe_path))
        return True
    except Exception:
        return False
    finally:
        try:
            probe_path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass


def _try_acquire_lock(lock_file: Any) -> bool:
    assert fcntl is not None
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _write_lock_metadata(lock_file: Any) -> None:
    try:
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"pid={os.getpid()} acquired_at={time.time():.3f}\n")
        lock_file.flush()
    except Exception:
        pass


def _parse_lock_metadata(raw_value: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for part in raw_value.split():
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key == "pid":
            try:
                metadata[key] = int(value)
            except ValueError:
                metadata[key] = value
        elif key == "acquired_at":
            try:
                metadata[key] = float(value)
            except ValueError:
                metadata[key] = value
        else:
            metadata[key] = value
    return metadata


def _process_exists(pid: Any) -> bool | None:
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return None


def milvus_lock_summary(db_path: Path) -> dict[str, Any]:
    lock_path = db_path.with_name(f"{db_path.name}.lock")
    raw_value = ""
    if lock_path.exists():
        try:
            raw_value = lock_path.read_text(encoding="utf-8").strip()
        except OSError as error:
            raw_value = f"read_error={_compact_error(error)}"
    metadata = _parse_lock_metadata(raw_value)

    locked: bool | None = None
    lock_error = None
    if fcntl is not None:
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+", encoding="utf-8") as lock_file:
                if _try_acquire_lock(lock_file):
                    locked = False
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                else:
                    locked = True
        except OSError as error:
            lock_error = _compact_error(error)

    pid_exists = _process_exists(metadata.get("pid"))
    age_seconds = None
    if isinstance(metadata.get("acquired_at"), float):
        age_seconds = round(max(time.time() - metadata["acquired_at"], 0.0), 3)

    return {
        "lock_path": str(lock_path),
        "lock_exists": lock_path.exists(),
        "locked": locked,
        "lock_error": lock_error,
        "metadata_raw": raw_value,
        "metadata": metadata,
        "pid_exists": pid_exists,
        "age_seconds": age_seconds,
        "stale_hint": bool(lock_path.exists() and pid_exists is False),
    }


@contextmanager
def _milvus_db_lock(db_path: Path):
    if fcntl is None:
        yield None
        return

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        yield f"lock_unavailable: {_compact_error(error)}"
        return
    lock_path = db_path.with_name(f"{db_path.name}.lock")
    try:
        lock_file = lock_path.open("a+", encoding="utf-8")
    except OSError as error:
        yield f"lock_unavailable: {_compact_error(error)}"
        return
    with lock_file:
        deadline = time.monotonic() + _milvus_lock_wait_seconds()
        while not _try_acquire_lock(lock_file):
            if time.monotonic() >= deadline:
                yield "locked_by_another_process"
                return
            time.sleep(0.05)
        try:
            _write_lock_metadata(lock_file)
            yield None
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _milvus_connection_lock(db_path: Path):
    if _remote_milvus_requested():
        yield None
        return
    with _milvus_db_lock(db_path) as lock_error:
        yield lock_error


def milvus_backend_summary(db_path: Path, *, deep_check: bool = False) -> dict[str, Any]:
    remote_uri = _remote_milvus_uri()
    if _remote_milvus_requested():
        return _remote_milvus_backend_summary(remote_uri, deep_check=deep_check)

    available = milvus_available()
    runtime_available = milvus_runtime_available() if available else False
    summary = {
        "backend": "milvus-lite",
        "mode": "local",
        "embedding": embedding_provider_config(),
        "available": available,
        "runtime_available": runtime_available,
        "status": "ready"
        if available and runtime_available and db_path.exists()
        else "not_initialized"
        if available and runtime_available
        else "degraded"
        if available
        else "unavailable",
        "deep_check": deep_check,
        "degraded_reason": None if runtime_available or not available else "unix_socket_bind_unavailable",
        "last_error": None,
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "remote_uri": None,
        "remote_configured": False,
        "collection_name": _milvus_collection_name(),
        "collection_exists": None if not deep_check else False,
        "indexed_entities": None,
    }
    if not summary["available"]:
        return summary
    if not summary["runtime_available"]:
        return summary

    if not deep_check:
        return summary

    with _milvus_db_lock(db_path) as lock_error:
        if lock_error:
            summary["status"] = "degraded"
            summary["degraded_reason"] = lock_error
            return summary
        client = _safe_client_unlocked(db_path)
        if client is None:
            summary["status"] = "degraded"
            summary["degraded_reason"] = "client_unavailable"
            return summary
        return _populate_backend_summary(client, summary)


def _remote_milvus_backend_summary(remote_uri: str | None, *, deep_check: bool = False) -> dict[str, Any]:
    available = milvus_available()
    summary = {
        "backend": "milvus",
        "mode": "remote",
        "embedding": embedding_provider_config(),
        "available": available,
        "runtime_available": True,
        "status": "configured" if available and remote_uri else "not_configured" if available else "unavailable",
        "deep_check": deep_check,
        "degraded_reason": None if remote_uri else "missing_retrieval_index_uri",
        "last_error": None,
        "db_path": None,
        "db_exists": None,
        "remote_uri": _redact_uri(remote_uri),
        "remote_configured": bool(remote_uri),
        "collection_name": _milvus_collection_name(),
        "collection_exists": None if not deep_check else False,
        "indexed_entities": None,
    }
    if not summary["available"] or not remote_uri or not deep_check:
        return summary
    client = _safe_client_unlocked(Path("."))
    if client is None:
        summary["status"] = "degraded"
        summary["degraded_reason"] = "client_unavailable"
        return summary
    return _populate_backend_summary(client, summary)


def _populate_backend_summary(client: Any, summary: dict[str, Any]) -> dict[str, Any]:
    try:
        summary["collection_exists"] = bool(client.has_collection(collection_name=_milvus_collection_name()))
    except Exception as error:
        summary["status"] = "degraded"
        summary["degraded_reason"] = "collection_check_failed"
        summary["last_error"] = _compact_error(error)
        return summary

    if not summary["collection_exists"]:
        summary["status"] = "not_initialized"
        return summary

    summary["status"] = "ready"
    try:
        stats = client.get_collection_stats(collection_name=_milvus_collection_name())
    except Exception as error:
        summary["status"] = "degraded"
        summary["degraded_reason"] = "stats_failed"
        summary["last_error"] = _compact_error(error)
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
                collection_name=_milvus_collection_name(),
                filter='asset_id != ""',
                output_fields=["asset_id"],
                limit=16384,
            )
            if isinstance(rows, list):
                summary["indexed_entities"] = len(rows)
        except Exception as error:
            summary["status"] = "degraded"
            summary["degraded_reason"] = "count_query_failed"
            summary["last_error"] = _compact_error(error)

    return summary


def _client(db_path: Path) -> Any:
    from pymilvus import MilvusClient

    remote_uri = _remote_milvus_uri()
    if _remote_milvus_requested():
        if not remote_uri:
            raise ValueError("EXPCAP_RETRIEVAL_INDEX_URI is required when EXPCAP_RETRIEVAL_BACKEND=milvus")
        return MilvusClient(uri=remote_uri, **_remote_milvus_client_kwargs())

    db_path.parent.mkdir(parents=True, exist_ok=True)
    return MilvusClient(str(db_path))


def _safe_client_unlocked(db_path: Path) -> Any | None:
    if not milvus_available():
        return None
    if not _remote_milvus_requested() and not milvus_runtime_available():
        return None
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="pkg_resources is deprecated as an API.*",
            category=UserWarning,
        )
        try:
            return _client(db_path)
        except Exception:
            return None


def _upsert_asset_vector_unlocked(client: Any, asset: dict[str, Any]) -> bool:
    try:
        _ensure_collection(client)
        client.upsert(
            collection_name=_milvus_collection_name(),
            data=[prepare_asset_document(asset)],
        )
        return True
    except Exception:
        return False


def _ensure_collection(client: Any) -> None:
    collection_name = _milvus_collection_name()
    if client.has_collection(collection_name=collection_name):
        return
    embedding_dim = int(embedding_provider_config().get("dim") or EMBEDDING_DIM)
    client.create_collection(
        collection_name=collection_name,
        dimension=embedding_dim,
        primary_field_name="asset_id",
        id_type="string",
        vector_field_name="vector",
        metric_type="COSINE",
        auto_id=False,
        max_length=512,
        enable_dynamic_field=True,
    )


def prepare_asset_document(asset: dict[str, Any]) -> dict[str, Any]:
    scope = asset.get("scope", {})
    raw_workspace = asset.get("workspace")
    source_workspace = asset.get("source_workspace") or raw_workspace
    indexed_workspace = _indexed_workspace_value(str(raw_workspace) if raw_workspace else None)
    document = {
        "asset_id": asset["asset_id"],
        "vector": embed_text(asset_embedding_text(asset)),
        "workspace": indexed_workspace,
        "source_workspace": source_workspace,
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
    document.update(embedding_metadata())
    return document


def upsert_asset_vector(db_path: Path, asset: dict[str, Any]) -> bool:
    with _milvus_connection_lock(db_path) as lock_error:
        if lock_error:
            return False
        client = _safe_client_unlocked(db_path)
        if client is None:
            return False
        return _upsert_asset_vector_unlocked(client, asset)


def sync_assets_directory(db_path: Path, assets_dir: Path) -> int:
    return sync_assets_directory_with_report(db_path, assets_dir)["synced"]


def sync_assets_directory_with_report(db_path: Path, assets_dir: Path, *, prune: bool = False) -> dict[str, Any]:
    report = {"synced": 0, "pruned": 0}
    if not assets_dir.exists():
        return report
    with _milvus_connection_lock(db_path) as lock_error:
        if lock_error:
            return report
        client = _safe_client_unlocked(db_path)
        if client is None:
            return report
        try:
            _ensure_collection(client)
        except Exception:
            return report
        expected_asset_ids: set[str] = set()
        for asset in iter_json_objects(assets_dir):
            asset_id = asset.get("asset_id")
            if not asset_id:
                continue
            expected_asset_ids.add(str(asset_id))
            if _upsert_asset_vector_unlocked(client, asset):
                report["synced"] += 1
        if prune and _remote_milvus_requested():
            report["prune_skipped_reason"] = "remote_prune_disabled"
        elif prune:
            report["pruned"] = _prune_stale_asset_vectors_unlocked(client, expected_asset_ids)
        return report


def _prune_stale_asset_vectors_unlocked(client: Any, expected_asset_ids: set[str]) -> int:
    try:
        rows = client.query(
            collection_name=_milvus_collection_name(),
            filter='asset_id != ""',
            output_fields=["asset_id"],
            limit=16384,
        )
    except Exception:
        return 0
    if not isinstance(rows, list):
        return 0

    stale_asset_ids = [
        str(row.get("asset_id"))
        for row in rows
        if isinstance(row, dict) and row.get("asset_id") and str(row.get("asset_id")) not in expected_asset_ids
    ]
    if not stale_asset_ids:
        return 0
    try:
        result = client.delete(collection_name=_milvus_collection_name(), ids=stale_asset_ids)
    except Exception:
        return 0
    if isinstance(result, dict):
        for key in ("delete_count", "deleted_count", "delete_cnt"):
            value = result.get(key)
            if value is not None:
                try:
                    return int(value)
                except Exception:
                    break
    return len(stale_asset_ids)


def _build_filter(
    *,
    knowledge_scope: str | None = None,
    workspace: str | None = None,
) -> str:
    clauses: list[str] = []
    if knowledge_scope:
        clauses.append(f'knowledge_scope == "{_escape_filter_value(knowledge_scope)}"')
    if workspace:
        indexed_workspace = _indexed_workspace_value(workspace)
        if indexed_workspace:
            clauses.append(f'workspace == "{_escape_filter_value(indexed_workspace)}"')
    return " and ".join(clauses)


def search_asset_vectors(
    db_path: Path,
    *,
    query_text: str,
    limit: int,
    knowledge_scope: str | None = None,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    if not _remote_milvus_requested() and not db_path.exists():
        return []

    with _milvus_connection_lock(db_path) as lock_error:
        if lock_error:
            return []
        client = _safe_client_unlocked(db_path)
        if client is None:
            return []
        try:
            if not client.has_collection(collection_name=_milvus_collection_name()):
                return []
        except Exception:
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
            "embedding_provider",
            "embedding_requested_provider",
            "embedding_model",
            "embedding_dim",
            "embedding_version",
            "embedding_status",
        ]
        filter_expr = _build_filter(knowledge_scope=knowledge_scope, workspace=workspace)
        try:
            result = client.search(
                collection_name=_milvus_collection_name(),
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
                    "embedding": {
                        "provider": entity.get("embedding_provider"),
                        "requested_provider": entity.get("embedding_requested_provider"),
                        "model": entity.get("embedding_model"),
                        "dim": entity.get("embedding_dim"),
                        "version": entity.get("embedding_version"),
                        "status": entity.get("embedding_status"),
                    },
                    "vector_score": float(item.get("distance", item.get("score", 0.0))),
                    "status": "active",
                }
            )
    return normalized
