import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.backends import resolve_backend_config
from runtime.storage.fs_store import default_milvus_db_path, memory_root_for_workspace, storage_layout_for_workspace


class BackendConfigTests(unittest.TestCase):
    def test_resolve_backend_config_defaults_to_local_mode(self) -> None:
        config = resolve_backend_config({})

        self.assertEqual(config["profile"], "local")
        self.assertEqual(config["storage_profile"], "local")
        self.assertEqual(config["source_of_truth"], "local-json")
        self.assertEqual(config["state_index"], "sqlite")
        self.assertEqual(config["retrieval"], "milvus-lite")
        self.assertEqual(config["state_index_role"], "lightweight-state-index")
        self.assertEqual(config["retrieval_role"], "core-semantic-retrieval")
        self.assertEqual(config["sharing"], "local-shared")
        self.assertFalse(config["cloud_enabled"])
        self.assertTrue(config["local_mode"])
        self.assertFalse(config["shareable_enabled"])
        self.assertEqual(config["asset_portability"], "local-deliverable")
        self.assertEqual(config["data_source_mode"], "project-local")
        self.assertTrue(config["project_owned_assets"])
        self.assertTrue(config["local_runtime_data_in_project"])

    def test_resolve_backend_config_accepts_hybrid_cloud_overrides(self) -> None:
        config = resolve_backend_config(
            {
                "EXPCAP_SOURCE_OF_TRUTH_BACKEND": "object-storage",
                "EXPCAP_STATE_INDEX_BACKEND": "cloud-sql",
                "EXPCAP_RETRIEVAL_BACKEND": "milvus",
                "EXPCAP_SHARING_BACKEND": "cloud-shared",
                "EXPCAP_PROJECT_ID": "github:org/repo",
                "EXPCAP_OWNING_TEAM": "agent-platform",
                "EXPCAP_ASSET_STORE_URI": "oss://bucket/expcap/assets",
                "EXPCAP_STATE_INDEX_URI": "postgres://expcap",
                "EXPCAP_RETRIEVAL_INDEX_URI": "https://milvus.example.com",
                "EXPCAP_SHARED_ASSET_STORE_URI": "oss://bucket/expcap/shared",
            }
        )

        self.assertEqual(config["profile"], "shared")
        self.assertEqual(config["storage_profile"], "shared")
        self.assertEqual(config["source_of_truth"], "object-storage")
        self.assertEqual(config["state_index"], "cloud-sql")
        self.assertEqual(config["retrieval"], "milvus")
        self.assertEqual(config["state_index_role"], "shared-state-index")
        self.assertEqual(config["retrieval_role"], "core-semantic-retrieval")
        self.assertEqual(config["sharing"], "cloud-shared")
        self.assertTrue(config["cloud_enabled"])
        self.assertFalse(config["local_mode"])
        self.assertTrue(config["shareable_enabled"])
        self.assertEqual(config["asset_portability"], "team-shareable")
        self.assertEqual(config["data_source_mode"], "shared-source")
        self.assertEqual(config["project_identity"]["project_id"], "github:org/repo")
        self.assertEqual(config["project_identity"]["owning_team"], "agent-platform")
        self.assertEqual(config["backend_uris"]["asset_store"], "oss://bucket/expcap/assets")
        self.assertEqual(config["backend_uris"]["state_index"], "postgres://expcap")
        self.assertEqual(config["backend_uris"]["retrieval_index"], "https://milvus.example.com")
        self.assertEqual(config["backend_uris"]["shared_asset_store"], "oss://bucket/expcap/shared")

    def test_resolve_backend_config_falls_back_for_unknown_values(self) -> None:
        config = resolve_backend_config(
            {
                "EXPCAP_SOURCE_OF_TRUTH_BACKEND": "mystery",
                "EXPCAP_STATE_INDEX_BACKEND": "unknown",
                "EXPCAP_RETRIEVAL_BACKEND": "none",
                "EXPCAP_SHARING_BACKEND": "whatever",
            }
        )

        self.assertEqual(config["source_of_truth"], "local-json")
        self.assertEqual(config["state_index"], "sqlite")
        self.assertEqual(config["retrieval"], "milvus-lite")
        self.assertEqual(config["retrieval_role"], "core-semantic-retrieval")
        self.assertEqual(config["sharing"], "local-shared")
        self.assertEqual(config["storage_profile"], "local")

    def test_resolve_backend_config_supports_user_cache_profile(self) -> None:
        config = resolve_backend_config({"EXPCAP_STORAGE_PROFILE": "user-cache"})

        self.assertEqual(config["profile"], "user-cache")
        self.assertEqual(config["source_of_truth"], "local-json")
        self.assertEqual(config["state_index"], "sqlite")
        self.assertEqual(config["retrieval"], "milvus-lite")
        self.assertEqual(config["state_index_role"], "lightweight-state-index")
        self.assertEqual(config["retrieval_role"], "core-semantic-retrieval")
        self.assertFalse(config["shareable_enabled"])
        self.assertEqual(config["asset_portability"], "user-cache")
        self.assertEqual(config["data_source_mode"], "user-cache")
        self.assertFalse(config["local_runtime_data_in_project"])

    def test_user_cache_storage_layout_keeps_runtime_data_outside_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "repo"
            expcap_home = (Path(tmpdir) / "expcap-home").resolve()
            workspace.mkdir()

            with patch.dict(
                os.environ,
                {
                    "EXPCAP_STORAGE_PROFILE": "user-cache",
                    "EXPCAP_HOME": str(expcap_home),
                    "EXPCAP_PROJECT_ID": "github:org/repo",
                },
            ):
                memory_root = memory_root_for_workspace(workspace)
                layout = storage_layout_for_workspace(workspace)

            self.assertTrue(str(memory_root).startswith(str(expcap_home / "projects")))
            self.assertFalse(str(memory_root).startswith(str(workspace)))
            self.assertEqual(layout["storage_profile"], "user-cache")
            self.assertEqual(layout["data_source_mode"], "user-cache")
            self.assertTrue(layout["project_owned_assets"])
            self.assertFalse(layout["local_runtime_data_in_project"])
            self.assertEqual(layout["retrieval_index_profile"], "hash-token-sha256-signhash-128")
            self.assertTrue(layout["retrieval_index_path"].endswith("milvus.hash-token-sha25-a1e82f9f.db"))
            self.assertTrue(layout["legacy_retrieval_index_path"].endswith("milvus.db"))

    def test_milvus_path_is_namespaced_by_embedding_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "repo"
            workspace.mkdir()

            with patch.dict(os.environ, {"EXPCAP_EMBEDDING_PROVIDER": "hash"}, clear=False):
                hash_path = default_milvus_db_path(workspace)

            with patch.dict(
                os.environ,
                {
                    "EXPCAP_EMBEDDING_PROVIDER": "openai",
                    "EXPCAP_OPENAI_API_KEY": "test-key",
                    "EXPCAP_OPENAI_EMBEDDING_MODEL": "text-embedding-3-small",
                    "EXPCAP_OPENAI_EMBEDDING_DIM": "128",
                },
                clear=False,
            ):
                openai_path = default_milvus_db_path(workspace)

            self.assertNotEqual(hash_path, openai_path)
            self.assertLess(len(hash_path.name), 36)
            self.assertLess(len(openai_path.name), 36)
            self.assertTrue(hash_path.name.startswith("milvus.hash-token-sha25-"))
            self.assertTrue(openai_path.name.startswith("milvus.openai-text-embe-"))

    def test_openai_missing_key_fallback_reuses_hash_profile_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "repo"
            workspace.mkdir()

            with patch.dict(os.environ, {"EXPCAP_EMBEDDING_PROVIDER": "hash"}, clear=False):
                hash_path = default_milvus_db_path(workspace)

            with patch.dict(
                os.environ,
                {
                    "EXPCAP_EMBEDDING_PROVIDER": "openai",
                    "EXPCAP_OPENAI_API_KEY": "",
                    "OPENAI_API_KEY": "",
                },
                clear=False,
            ):
                fallback_path = default_milvus_db_path(workspace)
                layout = storage_layout_for_workspace(workspace)

            self.assertEqual(fallback_path, hash_path)
            self.assertEqual(layout["retrieval_index_profile"], "hash-token-sha256-signhash-128")


if __name__ == "__main__":
    unittest.main()
