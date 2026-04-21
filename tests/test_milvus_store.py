import tempfile
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
                with patch.object(milvus_store, "milvus_available", return_value=True):
                    summary = milvus_store.milvus_backend_summary(db_path)
            finally:
                milvus_store.fcntl.flock(lock_file.fileno(), milvus_store.fcntl.LOCK_UN)
                lock_file.close()

            self.assertEqual(summary["status"], "degraded")
            self.assertEqual(summary["degraded_reason"], "locked_by_another_process")
            self.assertFalse(summary["collection_exists"])

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


if __name__ == "__main__":
    unittest.main()
