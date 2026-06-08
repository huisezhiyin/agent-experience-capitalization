import unittest

from runtime.core.governance_views import build_governance_dashboard_view
from runtime.core.governance_views import build_governance_status_view
from runtime.core.governance_views import build_validation_queue_view


class GovernanceViewTests(unittest.TestCase):
    def test_build_validation_queue_view_summarizes_actions_and_statuses(self) -> None:
        queue = {
            "items": [
                {
                    "asset_id": "pattern_a",
                    "review_status": "unproven",
                    "suggested_action": "replay",
                },
                {
                    "asset_id": "pattern_b",
                    "review_status": "needs_review",
                    "suggested_action": "review_or_quarantine",
                },
            ],
            "total_assets": 6,
            "pending_validation_count": 2,
        }

        view = build_validation_queue_view(queue)

        self.assertEqual(view["kind"], "validation_queue_view")
        self.assertEqual(view["summary"]["total_assets"], 6)
        self.assertEqual(view["summary"]["pending_validation_count"], 2)
        self.assertEqual(view["summary"]["action_counts"]["replay"], 1)
        self.assertEqual(view["summary"]["review_status_counts"]["needs_review"], 1)
        self.assertEqual(view["top_items"][0]["asset_id"], "pattern_a")

    def test_build_governance_status_view_builds_headline_and_focus(self) -> None:
        summary = {
            "asset_count": 10,
            "pending_validation_count": 3,
            "conflict_asset_count": 1,
            "deprecated_asset_count": 2,
            "review_status_counts": {"healthy": 6, "unproven": 2, "needs_review": 2},
            "quarantine_status_counts": {"active": 7, "quarantined": 1, "deprecated": 2},
            "top_validation_items": [
                {"asset_id": "pattern_a", "suggested_action": "replay"},
            ],
        }

        view = build_governance_status_view(summary)

        self.assertEqual(view["kind"], "governance_status_view")
        self.assertIn("assets=10", view["headline"])
        self.assertIn("pending_validation=3", view["headline"])
        self.assertIn("deprecated=2", view["headline"])
        self.assertEqual(view["cards"]["conflict_asset_count"], 1)
        self.assertEqual(view["cards"]["deprecated_asset_count"], 2)
        self.assertEqual(view["focus"]["top_validation_asset_id"], "pattern_a")
        self.assertEqual(view["focus"]["top_validation_action"], "replay")

    def test_build_governance_dashboard_view_combines_status_and_validation(self) -> None:
        summary = {
            "asset_count": 4,
            "pending_validation_count": 1,
            "conflict_asset_count": 0,
            "review_status_counts": {"healthy": 3, "unproven": 1},
            "quarantine_status_counts": {"active": 4},
            "top_validation_items": [
                {"asset_id": "pattern_a", "suggested_action": "replay"},
            ],
        }
        queue = {
            "items": [
                {"asset_id": "pattern_a", "review_status": "unproven", "suggested_action": "replay"},
            ],
            "total_assets": 4,
            "pending_validation_count": 1,
        }

        view = build_governance_dashboard_view(summary, queue)

        self.assertEqual(view["kind"], "governance_dashboard_view")
        self.assertEqual(view["status"]["cards"]["asset_count"], 4)
        self.assertEqual(view["validation"]["summary"]["pending_validation_count"], 1)
        self.assertEqual(view["cards"]["top_validation_items"][0]["asset_id"], "pattern_a")
        self.assertEqual(view["cards"]["validation_action_counts"]["replay"], 1)
