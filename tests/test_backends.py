import unittest

from runtime.backends import resolve_backend_config


class BackendConfigTests(unittest.TestCase):
    def test_resolve_backend_config_defaults_to_local_mode(self) -> None:
        config = resolve_backend_config({})

        self.assertEqual(config["profile"], "local-mode")
        self.assertEqual(config["source_of_truth"], "local-json")
        self.assertEqual(config["state_index"], "sqlite")
        self.assertEqual(config["retrieval"], "milvus-lite")
        self.assertEqual(config["sharing"], "local-shared")
        self.assertFalse(config["cloud_enabled"])
        self.assertTrue(config["local_mode"])
        self.assertFalse(config["shareable_enabled"])
        self.assertEqual(config["asset_portability"], "local-deliverable")

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

        self.assertEqual(config["profile"], "shareable")
        self.assertEqual(config["source_of_truth"], "object-storage")
        self.assertEqual(config["state_index"], "cloud-sql")
        self.assertEqual(config["retrieval"], "milvus")
        self.assertEqual(config["sharing"], "cloud-shared")
        self.assertTrue(config["cloud_enabled"])
        self.assertFalse(config["local_mode"])
        self.assertTrue(config["shareable_enabled"])
        self.assertEqual(config["asset_portability"], "team-shareable")
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
        self.assertEqual(config["sharing"], "local-shared")


if __name__ == "__main__":
    unittest.main()
