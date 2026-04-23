import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.storage import milvus_store


@unittest.skipIf(milvus_store.fcntl is None, "fcntl is required for Milvus Lite lock tests")
class MilvusStoreLockTests(unittest.TestCase):
    def _hold_lock(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = db_path.with_name(f"{db_path.name}.lock")
        lock_file = lock_path.open("a+", encoding="utf-8")
        milvus_store.fcntl.flock(lock_file.fileno(), milvus_store.fcntl.LOCK_EX)
        return lock_file

    def test_backend_summary_reports_degraded_when_db_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "milvus.db"
            lock_file = self._hold_lock(db_path)
            try:
                with patch.object(milvus_store, "milvus_available", return_value=True), patch.object(
                    milvus_store,
                    "milvus_runtime_available",
                    return_value=True,
                ):
                    summary = milvus_store.milvus_backend_summary(db_path)
            finally:
                milvus_store.fcntl.flock(lock_file.fileno(), milvus_store.fcntl.LOCK_UN)
                lock_file.close()

            self.assertEqual(summary["status"], "degraded")
            self.assertEqual(summary["degraded_reason"], "locked_by_another_process")
            self.assertIsNone(summary["collection_exists"])

    def test_backend_summary_waits_for_transient_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "milvus.db"
            db_path.touch()
            lock_file = self._hold_lock(db_path)

            def release_lock() -> None:
                milvus_store.fcntl.flock(lock_file.fileno(), milvus_store.fcntl.LOCK_UN)
                lock_file.close()

            timer = threading.Timer(0.1, release_lock)
            timer.start()
            try:
                with patch.dict(os.environ, {"EXPCAP_MILVUS_LOCK_WAIT_SECONDS": "1"}), patch.object(
                    milvus_store,
                    "milvus_available",
                    return_value=True,
                ), patch.object(
                    milvus_store,
                    "milvus_runtime_available",
                    return_value=True,
                ):
                    summary = milvus_store.milvus_backend_summary(db_path)
            finally:
                timer.join(timeout=1)

            self.assertEqual(summary["status"], "ready")
            self.assertIsNone(summary["degraded_reason"])

    def test_milvus_lock_summary_reports_owner_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "milvus.db"
            lock_file = self._hold_lock(db_path)
            milvus_store._write_lock_metadata(lock_file)
            try:
                summary = milvus_store.milvus_lock_summary(db_path)
            finally:
                milvus_store.fcntl.flock(lock_file.fileno(), milvus_store.fcntl.LOCK_UN)
                lock_file.close()

            self.assertTrue(summary["lock_exists"])
            self.assertTrue(summary["locked"])
            self.assertEqual(summary["metadata"]["pid"], os.getpid())
            self.assertTrue(summary["pid_exists"])
            self.assertIsInstance(summary["age_seconds"], float)

    def test_backend_summary_is_lightweight_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "milvus.db"
            db_path.touch()
            with patch.object(milvus_store, "milvus_available", return_value=True), patch.object(
                milvus_store,
                "milvus_runtime_available",
                return_value=True,
            ), patch.object(
                milvus_store,
                "_safe_client_unlocked",
                side_effect=AssertionError("lightweight summary should not open a Milvus client"),
            ):
                summary = milvus_store.milvus_backend_summary(db_path)

            self.assertEqual(summary["status"], "ready")
            self.assertFalse(summary["deep_check"])
            self.assertTrue(summary["db_exists"])
            self.assertIsNone(summary["collection_exists"])
            self.assertIsNone(summary["indexed_entities"])

    def test_backend_summary_deep_check_opens_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "milvus.db"
            with patch.object(milvus_store, "milvus_available", return_value=True), patch.object(
                milvus_store,
                "milvus_runtime_available",
                return_value=True,
            ), patch.object(
                milvus_store,
                "_safe_client_unlocked",
                return_value=None,
            ) as safe_client:
                summary = milvus_store.milvus_backend_summary(db_path, deep_check=True)

            safe_client.assert_called_once_with(db_path)
            self.assertTrue(summary["deep_check"])
            self.assertEqual(summary["status"], "degraded")
            self.assertEqual(summary["degraded_reason"], "client_unavailable")

    def test_backend_summary_degrades_when_runtime_cannot_bind_unix_socket(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "milvus.db"
            db_path.touch()
            with patch.object(milvus_store, "milvus_available", return_value=True), patch.object(
                milvus_store,
                "milvus_runtime_available",
                return_value=False,
            ), patch.object(
                milvus_store,
                "_safe_client_unlocked",
                side_effect=AssertionError("runtime degradation should not open a Milvus client"),
            ):
                summary = milvus_store.milvus_backend_summary(db_path)

            self.assertEqual(summary["status"], "degraded")
            self.assertFalse(summary["runtime_available"])
            self.assertEqual(summary["degraded_reason"], "unix_socket_bind_unavailable")

    def test_backend_summary_degrades_when_lock_file_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "milvus.db"
            with patch.object(milvus_store, "milvus_available", return_value=True), patch.object(
                milvus_store,
                "milvus_runtime_available",
                return_value=True,
            ), patch.object(Path, "open", side_effect=PermissionError("denied")):
                summary = milvus_store.milvus_backend_summary(db_path)

            self.assertEqual(summary["status"], "degraded")
            self.assertTrue(summary["degraded_reason"].startswith("lock_unavailable"))

    def test_vector_operations_skip_when_db_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "milvus.db"
            db_path.touch()
            assets_dir = Path(tmpdir) / "assets"
            assets_dir.mkdir()
            lock_file = self._hold_lock(db_path)
            try:
                with patch.object(milvus_store, "milvus_available", return_value=True), patch.object(
                    milvus_store,
                    "milvus_runtime_available",
                    return_value=True,
                ), patch.object(
                    milvus_store,
                    "_safe_client_unlocked",
                    side_effect=AssertionError("locked operations should not open a Milvus client"),
                ):
                    self.assertFalse(milvus_store.upsert_asset_vector(db_path, {"asset_id": "asset_1"}))
                    self.assertEqual(milvus_store.sync_assets_directory(db_path, assets_dir), 0)
                    self.assertEqual(
                        milvus_store.search_asset_vectors(db_path, query_text="hello", limit=3),
                        [],
                    )
            finally:
                milvus_store.fcntl.flock(lock_file.fileno(), milvus_store.fcntl.LOCK_UN)
                lock_file.close()

    def test_sync_assets_directory_with_report_prunes_stale_vectors(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.upserted_ids: list[str] = []
                self.deleted_ids: list[str] = []

            def has_collection(self, collection_name: str) -> bool:
                return True

            def upsert(self, collection_name: str, data: list[dict]) -> None:
                self.upserted_ids.extend(str(item["asset_id"]) for item in data)

            def query(self, collection_name: str, filter: str, output_fields: list[str], limit: int) -> list[dict]:
                return [{"asset_id": "asset_live"}, {"asset_id": "asset_stale"}]

            def delete(self, collection_name: str, ids: list[str]) -> dict:
                self.deleted_ids.extend(ids)
                return {"delete_count": len(ids)}

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "milvus.db"
            assets_dir = Path(tmpdir) / "assets" / "patterns"
            assets_dir.mkdir(parents=True)
            (assets_dir / "asset_live.json").write_text(
                json.dumps(
                    {
                        "asset_id": "asset_live",
                        "asset_type": "pattern",
                        "title": "Live asset",
                        "content": "Still exists",
                    }
                ),
                encoding="utf-8",
            )
            client = FakeClient()

            with patch.object(milvus_store, "milvus_available", return_value=True), patch.object(
                milvus_store,
                "milvus_runtime_available",
                return_value=True,
            ), patch.object(
                milvus_store,
                "_safe_client_unlocked",
                return_value=client,
            ):
                report = milvus_store.sync_assets_directory_with_report(
                    db_path,
                    Path(tmpdir) / "assets",
                    prune=True,
                )

            self.assertEqual(report, {"synced": 1, "pruned": 1})
            self.assertEqual(client.upserted_ids, ["asset_live"])
            self.assertEqual(client.deleted_ids, ["asset_stale"])


if __name__ == "__main__":
    unittest.main()
