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
