import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.storage import embeddings
from runtime.storage import milvus_store


@unittest.skipIf(milvus_store.fcntl is None, "fcntl is required for Milvus Lite lock tests")
class MilvusStoreLockTests(unittest.TestCase):
    def _hold_lock(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = db_path.with_name(f"{db_path.name}.lock")
        lock_file = lock_path.open("a+", encoding="utf-8")
        milvus_store.fcntl.flock(lock_file.fileno(), milvus_store.fcntl.LOCK_EX)
        return lock_file

    def test_backend_summary_deep_check_reports_degraded_when_db_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "milvus.db"
            lock_file = self._hold_lock(db_path)
            try:
                with patch.object(milvus_store, "milvus_available", return_value=True), patch.object(
                    milvus_store,
                    "milvus_runtime_available",
                    return_value=True,
                ):
                    summary = milvus_store.milvus_backend_summary(db_path, deep_check=True)
            finally:
                milvus_store.fcntl.flock(lock_file.fileno(), milvus_store.fcntl.LOCK_UN)
                lock_file.close()

            self.assertEqual(summary["status"], "degraded")
            self.assertEqual(summary["degraded_reason"], "locked_by_another_process")
            self.assertFalse(summary["collection_exists"])

    def test_backend_summary_lightweight_does_not_block_on_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "milvus.db"
            db_path.touch()
            lock_file = self._hold_lock(db_path)
            try:
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
            finally:
                milvus_store.fcntl.flock(lock_file.fileno(), milvus_store.fcntl.LOCK_UN)
                lock_file.close()

            self.assertEqual(summary["status"], "ready")
            self.assertIsNone(summary["degraded_reason"])

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
            self.assertEqual(summary["embedding"]["provider"], "hash")
            self.assertEqual(summary["embedding"]["model"], "token-sha256-signhash")
            self.assertEqual(summary["embedding"]["dim"], 128)
            self.assertEqual(summary["embedding"]["version"], "1")

    def test_prepare_asset_document_includes_embedding_metadata(self) -> None:
        document = milvus_store.prepare_asset_document(
            {
                "asset_id": "asset_1",
                "asset_type": "pattern",
                "knowledge_kind": "pattern",
                "knowledge_scope": "project",
                "title": "Milvus embedding provider",
                "content": "Store provider metadata with vectors.",
                "scope": {"level": "workspace", "value": "demo"},
            }
        )

        self.assertEqual(len(document["vector"]), 128)
        self.assertEqual(document["embedding_provider"], "hash")
        self.assertEqual(document["embedding_requested_provider"], "hash")
        self.assertEqual(document["embedding_model"], "token-sha256-signhash")
        self.assertEqual(document["embedding_dim"], 128)
        self.assertEqual(document["embedding_version"], "1")
        self.assertEqual(document["embedding_status"], "ready")
        self.assertEqual(document["embedding_profile"], "hash-token-sha256-signhash-128")

    def test_openai_embedding_provider_without_key_falls_back_to_hash(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EXPCAP_EMBEDDING_PROVIDER": "openai",
                "OPENAI_API_KEY": "",
                "EXPCAP_OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            document = milvus_store.prepare_asset_document(
                {
                    "asset_id": "asset_1",
                    "title": "Fallback embedding",
                    "content": "Missing API keys should not break local tests.",
                }
            )

        self.assertEqual(document["embedding_provider"], "hash")
        self.assertEqual(document["embedding_requested_provider"], "openai")
        self.assertEqual(document["embedding_status"], "fallback")
        self.assertEqual(document["embedding_profile"], "hash-token-sha256-signhash-128")

    def test_unsupported_embedding_provider_falls_back_to_hash(self) -> None:
        with patch.dict(os.environ, {"EXPCAP_EMBEDDING_PROVIDER": "unknown"}, clear=False):
            document = milvus_store.prepare_asset_document(
                {
                    "asset_id": "asset_1",
                    "title": "Fallback embedding",
                    "content": "Unsupported providers should not break local tests.",
                }
            )

        self.assertEqual(document["embedding_provider"], "hash")
        self.assertEqual(document["embedding_requested_provider"], "unknown")
        self.assertEqual(document["embedding_status"], "fallback")

    def test_openai_embedding_provider_uses_embeddings_api(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({"data": [{"embedding": [0.1, 0.2, -0.3, 0.4]}]}).encode("utf-8")

        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["authorization"] = request.headers.get("Authorization")
            return FakeResponse()

        with patch.dict(
            os.environ,
            {
                "EXPCAP_EMBEDDING_PROVIDER": "openai",
                "EXPCAP_OPENAI_API_KEY": "test-key",
                "EXPCAP_OPENAI_EMBEDDING_MODEL": "text-embedding-3-small",
                "EXPCAP_OPENAI_EMBEDDING_DIM": "4",
                "EXPCAP_OPENAI_BASE_URL": "https://example.test",
                "EXPCAP_OPENAI_TIMEOUT_SECONDS": "7",
            },
            clear=False,
        ), patch.object(embeddings.urllib.request, "urlopen", side_effect=fake_urlopen):
            vector = embeddings.embed_text("semantic retrieval")
            config = embeddings.embedding_provider_config()

        self.assertEqual(vector, [0.1, 0.2, -0.3, 0.4])
        self.assertEqual(captured["url"], "https://example.test/v1/embeddings")
        self.assertEqual(captured["timeout"], 7.0)
        self.assertEqual(captured["authorization"], "Bearer test-key")
        self.assertEqual(
            captured["body"],
            {
                "model": "text-embedding-3-small",
                "input": "semantic retrieval",
                "dimensions": 4,
            },
        )
        self.assertEqual(config["provider"], "openai")
        self.assertEqual(config["dim"], 4)
        self.assertEqual(config["status"], "ready")

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
                summary = milvus_store.milvus_backend_summary(db_path, deep_check=True)

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


class MilvusRemoteStoreTests(unittest.TestCase):
    def test_remote_milvus_summary_is_configured_without_deep_check(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EXPCAP_RETRIEVAL_BACKEND": "milvus",
                "EXPCAP_RETRIEVAL_INDEX_URI": "https://user:secret@milvus.example.com/path?token=secret",
            },
            clear=False,
        ), patch.object(milvus_store, "milvus_available", return_value=True), patch.object(
            milvus_store,
            "milvus_runtime_available",
            side_effect=AssertionError("remote Milvus should not need Milvus Lite runtime probe"),
        ):
            summary = milvus_store.milvus_backend_summary(Path("/tmp/missing-local.db"))

        self.assertEqual(summary["backend"], "milvus")
        self.assertEqual(summary["mode"], "remote")
        self.assertEqual(summary["status"], "configured")
        self.assertEqual(summary["remote_uri"], "https://milvus.example.com/path")
        self.assertTrue(summary["remote_configured"])
        self.assertIsNone(summary["db_path"])
        self.assertIsNone(summary["db_exists"])

    def test_remote_milvus_summary_reports_missing_uri(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EXPCAP_RETRIEVAL_BACKEND": "milvus",
                "EXPCAP_RETRIEVAL_INDEX_URI": "",
            },
            clear=False,
        ), patch.object(milvus_store, "milvus_available", return_value=True):
            summary = milvus_store.milvus_backend_summary(Path("/tmp/missing-local.db"))

        self.assertEqual(summary["backend"], "milvus")
        self.assertEqual(summary["mode"], "remote")
        self.assertEqual(summary["status"], "not_configured")
        self.assertFalse(summary["remote_configured"])
        self.assertEqual(summary["degraded_reason"], "missing_retrieval_index_uri")

    def test_remote_milvus_client_uses_uri_token_and_db_name(self) -> None:
        captured: dict[str, object] = {}

        class FakeMilvusClient:
            def __init__(self, *args, **kwargs) -> None:
                captured["args"] = args
                captured["kwargs"] = kwargs

        fake_pymilvus = types.SimpleNamespace(MilvusClient=FakeMilvusClient)
        with patch.dict(sys.modules, {"pymilvus": fake_pymilvus}), patch.dict(
            os.environ,
            {
                "EXPCAP_RETRIEVAL_BACKEND": "milvus",
                "EXPCAP_RETRIEVAL_INDEX_URI": "https://milvus.example.com",
                "EXPCAP_RETRIEVAL_INDEX_TOKEN": "token-value",
                "EXPCAP_MILVUS_DB_NAME": "expcap",
            },
            clear=False,
        ):
            milvus_store._client(Path("/tmp/ignored.db"))

        self.assertEqual(captured["args"], ())
        self.assertEqual(
            captured["kwargs"],
            {
                "uri": "https://milvus.example.com",
                "token": "token-value",
                "db_name": "expcap",
            },
        )

    def test_remote_asset_document_uses_project_id_as_index_workspace(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EXPCAP_RETRIEVAL_BACKEND": "milvus",
                "EXPCAP_RETRIEVAL_INDEX_URI": "https://milvus.example.com",
                "EXPCAP_PROJECT_ID": "github:org/repo",
            },
            clear=False,
        ):
            document = milvus_store.prepare_asset_document(
                {
                    "asset_id": "asset_remote",
                    "workspace": "/Users/alice/repo",
                    "title": "Remote project identity",
                    "content": "Use stable project identity in remote vector filters.",
                }
            )

        self.assertEqual(document["workspace"], "github:org/repo")
        self.assertEqual(document["source_workspace"], "/Users/alice/repo")

    def test_remote_search_does_not_require_local_db_file(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.filter = None

            def has_collection(self, collection_name: str) -> bool:
                return True

            def search(self, collection_name: str, data: list[list[float]], filter: str, limit: int, output_fields: list[str]):
                self.filter = filter
                return [
                    [
                        {
                            "id": "asset_remote",
                            "distance": 0.82,
                            "entity": {
                                "asset_id": "asset_remote",
                                "knowledge_scope": "project",
                                "knowledge_kind": "pattern",
                                "title": "Remote Milvus asset",
                                "content": "Cloud retrieval is reachable.",
                            },
                        }
                    ]
                ]

        fake_client = FakeClient()
        with patch.dict(
            os.environ,
            {
                "EXPCAP_RETRIEVAL_BACKEND": "milvus",
                "EXPCAP_RETRIEVAL_INDEX_URI": "https://milvus.example.com",
                "EXPCAP_PROJECT_ID": 'github:org/re"po',
            },
            clear=False,
        ), patch.object(milvus_store, "milvus_available", return_value=True), patch.object(
            milvus_store,
            "_safe_client_unlocked",
            return_value=fake_client,
        ):
            results = milvus_store.search_asset_vectors(
                Path("/tmp/does-not-exist.db"),
                query_text="cloud retrieval",
                limit=3,
                knowledge_scope="project",
                workspace="/Users/alice/repo",
            )

        self.assertEqual(results[0]["asset_id"], "asset_remote")
        self.assertEqual(results[0]["vector_score"], 0.82)
        self.assertEqual(fake_client.filter, 'knowledge_scope == "project" and workspace == "github:org/re\\"po"')

    def test_remote_prune_is_disabled_to_avoid_cross_team_deletes(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.deleted_ids: list[str] = []

            def has_collection(self, collection_name: str) -> bool:
                return True

            def upsert(self, collection_name: str, data: list[dict]) -> None:
                return None

            def query(self, collection_name: str, filter: str, output_fields: list[str], limit: int) -> list[dict]:
                return [{"asset_id": "other_team_asset"}]

            def delete(self, collection_name: str, ids: list[str]) -> dict:
                self.deleted_ids.extend(ids)
                return {"delete_count": len(ids)}

        with tempfile.TemporaryDirectory() as tmpdir:
            assets_dir = Path(tmpdir) / "assets" / "patterns"
            assets_dir.mkdir(parents=True)
            (assets_dir / "asset_live.json").write_text(
                json.dumps({"asset_id": "asset_live", "title": "Live", "content": "Current team asset"}),
                encoding="utf-8",
            )
            fake_client = FakeClient()
            with patch.dict(
                os.environ,
                {
                    "EXPCAP_RETRIEVAL_BACKEND": "milvus",
                    "EXPCAP_RETRIEVAL_INDEX_URI": "https://milvus.example.com",
                },
                clear=False,
            ), patch.object(milvus_store, "milvus_available", return_value=True), patch.object(
                milvus_store,
                "_safe_client_unlocked",
                return_value=fake_client,
            ):
                report = milvus_store.sync_assets_directory_with_report(
                    Path("/tmp/does-not-exist.db"),
                    Path(tmpdir) / "assets",
                    prune=True,
                )

        self.assertEqual(report["synced"], 1)
        self.assertEqual(report["pruned"], 0)
        self.assertEqual(report["prune_skipped_reason"], "remote_prune_disabled")
        self.assertEqual(fake_client.deleted_ids, [])


if __name__ == "__main__":
    unittest.main()
