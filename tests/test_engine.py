import os
import unittest
from unittest.mock import patch

from runtime.core.engine import (
    activate_assets,
    apply_asset_effectiveness,
    apply_candidate_promotion_feedback,
    build_candidate_review_queue,
    build_asset_effectiveness_summary,
    explain_object,
    extract_candidates,
    promote_candidate,
    review_trace_bundle,
    should_promote_candidate,
)


class EngineTests(unittest.TestCase):
    def test_review_trace_bundle_builds_structured_episode(self) -> None:
        trace = {
            "trace_id": "trace_20260413_001",
            "host": "codex",
            "workspace": "/tmp/demo",
            "task_hint": "fix pytest import error",
            "constraints": ["不要改 public API", "优先最小改动"],
            "events": [
                {"type": "command", "content": "uv run pytest tests/test_imports.py"},
                {"type": "error", "content": "ModuleNotFoundError: no module named foo"},
            ],
            "files_changed": ["pkg/module.py"],
            "verification": {"status": "passed", "summary": "1 passed"},
            "result": {"status": "success", "summary": "修复导入路径并补充回归测试"},
        }

        episode = review_trace_bundle(trace)

        self.assertEqual(episode["episode_id"], "ep_20260413_001")
        self.assertEqual(episode["scope_hint"], "python-import-error")
        self.assertTrue(episode["turning_points"])
        self.assertIn("优先检查真实包结构与导入路径", episode["lesson"])
        self.assertTrue(any("优先满足显式约束" in item for item in episode["decision_rationale"]))
        self.assertTrue(any("执行命令" in item for item in episode["attempted_paths"]))

    def test_extract_and_promote_keep_workspace_and_stable_titles(self) -> None:
        episode = {
            "episode_id": "ep_20260413_001",
            "trace_id": "trace_20260413_001",
            "goal": "fix pytest import error",
            "constraints": ["不要改 public API"],
            "workspace": "/tmp/demo",
            "files_touched": ["pkg/module.py"],
            "commands": ["uv run pytest tests/test_imports.py"],
            "turning_points": ["验证结果：1 passed"],
            "attempted_paths": ["执行命令：uv run pytest tests/test_imports.py"],
            "abandoned_paths": [],
            "decision_rationale": ["优先满足显式约束：不要改 public API"],
            "result": "success",
            "verification": "1 passed",
            "user_feedback": "accepted",
            "lesson": "遇到 Python 导入错误时，优先检查真实包结构与导入路径，用最小测试验证修复结果，不要先依赖环境补丁。",
            "scope_hint": "python-import-error",
            "confidence_hint": 0.8,
            "created_at": "2026-04-13T00:00:00+00:00",
        }

        [candidate] = extract_candidates(episode)
        asset = promote_candidate(candidate)

        self.assertEqual(candidate["title"], "Python 导入错误处理模式")
        self.assertEqual(candidate["workspace"], "/tmp/demo")
        self.assertEqual(candidate["status"], "new")
        self.assertEqual(asset["workspace"], "/tmp/demo")
        self.assertEqual(asset["asset_type"], "pattern")
        self.assertGreaterEqual(asset["confidence"], 0.75)

    def test_should_promote_candidate_requires_success_passed_and_threshold(self) -> None:
        candidate = {
            "reusability_score": 0.85,
            "stability_score": 0.8,
            "confidence_score": 0.8,
            "constraint_value_score": 0.78,
        }

        self.assertTrue(
            should_promote_candidate(
                candidate,
                verification_status="passed",
                result_status="success",
                min_score=0.70,
            )
        )
        borderline = {
            "reusability_score": 0.68,
            "stability_score": 0.67,
            "confidence_score": 0.68,
            "constraint_value_score": 0.69,
        }
        self.assertFalse(
            should_promote_candidate(
                borderline,
                verification_status="passed",
                result_status="success",
                min_score=0.70,
            )
        )
        boosted = apply_candidate_promotion_feedback(
            borderline,
            activation_feedback={
                "activation_id": "act_demo_001",
                "help_signal": "supported_strong",
                "linked_asset_ids": ["pattern_demo_001"],
            },
        )
        self.assertTrue(
            should_promote_candidate(
                boosted,
                verification_status="passed",
                result_status="success",
                min_score=0.70,
            )
        )

    def test_promote_candidate_can_mark_cross_project_scope_and_kind(self) -> None:
        candidate = {
            "candidate_id": "cand_demo_001",
            "candidate_type": "pattern",
            "knowledge_kind": "rule",
            "workspace": "/tmp/demo",
            "title": "遵循项目接口约束",
            "content": "修改实现时不要破坏既有接口契约。",
            "scope": {"level": "workspace", "value": "general-coding-task"},
            "source_episode_ids": ["ep_demo_001"],
            "reusability_score": 0.8,
            "stability_score": 0.82,
            "confidence_score": 0.81,
            "constraint_value_score": 0.79,
        }

        asset = promote_candidate(candidate, knowledge_scope="cross-project", knowledge_kind="rule")

        self.assertEqual(asset["knowledge_scope"], "cross-project")
        self.assertEqual(asset["knowledge_kind"], "rule")
        self.assertEqual(asset["source_workspace"], "/tmp/demo")
        self.assertEqual(asset["project_id"], "/tmp/demo")
        self.assertEqual(asset["source_project"], "/tmp/demo")
        self.assertTrue(asset["delivery"]["portable"])
        self.assertTrue(asset["delivery"]["shareable"])
        self.assertEqual(asset["delivery"]["owner"], "team")
        self.assertFalse(
            should_promote_candidate(
                candidate,
                verification_status="failed",
                result_status="success",
                min_score=0.70,
            )
        )
        self.assertFalse(
            should_promote_candidate(
                candidate,
                verification_status="passed",
                result_status="partial",
                min_score=0.70,
            )
        )

    def test_promote_candidate_uses_backend_config_for_shareable_asset_metadata(self) -> None:
        candidate = {
            "candidate_id": "cand_demo_cloud_001",
            "candidate_type": "pattern",
            "workspace": "/tmp/demo",
            "title": "共享资产后端契约",
            "content": "资产 metadata 应随 backend 配置切换。",
            "scope": {"level": "workspace", "value": "general-coding-task"},
            "source_episode_ids": ["ep_demo_cloud_001"],
            "reusability_score": 0.8,
            "stability_score": 0.82,
            "confidence_score": 0.81,
            "constraint_value_score": 0.79,
        }

        with patch.dict(
            os.environ,
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
            },
            clear=True,
        ):
            asset = promote_candidate(candidate)

        self.assertEqual(asset["project_id"], "github:org/repo")
        self.assertEqual(asset["source_project"], "github:org/repo")
        self.assertEqual(asset["owning_team"], "agent-platform")
        self.assertEqual(asset["asset_storage"]["backend"], "object-storage")
        self.assertEqual(asset["asset_storage"]["uri"], "oss://bucket/expcap/assets")
        self.assertEqual(asset["state_index"]["backend"], "cloud-sql")
        self.assertEqual(asset["state_index"]["uri"], "postgres://expcap")
        self.assertEqual(asset["retrieval_index"]["backend"], "milvus")
        self.assertEqual(asset["retrieval_index"]["uri"], "https://milvus.example.com")
        self.assertEqual(asset["delivery"]["mode"], "shared")
        self.assertTrue(asset["delivery"]["shareable"])

    def test_activate_assets_exposes_match_evidence_and_risks(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            assets_dir = workspace / ".agent-memory" / "assets" / "patterns"
            candidates_dir = workspace / ".agent-memory" / "candidates"
            shared_assets_dir = workspace / ".codex-home" / "expcap-memory" / "assets" / "patterns"
            assets_dir.mkdir(parents=True, exist_ok=True)
            candidates_dir.mkdir(parents=True, exist_ok=True)
            shared_assets_dir.mkdir(parents=True, exist_ok=True)

            local_asset = assets_dir / "pattern_local_001.json"
            local_asset.write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_local_001",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "context",
                        "title": "项目内导入规范",
                        "content": "本项目统一从 src 包根路径导入，避免相对导入漂移。",
                        "scope": {"level": "task-family", "value": "python-import-error"},
                        "source_episode_ids": ["ep_local_001"],
                        "source_candidate_ids": ["cand_local_001"],
                        "confidence": 0.91,
                        "status": "active",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            shared_asset = shared_assets_dir / "pattern_shared_001.json"
            shared_asset.write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_shared_001",
                        "workspace": None,
                        "source_workspace": "/tmp/elsewhere",
                        "asset_type": "pattern",
                        "knowledge_scope": "cross-project",
                        "knowledge_kind": "pattern",
                        "title": "通用导入错误排查模式",
                        "content": "先检查真实包结构，再检查测试入口和解释器路径。",
                        "scope": {"level": "workspace", "value": "general-coding-task"},
                        "source_episode_ids": ["ep_shared_001"],
                        "source_candidate_ids": ["cand_shared_001"],
                        "confidence": 0.68,
                        "status": "candidate",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            previous_home = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = str(workspace / ".codex-home")
            try:
                activation = activate_assets(
                    task="fix pytest import error",
                    workspace=workspace,
                    constraints=["不要改 public API"],
                    assets_dir=workspace / ".agent-memory" / "assets",
                    candidates_dir=candidates_dir,
                    db_path=None,
                )
            finally:
                if previous_home is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = previous_home

            self.assertIn("match_evidence", activation["selected_assets"][0])
            self.assertTrue(activation["selected_assets"][0]["match_evidence"])
            self.assertIn("score_breakdown", activation["selected_assets"][0])
            self.assertIn("effectiveness_summary", activation["selected_assets"][0])
            self.assertIn("retrieval_sources", activation["selected_assets"][0])
            self.assertIn("source_provenance", activation["selected_assets"][0])
            self.assertIn("llm_use_guidance", activation["selected_assets"][0])
            self.assertEqual(activation["selected_assets"][0]["llm_use_guidance"]["decision_owner"], "llm")
            self.assertTrue(activation["selected_assets"][0]["source_provenance"]["data_source_confirmed"])
            self.assertIn("retrieval_summary", activation)
            self.assertEqual(activation["pipeline"]["kind"], "experience_rag_activation")
            self.assertEqual(activation["pipeline"]["stages"], ["retrieve", "rerank", "assemble"])
            self.assertTrue(any("最终是否采用由 LLM" in item for item in activation["why_selected"]))
            self.assertTrue(any("跨项目经验" in item for item in activation["selection_risks"]))

    def test_build_asset_effectiveness_summary_marks_needs_review_for_cold_assets(self) -> None:
        summary = build_asset_effectiveness_summary(
            {
                "activation_count": 5,
                "supported_count": 0,
                "supported_strong_count": 0,
                "supported_weak_count": 0,
                "weighted_support_score": 0.0,
                "support_ratio": 0.0,
            }
        )

        self.assertEqual(summary["temperature"], "cool")
        self.assertEqual(summary["review_status"], "needs_review")

    def test_apply_asset_effectiveness_persists_temperature_and_review_status(self) -> None:
        asset = {
            "asset_id": "pattern_demo_001",
            "asset_type": "pattern",
            "knowledge_scope": "project",
            "knowledge_kind": "pattern",
            "title": "demo",
            "content": "demo",
            "scope": {"level": "workspace", "value": "general-coding-task"},
            "confidence": 0.8,
            "status": "active",
            "updated_at": "2026-04-13T00:00:00+00:00",
        }

        updated = apply_asset_effectiveness(
            asset,
            {
                "activation_count": 3,
                "supported_count": 2,
                "supported_strong_count": 1,
                "supported_weak_count": 1,
                "weighted_support_score": 1.5,
                "support_ratio": 0.5,
            },
            updated_at="2026-04-17T00:00:00+00:00",
        )

        self.assertEqual(updated["temperature"], "warm")
        self.assertEqual(updated["review_status"], "healthy")
        self.assertEqual(updated["effectiveness_summary"]["supported_strong_count"], 1)
        self.assertEqual(updated["updated_at"], "2026-04-17T00:00:00+00:00")

    def test_apply_candidate_promotion_feedback_marks_readiness(self) -> None:
        candidate = {
            "candidate_id": "cand_demo_001",
            "candidate_type": "pattern",
            "title": "demo",
            "content": "demo",
        }

        updated = apply_candidate_promotion_feedback(
            candidate,
            activation_feedback={
                "activation_id": "act_demo_001",
                "help_signal": "supported_weak",
                "linked_asset_ids": ["pattern_demo_001"],
                "feedback_summary": "partial but helpful",
            },
        )

        self.assertEqual(updated["promotion_readiness"], "encouraging")
        self.assertEqual(updated["promotion_feedback"]["signal_bonus"], 0.02)

    def test_build_candidate_review_queue_prioritizes_needs_review_and_boosted_items(self) -> None:
        queue = build_candidate_review_queue(
            [
                {
                    "candidate_id": "cand_watch_001",
                    "candidate_type": "pattern",
                    "knowledge_kind": "pattern",
                    "title": "watch item",
                    "status": "new",
                    "promotion_readiness": "unknown",
                    "promotion_feedback": {"help_signal": None, "signal_bonus": 0.0},
                    "confidence_score": 0.7,
                    "reusability_score": 0.69,
                    "stability_score": 0.68,
                    "constraint_value_score": 0.67,
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "created_at": "2026-04-17T00:00:00+00:00",
                },
                {
                    "candidate_id": "cand_review_001",
                    "candidate_type": "pattern",
                    "knowledge_kind": "pattern",
                    "title": "review item",
                    "status": "needs_review",
                    "promotion_readiness": "boosted",
                    "promotion_feedback": {"help_signal": "supported_strong", "signal_bonus": 0.05},
                    "confidence_score": 0.68,
                    "reusability_score": 0.68,
                    "stability_score": 0.68,
                    "constraint_value_score": 0.68,
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "created_at": "2026-04-17T00:01:00+00:00",
                },
            ],
            workspace="/tmp/demo",
        )

        self.assertEqual(queue["items"][0]["candidate_id"], "cand_review_001")
        self.assertEqual(queue["items"][0]["suggested_action"], "promote")
        self.assertEqual(queue["status_summary"]["needs_review"], 1)

    def test_build_candidate_review_queue_marks_approved_items_for_promote(self) -> None:
        queue = build_candidate_review_queue(
            [
                {
                    "candidate_id": "cand_approved_001",
                    "candidate_type": "pattern",
                    "knowledge_kind": "pattern",
                    "title": "approved item",
                    "status": "approved",
                    "promotion_readiness": "encouraging",
                    "promotion_feedback": {"help_signal": "supported_weak", "signal_bonus": 0.02},
                    "confidence_score": 0.77,
                    "reusability_score": 0.78,
                    "stability_score": 0.79,
                    "constraint_value_score": 0.8,
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "created_at": "2026-04-17T00:02:00+00:00",
                }
            ],
            workspace="/tmp/demo",
        )

        self.assertEqual(queue["items"][0]["candidate_id"], "cand_approved_001")
        self.assertEqual(queue["items"][0]["suggested_action"], "promote")
        self.assertEqual(queue["status_summary"]["approved"], 1)

    def test_activate_assets_demotes_broad_scope_low_evidence_matches(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            assets_dir = workspace / ".agent-memory" / "assets" / "patterns"
            candidates_dir = workspace / ".agent-memory" / "candidates"
            shared_assets_dir = workspace / ".codex-home" / "expcap-memory" / "assets" / "patterns"
            assets_dir.mkdir(parents=True, exist_ok=True)
            candidates_dir.mkdir(parents=True, exist_ok=True)
            shared_assets_dir.mkdir(parents=True, exist_ok=True)

            broad_local_asset = assets_dir / "pattern_local_generic_001.json"
            broad_local_asset.write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_local_generic_001",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "context",
                        "title": "项目通用开发上下文",
                        "content": "遵循当前项目的一般开发规范与基础上下文。",
                        "scope": {"level": "workspace", "value": "general-coding-task"},
                        "source_episode_ids": ["ep_local_generic_001"],
                        "source_candidate_ids": ["cand_local_generic_001"],
                        "confidence": 0.95,
                        "status": "active",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            specific_shared_asset = shared_assets_dir / "pattern_shared_specific_001.json"
            specific_shared_asset.write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_shared_specific_001",
                        "workspace": None,
                        "source_workspace": "/tmp/elsewhere",
                        "asset_type": "pattern",
                        "knowledge_scope": "cross-project",
                        "knowledge_kind": "pattern",
                        "title": "python import repair pattern",
                        "content": "fix import errors by checking package roots and test entry paths first.",
                        "scope": {"level": "task-family", "value": "python-import-error"},
                        "source_episode_ids": ["ep_shared_specific_001"],
                        "source_candidate_ids": ["cand_shared_specific_001"],
                        "confidence": 0.76,
                        "status": "active",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            previous_home = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = str(workspace / ".codex-home")
            try:
                activation = activate_assets(
                    task="fix pytest import error",
                    workspace=workspace,
                    constraints=[],
                    assets_dir=workspace / ".agent-memory" / "assets",
                    candidates_dir=candidates_dir,
                    db_path=None,
                )
            finally:
                if previous_home is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = previous_home

            self.assertEqual(activation["selected_assets"][0]["asset_id"], "pattern_shared_specific_001")
            broad_item = next(item for item in activation["selected_assets"] if item["asset_id"] == "pattern_local_generic_001")
            self.assertGreater(broad_item["score_breakdown"]["penalty_score"], 0)
            self.assertTrue(any("作用域较宽" in item for item in broad_item["risk_flags"]))

    def test_explain_activation_view_mentions_top_evidence_and_risk(self) -> None:
        explained = explain_object(
            {
                "activation_id": "act_demo_001",
                "selected_assets": [
                    {
                        "asset_id": "pattern_demo_001",
                        "reason": "匹配分数 1.12，作用域值命中 task-family::python-import-error。",
                        "match_evidence": ["作用域值命中 task-family::python-import-error", "标题命中关键词：import"],
                        "historical_help": {
                            "activation_count": 3,
                            "supported_strong_count": 1,
                            "supported_weak_count": 1,
                        },
                    }
                ],
                "selection_risks": ["跨项目经验可能缺少当前项目上下文，使用时应核对适用边界。"],
                "feedback": {"help_signal": "supported_weak"},
                "fallback_episode_refs": ["ep_demo_001"],
            }
        )

        self.assertEqual(explained["kind"], "activation_view")
        self.assertTrue(any("pattern_demo_001" in item for item in explained["explanation"]))
        self.assertTrue(any("强帮助 1 次、弱帮助 1 次" in item for item in explained["explanation"]))
        self.assertTrue(any("supported_weak" in item for item in explained["explanation"]))
        self.assertTrue(any("跨项目经验可能缺少当前项目上下文" in item for item in explained["explanation"]))


if __name__ == "__main__":
    unittest.main()
