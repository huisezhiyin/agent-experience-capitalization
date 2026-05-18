from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from typing import Iterator


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = _connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def _ensure_column(
    conn: sqlite3.Connection,
    *,
    table: str,
    column: str,
    definition: str,
) -> None:
    if column in _column_names(conn, table):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_db(db_path: Path) -> None:
    with _connection(db_path) as conn:
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
                knowledge_kind TEXT,
                knowledge_scope TEXT,
                owner TEXT,
                scope_level TEXT,
                scope_value TEXT,
                scope_task_type TEXT,
                scope_module TEXT,
                scope_language TEXT,
                scope_framework TEXT,
                status TEXT,
                review_status TEXT,
                temperature TEXT,
                quarantine_status TEXT,
                version TEXT,
                validity_start TEXT,
                validity_end TEXT,
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
                knowledge_kind TEXT,
                knowledge_scope TEXT,
                owner TEXT,
                scope_level TEXT,
                scope_value TEXT,
                scope_task_type TEXT,
                scope_module TEXT,
                scope_language TEXT,
                scope_framework TEXT,
                status TEXT,
                review_status TEXT,
                temperature TEXT,
                quarantine_status TEXT,
                version TEXT,
                validity_start TEXT,
                validity_end TEXT,
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
        _ensure_column(conn, table="candidates", column="knowledge_kind", definition="TEXT")
        _ensure_column(conn, table="candidates", column="knowledge_scope", definition="TEXT")
        _ensure_column(conn, table="candidates", column="owner", definition="TEXT")
        _ensure_column(conn, table="candidates", column="scope_task_type", definition="TEXT")
        _ensure_column(conn, table="candidates", column="scope_module", definition="TEXT")
        _ensure_column(conn, table="candidates", column="scope_language", definition="TEXT")
        _ensure_column(conn, table="candidates", column="scope_framework", definition="TEXT")
        _ensure_column(conn, table="candidates", column="review_status", definition="TEXT")
        _ensure_column(conn, table="candidates", column="temperature", definition="TEXT")
        _ensure_column(conn, table="candidates", column="quarantine_status", definition="TEXT")
        _ensure_column(conn, table="candidates", column="version", definition="TEXT")
        _ensure_column(conn, table="candidates", column="validity_start", definition="TEXT")
        _ensure_column(conn, table="candidates", column="validity_end", definition="TEXT")
        _ensure_column(conn, table="assets", column="knowledge_kind", definition="TEXT")
        _ensure_column(conn, table="assets", column="knowledge_scope", definition="TEXT")
        _ensure_column(conn, table="assets", column="owner", definition="TEXT")
        _ensure_column(conn, table="assets", column="scope_task_type", definition="TEXT")
        _ensure_column(conn, table="assets", column="scope_module", definition="TEXT")
        _ensure_column(conn, table="assets", column="scope_language", definition="TEXT")
        _ensure_column(conn, table="assets", column="scope_framework", definition="TEXT")
        _ensure_column(conn, table="assets", column="review_status", definition="TEXT")
        _ensure_column(conn, table="assets", column="temperature", definition="TEXT")
        _ensure_column(conn, table="assets", column="quarantine_status", definition="TEXT")
        _ensure_column(conn, table="assets", column="version", definition="TEXT")
        _ensure_column(conn, table="assets", column="validity_start", definition="TEXT")
        _ensure_column(conn, table="assets", column="validity_end", definition="TEXT")
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_candidates_governance
            ON candidates(workspace, knowledge_scope, review_status, temperature);

            CREATE INDEX IF NOT EXISTS idx_assets_governance
            ON assets(workspace, knowledge_scope, review_status, temperature);

            CREATE INDEX IF NOT EXISTS idx_candidates_scope_profile
            ON candidates(scope_task_type, scope_module, scope_language, scope_framework);

            CREATE INDEX IF NOT EXISTS idx_assets_scope_profile
            ON assets(scope_task_type, scope_module, scope_language, scope_framework);
            """
        )


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _governance_projection(payload: dict[str, Any], *, default_owner: str) -> dict[str, Any]:
    governance = payload.get("governance", {}) if isinstance(payload.get("governance"), dict) else {}
    effectiveness_summary = (
        payload.get("effectiveness_summary", {}) if isinstance(payload.get("effectiveness_summary"), dict) else {}
    )
    validity_window = governance.get("validity_window", {})
    if not isinstance(validity_window, dict):
        validity_window = {}
    return {
        "knowledge_kind": payload.get("knowledge_kind"),
        "knowledge_scope": payload.get("knowledge_scope") or governance.get("knowledge_scope") or "project",
        "owner": payload.get("owner") or governance.get("owner") or payload.get("delivery", {}).get("owner") or default_owner,
        "review_status": (
            payload.get("review_status") or governance.get("review_status") or effectiveness_summary.get("review_status") or "unproven"
        ),
        "temperature": (
            payload.get("temperature") or governance.get("temperature") or effectiveness_summary.get("temperature") or "neutral"
        ),
        "quarantine_status": payload.get("quarantine_status") or governance.get("quarantine_status") or "active",
        "version": str(payload.get("version") or governance.get("version") or "1"),
        "validity_start": payload.get("validity_start") or validity_window.get("starts_at") or payload.get("created_at"),
        "validity_end": payload.get("validity_end") or validity_window.get("ends_at"),
    }


def _scope_profile_projection(payload: dict[str, Any]) -> dict[str, Any]:
    scope_profile = payload.get("scope_profile", {}) if isinstance(payload.get("scope_profile"), dict) else {}
    return {
        "task_type": scope_profile.get("task_type"),
        "module": scope_profile.get("module"),
        "language": scope_profile.get("language"),
        "framework": scope_profile.get("framework"),
    }


def _effective_review_status(payload: dict[str, Any], feedback: dict[str, Any]) -> str:
    review_status = str(
        payload.get("review_status")
        or payload.get("governance", {}).get("review_status")
        or payload.get("effectiveness_summary", {}).get("review_status")
        or "unproven"
    )
    if review_status != "unproven":
        return review_status
    activation_count = int(feedback.get("activation_count", 0) or 0)
    support_ratio = float(feedback.get("support_ratio", 0.0) or 0.0)
    supported_strong_count = int(feedback.get("supported_strong_count", 0) or 0)
    if activation_count >= 4 and support_ratio < 0.2:
        return "needs_review"
    if activation_count >= 2 and (supported_strong_count >= 2 or support_ratio >= 0.75):
        return "healthy"
    if activation_count >= 1 and support_ratio < 0.35:
        return "watch"
    return review_status


def _validation_priority(payload: dict[str, Any], feedback: dict[str, Any]) -> tuple[float, list[str], str]:
    review_status = _effective_review_status(payload, feedback)
    quarantine_status = str(payload.get("quarantine_status") or payload.get("governance", {}).get("quarantine_status") or "active")
    conflicts_with = [str(item) for item in payload.get("conflicts_with", []) if item]
    activation_count = int(feedback.get("activation_count", 0) or 0)
    support_ratio = float(feedback.get("support_ratio", 0.0) or 0.0)
    confidence = float(payload.get("confidence", payload.get("confidence_score", 0.0)) or 0.0)
    scope_profile = payload.get("scope_profile", {}) if isinstance(payload.get("scope_profile"), dict) else {}

    priority = 0.0
    reasons: list[str] = []
    suggested_action = "watch"

    status_weight = {
        "unproven": 1.0,
        "watch": 0.78,
        "needs_review": 0.92,
        "healthy": 0.25,
    }
    priority += status_weight.get(review_status, 0.2)
    reasons.append(f"当前治理状态为 {review_status}")

    if activation_count == 0:
        priority += 0.38
        reasons.append("尚无历史激活，适合优先做首轮 replay/validation")
    else:
        priority += min(activation_count * 0.04, 0.2)
        reasons.append(f"已有 {activation_count} 次历史激活，可做基于真实使用的回放验证")

    if confidence >= 0.8:
        priority += 0.12
        reasons.append(f"资产置信度较高（{confidence:.2f}），值得优先验证是否应晋升稳定层")
    elif confidence < 0.65:
        priority -= 0.08
        reasons.append(f"资产置信度偏低（{confidence:.2f}），验证价值相对靠后")

    if support_ratio <= 0.2 and activation_count >= 2:
        priority += 0.08
        reasons.append("多次激活帮助偏弱，适合优先复核或隔离")
        suggested_action = "replay_or_quarantine"
    elif review_status in {"unproven", "watch"}:
        suggested_action = "replay"
    elif review_status == "needs_review":
        suggested_action = "review_or_quarantine"

    if quarantine_status != "active":
        priority -= 0.4
        reasons.append(f"资产当前已标记为 {quarantine_status}")
        suggested_action = "ignore"

    if conflicts_with:
        priority += 0.08
        reasons.append(f"存在 {len(conflicts_with)} 条显式冲突关系，适合优先裁决")

    if scope_profile.get("module") or scope_profile.get("task_type"):
        reasons.append(
            "作用域画像为 "
            + ", ".join(
                [
                    f"{key}={value}"
                    for key, value in (
                        ("task_type", scope_profile.get("task_type")),
                        ("module", scope_profile.get("module")),
                    )
                    if value
                ]
            )
        )

    return round(priority, 4), reasons, suggested_action


def upsert_trace(db_path: Path, trace: dict[str, Any]) -> None:
    ensure_db(db_path)
    timestamps = trace.get("timestamps", {})
    with _connection(db_path) as conn:
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
    with _connection(db_path) as conn:
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
    governance = _governance_projection(candidate, default_owner="project")
    scope_profile = _scope_profile_projection(candidate)
    with _connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO candidates (
                candidate_id, workspace, candidate_type, knowledge_kind, knowledge_scope, owner,
                scope_level, scope_value, scope_task_type, scope_module, scope_language, scope_framework,
                status, review_status, temperature, quarantine_status,
                version, validity_start, validity_end, confidence_score, reusability_score,
                stability_score, constraint_value_score, created_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                workspace = excluded.workspace,
                candidate_type = excluded.candidate_type,
                knowledge_kind = excluded.knowledge_kind,
                knowledge_scope = excluded.knowledge_scope,
                owner = excluded.owner,
                scope_level = excluded.scope_level,
                scope_value = excluded.scope_value,
                scope_task_type = excluded.scope_task_type,
                scope_module = excluded.scope_module,
                scope_language = excluded.scope_language,
                scope_framework = excluded.scope_framework,
                status = excluded.status,
                review_status = excluded.review_status,
                temperature = excluded.temperature,
                quarantine_status = excluded.quarantine_status,
                version = excluded.version,
                validity_start = excluded.validity_start,
                validity_end = excluded.validity_end,
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
                governance["knowledge_kind"],
                governance["knowledge_scope"],
                governance["owner"],
                scope.get("level"),
                scope.get("value"),
                scope_profile["task_type"],
                scope_profile["module"],
                scope_profile["language"],
                scope_profile["framework"],
                candidate.get("status"),
                governance["review_status"],
                governance["temperature"],
                governance["quarantine_status"],
                governance["version"],
                governance["validity_start"],
                governance["validity_end"],
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
    governance = _governance_projection(asset, default_owner="project")
    scope_profile = _scope_profile_projection(asset)
    with _connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO assets (
                asset_id, workspace, asset_type, knowledge_kind, knowledge_scope, owner,
                scope_level, scope_value, scope_task_type, scope_module, scope_language, scope_framework,
                status, review_status, temperature, quarantine_status,
                version, validity_start, validity_end, confidence, last_used_at, created_at,
                updated_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                workspace = excluded.workspace,
                asset_type = excluded.asset_type,
                knowledge_kind = excluded.knowledge_kind,
                knowledge_scope = excluded.knowledge_scope,
                owner = excluded.owner,
                scope_level = excluded.scope_level,
                scope_value = excluded.scope_value,
                scope_task_type = excluded.scope_task_type,
                scope_module = excluded.scope_module,
                scope_language = excluded.scope_language,
                scope_framework = excluded.scope_framework,
                status = excluded.status,
                review_status = excluded.review_status,
                temperature = excluded.temperature,
                quarantine_status = excluded.quarantine_status,
                version = excluded.version,
                validity_start = excluded.validity_start,
                validity_end = excluded.validity_end,
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
                governance["knowledge_kind"],
                governance["knowledge_scope"],
                governance["owner"],
                scope.get("level"),
                scope.get("value"),
                scope_profile["task_type"],
                scope_profile["module"],
                scope_profile["language"],
                scope_profile["framework"],
                asset.get("status"),
                governance["review_status"],
                governance["temperature"],
                governance["quarantine_status"],
                governance["version"],
                governance["validity_start"],
                governance["validity_end"],
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
    with _connection(db_path) as conn:
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
    with _connection(db_path) as conn:
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
    with _connection(db_path) as conn:
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
    with _connection(db_path) as conn:
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
    with _connection(db_path) as conn:
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
    with _connection(db_path) as conn:
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
    with _connection(db_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["payload_json"])


def _update_asset_payload(
    db_path: Path,
    *,
    asset_id: str,
    mutate: Any,
) -> dict[str, Any] | None:
    if not db_path.exists():
        return None
    ensure_db(db_path)
    with _connection(db_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"])
    updated = mutate(payload)
    if updated is None:
        updated = payload
    upsert_asset(db_path, updated)
    return updated


def set_asset_quarantine_status(
    db_path: Path,
    *,
    asset_id: str,
    quarantine_status: str,
    reason: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any] | None:
    normalized = quarantine_status.strip().lower() or "active"

    def _mutate(payload: dict[str, Any]) -> dict[str, Any]:
        governance = payload.get("governance", {}) if isinstance(payload.get("governance"), dict) else {}
        governance["quarantine_status"] = normalized
        if normalized != "active":
            governance["review_status"] = "needs_review"
            payload["review_status"] = "needs_review"
        payload["governance"] = governance
        payload["quarantine_status"] = normalized
        if reason:
            history = payload.get("governance_history", [])
            if not isinstance(history, list):
                history = []
            history.append(
                {
                    "action": "set_quarantine_status",
                    "quarantine_status": normalized,
                    "reason": reason,
                    "updated_at": updated_at,
                }
            )
            payload["governance_history"] = history
        if updated_at:
            payload["updated_at"] = updated_at
        return payload

    return _update_asset_payload(db_path, asset_id=asset_id, mutate=_mutate)


def deprecate_asset(
    db_path: Path,
    *,
    asset_id: str,
    reason: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any] | None:
    def _mutate(payload: dict[str, Any]) -> dict[str, Any]:
        governance = payload.get("governance", {}) if isinstance(payload.get("governance"), dict) else {}
        governance["quarantine_status"] = "deprecated"
        governance["review_status"] = "needs_review"
        governance["deprecated_at"] = updated_at
        if reason:
            governance["deprecation_reason"] = reason
        payload["governance"] = governance
        payload["status"] = "deprecated"
        payload["review_status"] = "needs_review"
        payload["temperature"] = "cool"
        payload["quarantine_status"] = "deprecated"
        effectiveness_summary = (
            payload.get("effectiveness_summary", {})
            if isinstance(payload.get("effectiveness_summary"), dict)
            else {}
        )
        effectiveness_summary["review_status"] = "needs_review"
        effectiveness_summary["temperature"] = "cool"
        payload["effectiveness_summary"] = effectiveness_summary
        history = payload.get("governance_history", [])
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "action": "deprecate_asset",
                "reason": reason,
                "updated_at": updated_at,
            }
        )
        payload["governance_history"] = history
        if updated_at:
            payload["updated_at"] = updated_at
        return payload

    return _update_asset_payload(db_path, asset_id=asset_id, mutate=_mutate)


def reactivate_asset(
    db_path: Path,
    *,
    asset_id: str,
    reason: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any] | None:
    def _mutate(payload: dict[str, Any]) -> dict[str, Any]:
        governance = payload.get("governance", {}) if isinstance(payload.get("governance"), dict) else {}
        governance["quarantine_status"] = "active"
        governance["review_status"] = "watch"
        payload["governance"] = governance
        payload["status"] = "active"
        payload["review_status"] = "watch"
        payload["temperature"] = "cool"
        payload["quarantine_status"] = "active"
        effectiveness_summary = (
            payload.get("effectiveness_summary", {})
            if isinstance(payload.get("effectiveness_summary"), dict)
            else {}
        )
        effectiveness_summary["review_status"] = "watch"
        effectiveness_summary["temperature"] = "cool"
        payload["effectiveness_summary"] = effectiveness_summary
        history = payload.get("governance_history", [])
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "action": "reactivate_asset",
                "reason": reason,
                "updated_at": updated_at,
            }
        )
        payload["governance_history"] = history
        if updated_at:
            payload["updated_at"] = updated_at
        return payload

    return _update_asset_payload(db_path, asset_id=asset_id, mutate=_mutate)


def mark_asset_conflict(
    db_path: Path,
    *,
    asset_id: str,
    conflicting_asset_id: str,
    updated_at: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    def _mutate(payload: dict[str, Any], other_id: str) -> dict[str, Any]:
        conflicts = [str(item) for item in payload.get("conflicts_with", []) if item]
        if other_id not in conflicts:
            conflicts.append(other_id)
        payload["conflicts_with"] = conflicts
        history = payload.get("governance_history", [])
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "action": "mark_conflict",
                "conflicting_asset_id": other_id,
                "updated_at": updated_at,
            }
        )
        payload["governance_history"] = history
        if updated_at:
            payload["updated_at"] = updated_at
        return payload

    left = _update_asset_payload(
        db_path,
        asset_id=asset_id,
        mutate=lambda payload: _mutate(payload, conflicting_asset_id),
    )
    right = _update_asset_payload(
        db_path,
        asset_id=conflicting_asset_id,
        mutate=lambda payload: _mutate(payload, asset_id),
    )
    return left, right


def resolve_asset_conflict(
    db_path: Path,
    *,
    asset_id: str,
    conflicting_asset_id: str,
    reason: str | None = None,
    updated_at: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    def _mutate(payload: dict[str, Any], other_id: str) -> dict[str, Any]:
        conflicts = [str(item) for item in payload.get("conflicts_with", []) if item and str(item) != other_id]
        payload["conflicts_with"] = conflicts
        history = payload.get("governance_history", [])
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "action": "resolve_conflict",
                "conflicting_asset_id": other_id,
                "reason": reason,
                "updated_at": updated_at,
            }
        )
        payload["governance_history"] = history
        if updated_at:
            payload["updated_at"] = updated_at
        return payload

    left = _update_asset_payload(
        db_path,
        asset_id=asset_id,
        mutate=lambda payload: _mutate(payload, conflicting_asset_id),
    )
    right = _update_asset_payload(
        db_path,
        asset_id=conflicting_asset_id,
        mutate=lambda payload: _mutate(payload, asset_id),
    )
    return left, right


def build_asset_validation_queue(
    db_path: Path,
    *,
    workspace: str | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    assets = list_assets(db_path, workspace=workspace)
    asset_ids = [asset.get("asset_id") for asset in assets if asset.get("asset_id")]
    feedback_stats = summarize_asset_feedback(db_path, asset_ids=asset_ids)
    queue_items: list[dict[str, Any]] = []

    for asset in assets:
        asset_id = str(asset.get("asset_id") or "")
        if not asset_id:
            continue
        feedback = feedback_stats.get(asset_id, {})
        priority, reasons, suggested_action = _validation_priority(asset, feedback)
        item = {
            "asset_id": asset_id,
            "title": asset.get("title"),
            "knowledge_scope": asset.get("knowledge_scope", "project"),
            "knowledge_kind": asset.get("knowledge_kind", asset.get("asset_type", "pattern")),
            "review_status": _effective_review_status(asset, feedback),
            "temperature": str(
                asset.get("temperature")
                or asset.get("governance", {}).get("temperature")
                or asset.get("effectiveness_summary", {}).get("temperature")
                or "neutral"
            ),
            "quarantine_status": str(asset.get("quarantine_status") or asset.get("governance", {}).get("quarantine_status") or "active"),
            "scope": asset.get("scope"),
            "scope_profile": asset.get("scope_profile"),
            "conflicts_with": asset.get("conflicts_with", []),
            "historical_help": feedback,
            "validation_priority": priority,
            "suggested_action": suggested_action,
            "reasons": reasons,
        }
        queue_items.append(item)

    queue_items.sort(
        key=lambda item: (
            float(item["validation_priority"]),
            int(item["historical_help"].get("activation_count", 0) or 0),
        ),
        reverse=True,
    )
    if limit is not None:
        queue_items = queue_items[:limit]

    return {
        "items": queue_items,
        "total_assets": len(assets),
        "pending_validation_count": sum(
            1 for item in queue_items if item["suggested_action"] in {"replay", "replay_or_quarantine", "review_or_quarantine"}
        ),
    }


def build_governance_summary(
    db_path: Path,
    *,
    workspace: str | None = None,
    validation_limit: int | None = 10,
) -> dict[str, Any]:
    assets = list_assets(db_path, workspace=workspace)
    validation_queue = build_asset_validation_queue(
        db_path,
        workspace=workspace,
        limit=validation_limit,
    )
    summary = {
        "asset_count": len(assets),
        "review_status_counts": {},
        "temperature_counts": {},
        "quarantine_status_counts": {},
        "deprecated_asset_count": 0,
        "conflict_asset_count": 0,
        "pending_validation_count": validation_queue["pending_validation_count"],
        "top_validation_items": validation_queue["items"][: min(5, len(validation_queue["items"]))],
    }
    seen_conflict_assets: set[str] = set()
    for asset in assets:
        review_status = str(
            asset.get("review_status")
            or asset.get("governance", {}).get("review_status")
            or asset.get("effectiveness_summary", {}).get("review_status")
            or "unproven"
        )
        temperature = str(
            asset.get("temperature")
            or asset.get("governance", {}).get("temperature")
            or asset.get("effectiveness_summary", {}).get("temperature")
            or "neutral"
        )
        quarantine_status = str(asset.get("quarantine_status") or asset.get("governance", {}).get("quarantine_status") or "active")
        summary["review_status_counts"][review_status] = summary["review_status_counts"].get(review_status, 0) + 1
        summary["temperature_counts"][temperature] = summary["temperature_counts"].get(temperature, 0) + 1
        summary["quarantine_status_counts"][quarantine_status] = summary["quarantine_status_counts"].get(quarantine_status, 0) + 1
        if quarantine_status == "deprecated" or str(asset.get("status") or "").lower() == "deprecated":
            summary["deprecated_asset_count"] += 1
        conflicts_with = [str(item) for item in asset.get("conflicts_with", []) if item]
        if conflicts_with:
            seen_conflict_assets.add(str(asset.get("asset_id")))
    summary["conflict_asset_count"] = len(seen_conflict_assets)
    return summary


def touch_assets_last_used(db_path: Path, asset_ids: list[str], used_at: str) -> None:
    if not asset_ids:
        return
    ensure_db(db_path)
    placeholders = ", ".join("?" for _ in asset_ids)
    with _connection(db_path) as conn:
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
    query = "SELECT payload_json FROM assets WHERE status IN ('active', 'deprecated')"
    params: list[Any] = []
    if workspace:
        query += " AND (workspace = ? OR workspace IS NULL)"
        params.append(workspace)
    query += " ORDER BY confidence DESC, updated_at DESC"
    with _connection(db_path) as conn:
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
    with _connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]
