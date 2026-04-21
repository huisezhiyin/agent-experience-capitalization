from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_db(db_path: Path) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS traces (
                trace_id TEXT PRIMARY KEY,
                workspace TEXT,
                host TEXT,
                task_hint TEXT,
                result_status TEXT,
                created_at TEXT,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS episodes (
                episode_id TEXT PRIMARY KEY,
                workspace TEXT,
                trace_id TEXT,
                scope_hint TEXT,
                result_status TEXT,
                confidence_hint REAL,
                created_at TEXT,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS candidates (
                candidate_id TEXT PRIMARY KEY,
                workspace TEXT,
                candidate_type TEXT,
                scope_level TEXT,
                scope_value TEXT,
                status TEXT,
                confidence_score REAL,
                reusability_score REAL,
                stability_score REAL,
                constraint_value_score REAL,
                created_at TEXT,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS assets (
                asset_id TEXT PRIMARY KEY,
                workspace TEXT,
                asset_type TEXT,
                scope_level TEXT,
                scope_value TEXT,
                status TEXT,
                confidence REAL,
                last_used_at TEXT,
                created_at TEXT,
                updated_at TEXT,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS activation_logs (
                activation_id TEXT PRIMARY KEY,
                workspace TEXT,
                task_query TEXT,
                selected_asset_ids_json TEXT,
                created_at TEXT,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_candidates_workspace_status
            ON candidates(workspace, status, candidate_type);

            CREATE INDEX IF NOT EXISTS idx_assets_workspace_status
            ON assets(workspace, status, asset_type);

            CREATE INDEX IF NOT EXISTS idx_assets_scope
            ON assets(scope_level, scope_value);

            CREATE INDEX IF NOT EXISTS idx_traces_workspace_result
            ON traces(workspace, result_status);
            """
        )


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def upsert_trace(db_path: Path, trace: dict[str, Any]) -> None:
    ensure_db(db_path)
    timestamps = trace.get("timestamps", {})
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO traces (
                trace_id, workspace, host, task_hint, result_status, created_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trace_id) DO UPDATE SET
                workspace = excluded.workspace,
                host = excluded.host,
                task_hint = excluded.task_hint,
                result_status = excluded.result_status,
                created_at = excluded.created_at,
                payload_json = excluded.payload_json
            """,
            (
                trace["trace_id"],
                trace.get("workspace"),
                trace.get("host"),
                trace.get("task_hint"),
                trace.get("result", {}).get("status"),
                timestamps.get("ended_at") or timestamps.get("started_at"),
                _dump(trace),
            ),
        )


def upsert_episode(db_path: Path, episode: dict[str, Any]) -> None:
    ensure_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO episodes (
                episode_id, workspace, trace_id, scope_hint, result_status,
                confidence_hint, created_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(episode_id) DO UPDATE SET
                workspace = excluded.workspace,
                trace_id = excluded.trace_id,
                scope_hint = excluded.scope_hint,
                result_status = excluded.result_status,
                confidence_hint = excluded.confidence_hint,
                created_at = excluded.created_at,
                payload_json = excluded.payload_json
            """,
            (
                episode["episode_id"],
                episode.get("workspace"),
                episode.get("trace_id"),
                episode.get("scope_hint"),
                episode.get("result"),
                episode.get("confidence_hint"),
                episode.get("created_at"),
                _dump(episode),
            ),
        )


def upsert_candidate(db_path: Path, candidate: dict[str, Any]) -> None:
    ensure_db(db_path)
    scope = candidate.get("scope", {})
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO candidates (
                candidate_id, workspace, candidate_type, scope_level, scope_value,
                status, confidence_score, reusability_score, stability_score,
                constraint_value_score, created_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                workspace = excluded.workspace,
                candidate_type = excluded.candidate_type,
                scope_level = excluded.scope_level,
                scope_value = excluded.scope_value,
                status = excluded.status,
                confidence_score = excluded.confidence_score,
                reusability_score = excluded.reusability_score,
                stability_score = excluded.stability_score,
                constraint_value_score = excluded.constraint_value_score,
                created_at = excluded.created_at,
                payload_json = excluded.payload_json
            """,
            (
                candidate["candidate_id"],
                candidate.get("workspace"),
                candidate.get("candidate_type"),
                scope.get("level"),
                scope.get("value"),
                candidate.get("status"),
                candidate.get("confidence_score"),
                candidate.get("reusability_score"),
                candidate.get("stability_score"),
                candidate.get("constraint_value_score"),
                candidate.get("created_at"),
                _dump(candidate),
            ),
        )


def upsert_asset(db_path: Path, asset: dict[str, Any]) -> None:
    ensure_db(db_path)
    scope = asset.get("scope", {})
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO assets (
                asset_id, workspace, asset_type, scope_level, scope_value, status,
                confidence, last_used_at, created_at, updated_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                workspace = excluded.workspace,
                asset_type = excluded.asset_type,
                scope_level = excluded.scope_level,
                scope_value = excluded.scope_value,
                status = excluded.status,
                confidence = excluded.confidence,
                last_used_at = excluded.last_used_at,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                payload_json = excluded.payload_json
            """,
            (
                asset["asset_id"],
                asset.get("workspace"),
                asset.get("asset_type"),
                scope.get("level"),
                scope.get("value"),
                asset.get("status"),
                asset.get("confidence"),
                asset.get("last_used_at"),
                asset.get("created_at"),
                asset.get("updated_at"),
                _dump(asset),
            ),
        )


def log_activation(db_path: Path, activation_view: dict[str, Any]) -> None:
    ensure_db(db_path)
    selected_asset_ids = [item["asset_id"] for item in activation_view.get("selected_assets", [])]
    payload = dict(activation_view)
    payload["selected_asset_ids"] = selected_asset_ids
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO activation_logs (
                activation_id, workspace, task_query, selected_asset_ids_json, created_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(activation_id) DO UPDATE SET
                workspace = excluded.workspace,
                task_query = excluded.task_query,
                selected_asset_ids_json = excluded.selected_asset_ids_json,
                created_at = excluded.created_at,
                payload_json = excluded.payload_json
            """,
            (
                activation_view["activation_id"],
                activation_view.get("workspace"),
                activation_view.get("task_query"),
                json.dumps(selected_asset_ids, ensure_ascii=False),
                activation_view.get("created_at"),
                _dump(payload),
            ),
        )


def find_latest_activation(
    db_path: Path,
    *,
    workspace: str,
    unresolved_only: bool = False,
) -> dict[str, Any] | None:
    if not db_path.exists():
        return None
    ensure_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM activation_logs
            WHERE workspace = ?
            ORDER BY created_at DESC
            """,
            (workspace,),
        ).fetchall()
    for row in rows:
        payload = json.loads(row["payload_json"])
        if unresolved_only and payload.get("feedback", {}).get("help_signal"):
            continue
        return payload
    return None


def record_activation_feedback(
    db_path: Path,
    *,
    activation_id: str,
    feedback: dict[str, Any],
) -> dict[str, Any] | None:
    if not db_path.exists():
        return None
    ensure_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM activation_logs WHERE activation_id = ?",
            (activation_id,),
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        payload["feedback"] = feedback
        conn.execute(
            """
            UPDATE activation_logs
            SET payload_json = ?
            WHERE activation_id = ?
            """,
            (_dump(payload), activation_id),
        )
    return payload


def list_activation_logs(
    db_path: Path,
    *,
    workspace: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    ensure_db(db_path)
    query = "SELECT payload_json FROM activation_logs"
    params: list[Any] = []
    if workspace:
        query += " WHERE workspace = ?"
        params.append(workspace)
    query += " ORDER BY created_at DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    with _connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def summarize_asset_feedback(
    db_path: Path,
    *,
    asset_ids: list[str],
) -> dict[str, dict[str, Any]]:
    if not asset_ids or not db_path.exists():
        return {}
    ensure_db(db_path)
    stats = {
        asset_id: {
            "activation_count": 0,
            "supported_count": 0,
            "supported_strong_count": 0,
            "supported_weak_count": 0,
            "unclear_count": 0,
            "weighted_support_score": 0.0,
            "support_ratio": 0.0,
        }
        for asset_id in asset_ids
    }
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT payload_json FROM activation_logs ORDER BY created_at DESC").fetchall()
    for row in rows:
        payload = json.loads(row["payload_json"])
        selected_asset_ids = [item for item in payload.get("selected_asset_ids", []) if item in stats]
        if not selected_asset_ids:
            selected_asset_ids = [
                item
                for item in payload.get("selected_assets", [])
                if isinstance(item, dict) and item.get("asset_id") in stats
            ]
            selected_asset_ids = [item["asset_id"] for item in selected_asset_ids]
        if not selected_asset_ids:
            continue
        help_signal = payload.get("feedback", {}).get("help_signal")
        for asset_id in selected_asset_ids:
            item = stats[asset_id]
            item["activation_count"] += 1
            if help_signal == "supported_strong":
                item["supported_count"] += 1
                item["supported_strong_count"] += 1
                item["weighted_support_score"] += 1.0
            elif help_signal == "supported_weak":
                item["supported_count"] += 1
                item["supported_weak_count"] += 1
                item["weighted_support_score"] += 0.5
            else:
                item["unclear_count"] += 1
    for item in stats.values():
        activation_count = item["activation_count"]
        item["support_ratio"] = round(
            (item["weighted_support_score"] / activation_count) if activation_count else 0.0,
            2,
        )
        item["weighted_support_score"] = round(item["weighted_support_score"], 2)
    return stats


def get_asset(db_path: Path, *, asset_id: str) -> dict[str, Any] | None:
    if not db_path.exists():
        return None
    ensure_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["payload_json"])


def get_candidate(db_path: Path, *, candidate_id: str) -> dict[str, Any] | None:
    if not db_path.exists():
        return None
    ensure_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["payload_json"])


def touch_assets_last_used(db_path: Path, asset_ids: list[str], used_at: str) -> None:
    if not asset_ids:
        return
    ensure_db(db_path)
    placeholders = ", ".join("?" for _ in asset_ids)
    with _connect(db_path) as conn:
        conn.execute(
            f"""
            UPDATE assets
            SET last_used_at = ?, updated_at = ?
            WHERE asset_id IN ({placeholders})
            """,
            (used_at, used_at, *asset_ids),
        )


def list_assets(db_path: Path, *, workspace: str | None = None) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    ensure_db(db_path)
    query = "SELECT payload_json FROM assets WHERE status = 'active'"
    params: list[Any] = []
    if workspace:
        query += " AND (workspace = ? OR workspace IS NULL)"
        params.append(workspace)
    query += " ORDER BY confidence DESC, updated_at DESC"
    with _connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def list_candidates(
    db_path: Path,
    *,
    workspace: str | None = None,
    statuses: tuple[str, ...] = ("new", "needs_review", "approved", "rejected", "promoted"),
) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    ensure_db(db_path)
    placeholders = ", ".join("?" for _ in statuses)
    query = f"SELECT payload_json FROM candidates WHERE status IN ({placeholders})"
    params: list[Any] = list(statuses)
    if workspace:
        query += " AND (workspace = ? OR workspace IS NULL)"
        params.append(workspace)
    query += " ORDER BY confidence_score DESC, reusability_score DESC, created_at DESC"
    with _connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]
