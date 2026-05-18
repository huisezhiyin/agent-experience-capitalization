from __future__ import annotations

from typing import Any


def build_validation_queue_view(queue: dict[str, Any]) -> dict[str, Any]:
    items = list(queue.get("items", []))
    top_items = items[: min(5, len(items))]
    action_counts: dict[str, int] = {}
    review_status_counts: dict[str, int] = {}
    for item in items:
        action = str(item.get("suggested_action") or "unknown")
        action_counts[action] = action_counts.get(action, 0) + 1
        review_status = str(item.get("review_status") or "unknown")
        review_status_counts[review_status] = review_status_counts.get(review_status, 0) + 1
    return {
        "kind": "validation_queue_view",
        "summary": {
            "total_assets": int(queue.get("total_assets", 0) or 0),
            "pending_validation_count": int(queue.get("pending_validation_count", 0) or 0),
            "action_counts": action_counts,
            "review_status_counts": review_status_counts,
        },
        "top_items": top_items,
    }


def build_governance_status_view(summary: dict[str, Any]) -> dict[str, Any]:
    review_status_counts = dict(summary.get("review_status_counts", {}))
    quarantine_status_counts = dict(summary.get("quarantine_status_counts", {}))
    deprecated_asset_count = int(summary.get("deprecated_asset_count", 0) or 0)
    top_validation_items = list(summary.get("top_validation_items", []))
    top_item = top_validation_items[0] if top_validation_items else None
    headline_parts = [
        f"assets={int(summary.get('asset_count', 0) or 0)}",
        f"pending_validation={int(summary.get('pending_validation_count', 0) or 0)}",
        f"conflicts={int(summary.get('conflict_asset_count', 0) or 0)}",
    ]
    if deprecated_asset_count > 0:
        headline_parts.append(f"deprecated={deprecated_asset_count}")
    if quarantine_status_counts:
        headline_parts.append(
            "quarantine="
            + ",".join(f"{key}:{value}" for key, value in sorted(quarantine_status_counts.items()))
        )
    return {
        "kind": "governance_status_view",
        "headline": " | ".join(headline_parts),
        "cards": {
            "asset_count": int(summary.get("asset_count", 0) or 0),
            "pending_validation_count": int(summary.get("pending_validation_count", 0) or 0),
            "conflict_asset_count": int(summary.get("conflict_asset_count", 0) or 0),
            "deprecated_asset_count": deprecated_asset_count,
            "review_status_counts": review_status_counts,
            "quarantine_status_counts": quarantine_status_counts,
        },
        "focus": {
            "top_validation_asset_id": top_item.get("asset_id") if top_item else None,
            "top_validation_action": top_item.get("suggested_action") if top_item else None,
        },
    }


def build_governance_dashboard_view(summary: dict[str, Any], queue: dict[str, Any]) -> dict[str, Any]:
    validation_view = build_validation_queue_view(queue)
    status_view = build_governance_status_view(summary)
    return {
        "kind": "governance_dashboard_view",
        "status": status_view,
        "validation": validation_view,
        "cards": {
            **status_view["cards"],
            "top_validation_items": validation_view["top_items"],
            "validation_action_counts": validation_view["summary"]["action_counts"],
        },
    }
