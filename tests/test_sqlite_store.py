import sqlite3
import tempfile
import unittest
from pathlib import Path

from runtime.storage.sqlite_store import ensure_db
from runtime.storage.sqlite_store import build_asset_validation_queue
from runtime.storage.sqlite_store import build_governance_summary
from runtime.storage.sqlite_store import deprecate_asset
from runtime.storage.sqlite_store import log_activation
from runtime.storage.sqlite_store import mark_asset_conflict
from runtime.storage.sqlite_store import resolve_asset_conflict
from runtime.storage.sqlite_store import set_asset_quarantine_status
from runtime.storage.sqlite_store import get_asset
from runtime.storage.sqlite_store import reactivate_asset
from runtime.storage.sqlite_store import upsert_asset
from runtime.storage.sqlite_store import upsert_candidate


class SqliteGovernanceLedgerTests(unittest.TestCase):
    def test_ensure_db_adds_governance_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"

            ensure_db(db_path)

            with sqlite3.connect(db_path) as conn:
                asset_columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(assets)").fetchall()
                }
                candidate_columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(candidates)").fetchall()
                }

        self.assertTrue(
            {
                "knowledge_scope",
                "owner",
                "scope_task_type",
                "scope_module",
                "scope_language",
                "scope_framework",
                "review_status",
                "temperature",
                "quarantine_status",
                "version",
                "validity_start",
                "validity_end",
            }.issubset(asset_columns)
        )
        self.assertTrue(
            {
                "knowledge_scope",
                "owner",
                "scope_task_type",
                "scope_module",
                "scope_language",
                "scope_framework",
                "review_status",
                "temperature",
                "quarantine_status",
                "version",
                "validity_start",
                "validity_end",
            }.issubset(candidate_columns)
        )

    def test_upsert_asset_projects_governance_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            upsert_asset(
                db_path,
                {
                    "asset_id": "pattern_demo_001",
                    "workspace": "/tmp/demo",
                    "asset_type": "pattern",
                    "knowledge_kind": "pattern",
                    "knowledge_scope": "project",
                    "scope": {"level": "module", "value": "runtime/storage"},
                    "scope_profile": {
                        "task_type": "implementation",
                        "module": "runtime/storage",
                        "language": "python",
                        "framework": None,
                    },
                    "status": "active",
                    "confidence": 0.84,
                    "created_at": "2026-05-18T10:00:00+00:00",
                    "updated_at": "2026-05-18T11:00:00+00:00",
                    "delivery": {"owner": "project"},
                    "effectiveness_summary": {"review_status": "healthy", "temperature": "warm"},
                    "governance": {
                        "knowledge_scope": "project",
                        "owner": "project",
                        "review_status": "healthy",
                        "temperature": "warm",
                        "quarantine_status": "active",
                        "version": "3",
                        "validity_window": {
                            "starts_at": "2026-05-18T10:00:00+00:00",
                            "ends_at": None,
                        },
                    },
                },
            )

            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    """
                    SELECT knowledge_scope, owner, review_status, temperature,
                           quarantine_status, version, validity_start, validity_end,
                           scope_task_type, scope_module, scope_language, scope_framework
                    FROM assets
                    WHERE asset_id = ?
                    """,
                    ("pattern_demo_001",),
                ).fetchone()

        self.assertEqual(
            row,
            (
                "project",
                "project",
                "healthy",
                "warm",
                "active",
                "3",
                "2026-05-18T10:00:00+00:00",
                None,
                "implementation",
                "runtime/storage",
                "python",
                None,
            ),
        )

    def test_upsert_candidate_projects_governance_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            upsert_candidate(
                db_path,
                {
                    "candidate_id": "cand_demo_001",
                    "workspace": "/tmp/demo",
                    "candidate_type": "pattern",
                    "knowledge_kind": "pattern",
                    "knowledge_scope": "project",
                    "scope": {"level": "task-family", "value": "python-import-error"},
                    "scope_profile": {
                        "task_type": "bugfix",
                        "module": "pkg/module.py",
                        "language": "python",
                        "framework": "pytest",
                    },
                    "status": "new",
                    "confidence_score": 0.7,
                    "reusability_score": 0.72,
                    "stability_score": 0.74,
                    "constraint_value_score": 0.68,
                    "created_at": "2026-05-18T10:00:00+00:00",
                    "governance": {
                        "knowledge_scope": "project",
                        "owner": "project",
                        "review_status": "unproven",
                        "temperature": "neutral",
                        "quarantine_status": "active",
                        "version": "2",
                        "validity_window": {
                            "starts_at": "2026-05-18T10:00:00+00:00",
                            "ends_at": None,
                        },
                    },
                },
            )

            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    """
                    SELECT knowledge_scope, owner, review_status, temperature,
                           quarantine_status, version, validity_start, validity_end,
                           scope_task_type, scope_module, scope_language, scope_framework
                    FROM candidates
                    WHERE candidate_id = ?
                    """,
                    ("cand_demo_001",),
                ).fetchone()

        self.assertEqual(
            row,
            (
                "project",
                "project",
                "unproven",
                "neutral",
                "active",
                "2",
                "2026-05-18T10:00:00+00:00",
                None,
                "bugfix",
                "pkg/module.py",
                "python",
                "pytest",
            ),
        )

    def test_set_asset_quarantine_status_updates_payload_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            upsert_asset(
                db_path,
                {
                    "asset_id": "pattern_demo_quarantine",
                    "workspace": "/tmp/demo",
                    "asset_type": "pattern",
                    "knowledge_kind": "pattern",
                    "knowledge_scope": "project",
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "status": "active",
                    "confidence": 0.8,
                    "created_at": "2026-05-18T10:00:00+00:00",
                    "updated_at": "2026-05-18T10:00:00+00:00",
                },
            )

            updated = set_asset_quarantine_status(
                db_path,
                asset_id="pattern_demo_quarantine",
                quarantine_status="quarantined",
                reason="negative replay result",
                updated_at="2026-05-18T11:00:00+00:00",
            )

        self.assertIsNotNone(updated)
        self.assertEqual(updated["quarantine_status"], "quarantined")
        self.assertEqual(updated["review_status"], "needs_review")
        self.assertEqual(updated["governance"]["quarantine_status"], "quarantined")
        self.assertEqual(updated["governance_history"][-1]["reason"], "negative replay result")

    def test_deprecate_asset_updates_status_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            upsert_asset(
                db_path,
                {
                    "asset_id": "pattern_demo_deprecated",
                    "workspace": "/tmp/demo",
                    "asset_type": "pattern",
                    "knowledge_kind": "pattern",
                    "knowledge_scope": "project",
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "status": "active",
                    "confidence": 0.8,
                    "created_at": "2026-05-18T10:00:00+00:00",
                    "updated_at": "2026-05-18T10:00:00+00:00",
                },
            )

            updated = deprecate_asset(
                db_path,
                asset_id="pattern_demo_deprecated",
                reason="replay kept missing in the right scope",
                updated_at="2026-05-18T11:00:00+00:00",
            )

        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "deprecated")
        self.assertEqual(updated["quarantine_status"], "deprecated")
        self.assertEqual(updated["review_status"], "needs_review")
        self.assertEqual(updated["temperature"], "cool")
        self.assertEqual(updated["governance"]["quarantine_status"], "deprecated")
        self.assertEqual(updated["governance_history"][-1]["action"], "deprecate_asset")

    def test_reactivate_asset_restores_active_watch_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            upsert_asset(
                db_path,
                {
                    "asset_id": "pattern_demo_reactivate",
                    "workspace": "/tmp/demo",
                    "asset_type": "pattern",
                    "knowledge_kind": "pattern",
                    "knowledge_scope": "project",
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "status": "deprecated",
                    "review_status": "needs_review",
                    "temperature": "cool",
                    "quarantine_status": "deprecated",
                    "created_at": "2026-05-18T10:00:00+00:00",
                    "updated_at": "2026-05-18T10:00:00+00:00",
                },
            )

            updated = reactivate_asset(
                db_path,
                asset_id="pattern_demo_reactivate",
                reason="fresh replay evidence supports limited reuse",
                updated_at="2026-05-18T11:00:00+00:00",
            )

        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "active")
        self.assertEqual(updated["quarantine_status"], "active")
        self.assertEqual(updated["review_status"], "watch")
        self.assertEqual(updated["temperature"], "cool")
        self.assertEqual(updated["governance_history"][-1]["action"], "reactivate_asset")

    def test_mark_asset_conflict_records_bidirectional_relationship(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            for asset_id in ("pattern_a", "pattern_b"):
                upsert_asset(
                    db_path,
                    {
                        "asset_id": asset_id,
                        "workspace": "/tmp/demo",
                        "asset_type": "pattern",
                        "knowledge_kind": "pattern",
                        "knowledge_scope": "project",
                        "scope": {"level": "workspace", "value": "general-coding-task"},
                        "status": "active",
                        "confidence": 0.8,
                        "created_at": "2026-05-18T10:00:00+00:00",
                        "updated_at": "2026-05-18T10:00:00+00:00",
                    },
                )

            left, right = mark_asset_conflict(
                db_path,
                asset_id="pattern_a",
                conflicting_asset_id="pattern_b",
                updated_at="2026-05-18T11:00:00+00:00",
            )

            stored_left = get_asset(db_path, asset_id="pattern_a")
            stored_right = get_asset(db_path, asset_id="pattern_b")

        self.assertIn("pattern_b", left["conflicts_with"])
        self.assertIn("pattern_a", right["conflicts_with"])
        self.assertIn("pattern_b", stored_left["conflicts_with"])
        self.assertIn("pattern_a", stored_right["conflicts_with"])

    def test_resolve_asset_conflict_removes_bidirectional_relationship(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            for asset_id, conflicts_with in (
                ("pattern_a", ["pattern_b"]),
                ("pattern_b", ["pattern_a"]),
            ):
                upsert_asset(
                    db_path,
                    {
                        "asset_id": asset_id,
                        "workspace": "/tmp/demo",
                        "asset_type": "pattern",
                        "knowledge_kind": "pattern",
                        "knowledge_scope": "project",
                        "scope": {"level": "workspace", "value": "general-coding-task"},
                        "status": "active",
                        "confidence": 0.8,
                        "conflicts_with": conflicts_with,
                        "created_at": "2026-05-18T10:00:00+00:00",
                        "updated_at": "2026-05-18T10:00:00+00:00",
                    },
                )

            left, right = resolve_asset_conflict(
                db_path,
                asset_id="pattern_a",
                conflicting_asset_id="pattern_b",
                reason="confirmed compatible after replay",
                updated_at="2026-05-18T11:00:00+00:00",
            )
            stored_left = get_asset(db_path, asset_id="pattern_a")
            stored_right = get_asset(db_path, asset_id="pattern_b")

        assert left is not None and right is not None
        assert stored_left is not None and stored_right is not None
        self.assertNotIn("pattern_b", left["conflicts_with"])
        self.assertNotIn("pattern_a", right["conflicts_with"])
        self.assertEqual(stored_left["conflicts_with"], [])
        self.assertEqual(stored_right["conflicts_with"], [])
        self.assertEqual(stored_left["governance_history"][-1]["action"], "resolve_conflict")

    def test_build_asset_validation_queue_prioritizes_replay_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            upsert_asset(
                db_path,
                {
                    "asset_id": "pattern_unproven_001",
                    "workspace": "/tmp/demo",
                    "asset_type": "pattern",
                    "knowledge_kind": "pattern",
                    "knowledge_scope": "project",
                    "title": "新经验待验证",
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "scope_profile": {"task_type": "bugfix", "module": "runtime"},
                    "status": "active",
                    "confidence": 0.86,
                    "created_at": "2026-05-18T10:00:00+00:00",
                    "updated_at": "2026-05-18T10:00:00+00:00",
                    "review_status": "unproven",
                },
            )
            upsert_asset(
                db_path,
                {
                    "asset_id": "pattern_cold_001",
                    "workspace": "/tmp/demo",
                    "asset_type": "pattern",
                    "knowledge_kind": "pattern",
                    "knowledge_scope": "project",
                    "title": "多次命中但帮助弱",
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "status": "active",
                    "confidence": 0.84,
                    "created_at": "2026-05-18T10:00:00+00:00",
                    "updated_at": "2026-05-18T10:00:00+00:00",
                },
            )
            for index in range(4):
                log_activation(
                    db_path,
                    {
                        "activation_id": f"act_{index}",
                        "workspace": "/tmp/demo",
                        "task_query": "demo",
                        "selected_assets": [{"asset_id": "pattern_cold_001"}],
                        "created_at": f"2026-05-18T10:0{index}:00+00:00",
                        "feedback": {"help_signal": "unclear"},
                    },
                )

            queue = build_asset_validation_queue(db_path, workspace="/tmp/demo", limit=10)

        self.assertEqual(queue["total_assets"], 2)
        self.assertGreaterEqual(queue["pending_validation_count"], 2)
        self.assertEqual(queue["items"][0]["asset_id"], "pattern_unproven_001")
        self.assertIn(queue["items"][1]["suggested_action"], {"replay_or_quarantine", "review_or_quarantine"})

    def test_build_governance_summary_reports_counts_and_top_validation_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            upsert_asset(
                db_path,
                {
                    "asset_id": "pattern_summary_001",
                    "workspace": "/tmp/demo",
                    "asset_type": "pattern",
                    "knowledge_kind": "pattern",
                    "knowledge_scope": "project",
                    "title": "待验证经验",
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "status": "active",
                    "confidence": 0.82,
                    "review_status": "unproven",
                    "created_at": "2026-05-18T10:00:00+00:00",
                    "updated_at": "2026-05-18T10:00:00+00:00",
                },
            )
            upsert_asset(
                db_path,
                {
                    "asset_id": "pattern_summary_002",
                    "workspace": "/tmp/demo",
                    "asset_type": "pattern",
                    "knowledge_kind": "pattern",
                    "knowledge_scope": "project",
                    "title": "已隔离经验",
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "status": "active",
                    "confidence": 0.82,
                    "review_status": "needs_review",
                    "quarantine_status": "quarantined",
                    "conflicts_with": ["pattern_summary_001"],
                    "created_at": "2026-05-18T10:00:00+00:00",
                    "updated_at": "2026-05-18T10:00:00+00:00",
                },
            )

            summary = build_governance_summary(db_path, workspace="/tmp/demo", validation_limit=5)

        self.assertEqual(summary["asset_count"], 2)
        self.assertEqual(summary["review_status_counts"]["unproven"], 1)
        self.assertEqual(summary["review_status_counts"]["needs_review"], 1)
        self.assertEqual(summary["quarantine_status_counts"]["quarantined"], 1)
        self.assertEqual(summary["deprecated_asset_count"], 0)
        self.assertEqual(summary["conflict_asset_count"], 1)
        self.assertTrue(summary["top_validation_items"])
