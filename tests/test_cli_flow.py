import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_candidate(
    path: Path,
    *,
    workspace: Path,
    candidate_id: str,
    status: str,
    promotion_readiness: str = "boosted",
    help_signal: str = "supported_strong",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "candidate_id": candidate_id,
                "source_episode_ids": ["ep_manual_review_001"],
                "workspace": str(workspace),
                "candidate_type": "pattern",
                "knowledge_kind": "pattern",
                "title": "manual review candidate",
                "content": "promote stable manual review experience into reusable guidance.",
                "reusability_score": 0.8,
                "stability_score": 0.79,
                "confidence_score": 0.81,
                "constraint_value_score": 0.78,
                "scope": {"level": "workspace", "value": "general-coding-task"},
                "promotion_feedback": {
                    "help_signal": help_signal,
                    "signal_bonus": 0.05 if help_signal == "supported_strong" else 0.02,
                    "activation_id": "act_manual_review_001",
                    "linked_asset_ids": ["pattern_manual_support_001"],
                    "feedback_summary": "manual review looked helpful",
                },
                "promotion_readiness": promotion_readiness,
                "status": status,
                "created_at": "2026-04-17T00:00:00+00:00",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class CliFlowTests(unittest.TestCase):
    def test_cli_ingest_review_extract_promote_activate_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            commands = [
                [
                    "ingest",
                    "--workspace", str(workspace),
                    "--task", "fix pytest import error",
                    "--user-request", "修复 pytest 导入错误，并确保测试通过。",
                    "--constraint", "不要改 public API",
                    "--command", "uv run pytest tests/test_imports.py",
                    "--error", "ModuleNotFoundError: no module named foo",
                    "--file-changed", "pkg/module.py",
                    "--file-changed", "tests/test_imports.py",
                    "--verification-status", "passed",
                    "--verification-summary", "1 passed",
                    "--result-status", "success",
                    "--result-summary", "修复导入路径并补充回归测试",
                    "--trace-id", "trace_20260413_001",
                ],
                ["review", "--input", str(workspace / ".agent-memory" / "traces" / "bundles" / "trace_20260413_001.json")],
                ["extract", "--episode", str(workspace / ".agent-memory" / "episodes" / "ep_20260413_001.json")],
                ["promote", "--candidate", str(workspace / ".agent-memory" / "candidates" / "cand_20260413_001.json")],
                ["activate", "--task", "fix pytest import error", "--workspace", str(workspace)],
            ]

            for command in commands:
                subprocess.run(
                    [sys.executable, "-m", "runtime.cli", *command],
                    cwd=REPO_ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                )

            trace_path = workspace / ".agent-memory" / "traces" / "bundles" / "trace_20260413_001.json"
            candidate_path = workspace / ".agent-memory" / "candidates" / "cand_20260413_001.json"
            asset_path = workspace / ".agent-memory" / "assets" / "patterns" / "pattern_20260413_001.json"
            activation_path = workspace / ".agent-memory" / "views" / "act_fix-pytest-import-error.json"
            db_path = workspace / ".agent-memory" / "index.sqlite3"

            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            asset = json.loads(asset_path.read_text(encoding="utf-8"))
            activation = json.loads(activation_path.read_text(encoding="utf-8"))

            self.assertEqual(trace["trace_id"], "trace_20260413_001")
            self.assertEqual(candidate["status"], "promoted")
            self.assertEqual(Path(asset["workspace"]).resolve(), workspace.resolve())
            self.assertEqual(activation["selected_assets"][0]["asset_id"], "pattern_20260413_001")
            self.assertTrue(activation["selected_assets"][0]["match_evidence"])
            self.assertIn("risk_flags", activation["selected_assets"][0])
            self.assertIn("score_breakdown", activation["selected_assets"][0])
            self.assertTrue(any("SQLite" in item for item in activation["why_selected"]))

            conn = sqlite3.connect(db_path)
            try:
                trace_count = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
                episode_count = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
                candidate_count = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
                asset_count = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
                activation_count = conn.execute("SELECT COUNT(*) FROM activation_logs").fetchone()[0]
                asset_last_used = conn.execute(
                    "SELECT last_used_at FROM assets WHERE asset_id = 'pattern_20260413_001'"
                ).fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(trace_count, 1)
            self.assertEqual(episode_count, 1)
            self.assertEqual(candidate_count, 1)
            self.assertEqual(asset_count, 1)
            self.assertEqual(activation_count, 1)
            self.assertIsNotNone(asset_last_used)

    def test_cli_auto_start_and_auto_finish_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            auto_finish = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-finish",
                    "--workspace",
                    str(workspace),
                    "--task",
                    "fix pytest import error",
                    "--user-request",
                    "修复 pytest 导入错误，并确保测试通过。",
                    "--constraint",
                    "不要改 public API",
                    "--command",
                    "uv run pytest tests/test_imports.py",
                    "--error",
                    "ModuleNotFoundError: no module named foo",
                    "--file-changed",
                    "pkg/module.py",
                    "--verification-status",
                    "passed",
                    "--verification-summary",
                    "1 passed",
                    "--result-status",
                    "success",
                    "--result-summary",
                    "修复导入路径并补充回归测试",
                    "--trace-id",
                    "trace_20260413_auto",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            auto_finish_payload = json.loads(auto_finish.stdout)

            auto_start = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-start",
                    "--task",
                    "fix pytest import error",
                    "--workspace",
                    str(workspace),
                    "--constraint",
                    "不要改 public API",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            auto_start_payload = json.loads(auto_start.stdout)

            candidate_path = workspace / ".agent-memory" / "candidates" / "cand_20260413_auto.json"
            asset_path = workspace / ".agent-memory" / "assets" / "patterns" / "pattern_20260413_auto.json"
            activation_path = workspace / ".agent-memory" / "views" / "act_fix-pytest-import-error.json"

            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            asset = json.loads(asset_path.read_text(encoding="utf-8"))
            activation = json.loads(activation_path.read_text(encoding="utf-8"))

            self.assertEqual(auto_finish_payload["trace"]["trace_id"], "trace_20260413_auto")
            self.assertEqual(candidate["status"], "promoted")
            self.assertEqual(auto_finish_payload["promoted_assets"][0]["asset_id"], "pattern_20260413_auto")
            self.assertEqual(asset["asset_id"], "pattern_20260413_auto")
            self.assertEqual(auto_start_payload["selected_count"], 1)
            self.assertEqual(activation["selected_assets"][0]["asset_id"], "pattern_20260413_auto")
            self.assertTrue(any("当前约束" in item for item in activation["rendered_context"]))

    def test_cli_auto_finish_can_promote_cross_project_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            codex_home = Path(tmpdir) / "codex-home"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-finish",
                    "--workspace",
                    str(workspace),
                    "--task",
                    "stabilize API contract checks",
                    "--constraint",
                    "不要破坏现有 API 契约",
                    "--command",
                    "uv run pytest tests/test_api_contract.py",
                    "--verification-status",
                    "passed",
                    "--verification-summary",
                    "3 passed",
                    "--result-status",
                    "success",
                    "--result-summary",
                    "补充接口契约校验并固定回归路径",
                    "--trace-id",
                    "trace_cross_project_001",
                    "--knowledge-scope",
                    "cross-project",
                    "--knowledge-kind",
                    "rule",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
                env={**dict(os.environ), "CODEX_HOME": str(codex_home)},
            )

            payload = json.loads(completed.stdout)
            asset_path = codex_home / "expcap-memory" / "assets" / "patterns" / "pattern_cross_project_001.json"
            asset = json.loads(asset_path.read_text(encoding="utf-8"))

            self.assertEqual(payload["promoted_assets"][0]["asset_id"], "pattern_cross_project_001")
            self.assertEqual(asset["knowledge_scope"], "cross-project")
            self.assertEqual(asset["knowledge_kind"], "rule")

    def test_cli_auto_finish_records_activation_help_feedback_for_later_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            asset_path = workspace / ".agent-memory" / "assets" / "patterns" / "pattern_local_001.json"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_local_001",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "pattern",
                        "title": "python import repair pattern",
                        "content": "fix import errors by checking package roots and test entry paths first.",
                        "scope": {"level": "task-family", "value": "python-import-error"},
                        "source_episode_ids": ["ep_local_001"],
                        "source_candidate_ids": ["cand_local_001"],
                        "confidence": 0.88,
                        "status": "active",
                        "last_used_at": None,
                        "created_at": "2026-04-13T00:00:00+00:00",
                        "updated_at": "2026-04-13T00:00:00+00:00",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-start",
                    "--task",
                    "fix pytest import error",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            auto_finish = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-finish",
                    "--workspace",
                    str(workspace),
                    "--task",
                    "fix pytest import error",
                    "--verification-status",
                    "passed",
                    "--verification-summary",
                    "1 passed",
                    "--result-status",
                    "success",
                    "--result-summary",
                    "修复导入路径并验证通过",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            finish_payload = json.loads(auto_finish.stdout)
            self.assertEqual(finish_payload["activation_feedback"]["help_signal"], "supported_strong")
            self.assertIn("pattern_local_001", finish_payload["activation_feedback"]["linked_asset_ids"])

            follow_up = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-start",
                    "--task",
                    "fix pytest import error",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            follow_up_payload = json.loads(follow_up.stdout)
            selected_assets = follow_up_payload["activation_view"]["selected_assets"]
            updated_original_asset = json.loads(asset_path.read_text(encoding="utf-8"))

            self.assertTrue(selected_assets[0]["asset_id"].startswith("pattern_"))
            self.assertEqual(updated_original_asset["historical_help"]["supported_count"], 1)
            self.assertEqual(updated_original_asset["historical_help"]["supported_strong_count"], 1)
            self.assertEqual(updated_original_asset["historical_help"]["supported_weak_count"], 0)
            self.assertEqual(updated_original_asset["historical_help"]["activation_count"], 1)
            self.assertEqual(updated_original_asset["temperature"], "warm")

    def test_cli_auto_finish_can_record_weak_help_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            asset_path = workspace / ".agent-memory" / "assets" / "patterns" / "pattern_local_weak_001.json"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_local_weak_001",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "pattern",
                        "title": "pytest import stabilization pattern",
                        "content": "stabilize import-related tests with minimal path verification first.",
                        "scope": {"level": "task-family", "value": "python-import-error"},
                        "source_episode_ids": ["ep_local_weak_001"],
                        "source_candidate_ids": ["cand_local_weak_001"],
                        "confidence": 0.84,
                        "status": "active",
                        "last_used_at": None,
                        "created_at": "2026-04-13T00:00:00+00:00",
                        "updated_at": "2026-04-13T00:00:00+00:00",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-start",
                    "--task",
                    "fix pytest import error",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            auto_finish = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-finish",
                    "--workspace",
                    str(workspace),
                    "--task",
                    "fix pytest import error",
                    "--verification-status",
                    "passed",
                    "--verification-summary",
                    "1 passed",
                    "--result-status",
                    "partial",
                    "--result-summary",
                    "定位清楚并保留后续清理项",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            finish_payload = json.loads(auto_finish.stdout)
            self.assertEqual(finish_payload["activation_feedback"]["help_signal"], "supported_weak")

            follow_up = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-start",
                    "--task",
                    "fix pytest import error",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            follow_up_payload = json.loads(follow_up.stdout)
            top_asset = follow_up_payload["activation_view"]["selected_assets"][0]

            self.assertEqual(top_asset["historical_help"]["supported_strong_count"], 0)
            self.assertEqual(top_asset["historical_help"]["supported_weak_count"], 1)
            self.assertEqual(top_asset["historical_help"]["support_ratio"], 0.5)

    def test_cli_auto_finish_marks_borderline_candidate_for_review_when_helpful(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            asset_path = workspace / ".agent-memory" / "assets" / "patterns" / "pattern_local_review_001.json"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_local_review_001",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "pattern",
                        "title": "general coding support pattern",
                        "content": "help organize fixes and keep verification focused.",
                        "scope": {"level": "workspace", "value": "general-coding-task"},
                        "source_episode_ids": ["ep_local_review_001"],
                        "source_candidate_ids": ["cand_local_review_001"],
                        "confidence": 0.82,
                        "status": "active",
                        "last_used_at": None,
                        "created_at": "2026-04-13T00:00:00+00:00",
                        "updated_at": "2026-04-13T00:00:00+00:00",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-start",
                    "--task",
                    "stabilize API contract checks",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            auto_finish = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-finish",
                    "--workspace",
                    str(workspace),
                    "--task",
                    "stabilize API contract checks",
                    "--verification-status",
                    "passed",
                    "--verification-summary",
                    "2 passed",
                    "--result-status",
                    "partial",
                    "--result-summary",
                    "主要路径已稳定，但仍需后续清理",
                    "--no-promote",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(auto_finish.stdout)
            self.assertEqual(payload["activation_feedback"]["help_signal"], "supported_weak")

            candidate_path = Path(payload["candidates"][0]["path"])
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            self.assertEqual(candidate["status"], "needs_review")
            self.assertEqual(candidate["promotion_readiness"], "encouraging")
            self.assertEqual(candidate["promotion_feedback"]["help_signal"], "supported_weak")

    def test_cli_review_candidates_builds_review_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            asset_path = workspace / ".agent-memory" / "assets" / "patterns" / "pattern_local_queue_001.json"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_local_queue_001",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "pattern",
                        "title": "queue support pattern",
                        "content": "support review queue triage with stable guidance.",
                        "scope": {"level": "workspace", "value": "general-coding-task"},
                        "source_episode_ids": ["ep_local_queue_001"],
                        "source_candidate_ids": ["cand_local_queue_001"],
                        "confidence": 0.82,
                        "status": "active",
                        "last_used_at": None,
                        "created_at": "2026-04-13T00:00:00+00:00",
                        "updated_at": "2026-04-13T00:00:00+00:00",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-start",
                    "--task",
                    "stabilize API contract checks",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-finish",
                    "--workspace",
                    str(workspace),
                    "--task",
                    "stabilize API contract checks",
                    "--verification-status",
                    "passed",
                    "--verification-summary",
                    "2 passed",
                    "--result-status",
                    "partial",
                    "--result-summary",
                    "主要路径已稳定，但仍需后续清理",
                    "--no-promote",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "review-candidates",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)

            self.assertEqual(payload["review_queue"]["kind"], "candidate_review_queue")
            self.assertGreaterEqual(payload["candidate_count"], 1)
            self.assertEqual(payload["review_queue"]["items"][0]["status"], "needs_review")
            self.assertIn(payload["review_queue"]["items"][0]["suggested_action"], {"review", "promote"})

    def test_cli_review_candidates_can_approve_then_promote_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            candidate_path = workspace / ".agent-memory" / "candidates" / "cand_manual_review_001.json"
            _write_candidate(
                candidate_path,
                workspace=workspace,
                candidate_id="cand_manual_review_001",
                status="needs_review",
            )

            approved = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "review-candidates",
                    "--workspace",
                    str(workspace),
                    "--action",
                    "approve",
                    "--candidate-id",
                    "cand_manual_review_001",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            approved_payload = json.loads(approved.stdout)
            approved_candidate = json.loads(candidate_path.read_text(encoding="utf-8"))

            self.assertEqual(approved_payload["action_result"]["status"], "approved")
            self.assertEqual(approved_candidate["status"], "approved")
            self.assertEqual(approved_candidate["review_history"][0]["action"], "approve")
            self.assertEqual(approved_payload["review_queue"]["items"][0]["suggested_action"], "promote")

            promoted = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "review-candidates",
                    "--workspace",
                    str(workspace),
                    "--action",
                    "promote",
                    "--candidate-id",
                    "cand_manual_review_001",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            promoted_payload = json.loads(promoted.stdout)
            promoted_candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            asset_path = workspace / ".agent-memory" / "assets" / "patterns" / "pattern_manual_review_001.json"
            asset = json.loads(asset_path.read_text(encoding="utf-8"))

            self.assertEqual(promoted_payload["action_result"]["status"], "promoted")
            self.assertEqual(promoted_payload["action_result"]["asset"]["asset_id"], "pattern_manual_review_001")
            self.assertEqual(promoted_candidate["status"], "promoted")
            self.assertEqual(len(promoted_candidate["review_history"]), 2)
            self.assertEqual(promoted_candidate["review_history"][1]["action"], "promote")
            self.assertEqual(asset["asset_id"], "pattern_manual_review_001")

    def test_cli_review_candidates_can_reject_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            candidate_path = workspace / ".agent-memory" / "candidates" / "cand_manual_review_reject_001.json"
            _write_candidate(
                candidate_path,
                workspace=workspace,
                candidate_id="cand_manual_review_reject_001",
                status="needs_review",
                promotion_readiness="encouraging",
                help_signal="supported_weak",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "review-candidates",
                    "--workspace",
                    str(workspace),
                    "--action",
                    "reject",
                    "--candidate-id",
                    "cand_manual_review_reject_001",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            rejected_candidate = json.loads(candidate_path.read_text(encoding="utf-8"))

            self.assertEqual(payload["action_result"]["status"], "rejected")
            self.assertEqual(rejected_candidate["status"], "rejected")
            self.assertEqual(rejected_candidate["review_history"][0]["action"], "reject")
            self.assertEqual(payload["candidate_count"], 0)
            self.assertEqual(payload["review_queue"]["items"], [])

    def test_cli_status_summarizes_short_test_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            asset_path = workspace / ".agent-memory" / "assets" / "patterns" / "pattern_status_001.json"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_status_001",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "pattern",
                        "title": "status support pattern",
                        "content": "support short test review with explicit signals.",
                        "scope": {"level": "workspace", "value": "general-coding-task"},
                        "source_episode_ids": ["ep_status_001"],
                        "source_candidate_ids": ["cand_status_001"],
                        "confidence": 0.84,
                        "status": "active",
                        "last_used_at": None,
                        "created_at": "2026-04-13T00:00:00+00:00",
                        "updated_at": "2026-04-13T00:00:00+00:00",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-start",
                    "--task",
                    "stabilize API contract checks",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-finish",
                    "--workspace",
                    str(workspace),
                    "--task",
                    "stabilize API contract checks",
                    "--verification-status",
                    "passed",
                    "--verification-summary",
                    "2 passed",
                    "--result-status",
                    "partial",
                    "--result-summary",
                    "主要路径已稳定，但仍需后续清理",
                    "--no-promote",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "status",
                    "--workspace",
                    str(workspace),
                    "--limit",
                    "3",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)["status"]

            self.assertEqual(payload["counts"]["activation_logs"], 1)
            self.assertEqual(payload["activation_feedback_summary"]["supported_weak"], 1)
            self.assertGreaterEqual(payload["counts"]["candidates"], 1)
            self.assertEqual(payload["candidate_status_summary"]["needs_review"], 1)
            self.assertGreaterEqual(payload["candidate_review_queue"]["candidate_count"], 1)
            self.assertEqual(payload["candidate_review_queue"]["top_items"][0]["status"], "needs_review")
            self.assertEqual(payload["recent_activations"][0]["help_signal"], "supported_weak")
            self.assertEqual(payload["retrieval_backends"]["sqlite"]["backend"], "sqlite")
            self.assertTrue(payload["retrieval_backends"]["sqlite"]["db_exists"])
            self.assertEqual(payload["retrieval_backends"]["milvus"]["local"]["backend"], "milvus-lite")
            self.assertIn("available", payload["retrieval_backends"]["milvus"])
            self.assertFalse(payload["retrieval_backends"]["milvus"]["local"]["deep_check"])
            self.assertIn("collection_exists", payload["retrieval_backends"]["milvus"]["local"])
            self.assertEqual(payload["backend_configuration"]["profile"], "local-first")
            self.assertEqual(payload["backend_configuration"]["source_of_truth"], "local-json")
            self.assertEqual(payload["backend_configuration"]["state_index"], "sqlite")
            self.assertEqual(payload["backend_configuration"]["retrieval"], "milvus-lite")

    def test_cli_auto_finish_persists_asset_effectiveness_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            asset_path = workspace / ".agent-memory" / "assets" / "patterns" / "pattern_local_effective_001.json"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_local_effective_001",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "pattern",
                        "title": "pytest import repair playbook",
                        "content": "repair import-related test failures with package-root verification first.",
                        "scope": {"level": "task-family", "value": "python-import-error"},
                        "source_episode_ids": ["ep_local_effective_001"],
                        "source_candidate_ids": ["cand_local_effective_001"],
                        "confidence": 0.86,
                        "status": "active",
                        "last_used_at": None,
                        "created_at": "2026-04-13T00:00:00+00:00",
                        "updated_at": "2026-04-13T00:00:00+00:00",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-start",
                    "--task",
                    "fix pytest import error",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-finish",
                    "--workspace",
                    str(workspace),
                    "--task",
                    "fix pytest import error",
                    "--verification-status",
                    "passed",
                    "--verification-summary",
                    "1 passed",
                    "--result-status",
                    "success",
                    "--result-summary",
                    "修复完成并验证通过",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            updated_asset = json.loads(asset_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_asset["temperature"], "warm")
            self.assertEqual(updated_asset["review_status"], "healthy")
            self.assertEqual(updated_asset["effectiveness_summary"]["supported_strong_count"], 1)
            self.assertEqual(updated_asset["historical_help"]["supported_count"], 1)

    def test_cli_auto_start_prioritizes_project_asset_before_shared_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            codex_home = Path(tmpdir) / "codex-home"

            local_asset_path = workspace / ".agent-memory" / "assets" / "patterns" / "pattern_local_001.json"
            local_asset_path.parent.mkdir(parents=True, exist_ok=True)
            local_asset_path.write_text(
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
                        "last_used_at": None,
                        "created_at": "2026-04-13T00:00:00+00:00",
                        "updated_at": "2026-04-13T00:00:00+00:00",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            shared_asset_path = codex_home / "expcap-memory" / "assets" / "patterns" / "pattern_shared_001.json"
            shared_asset_path.parent.mkdir(parents=True, exist_ok=True)
            shared_asset_path.write_text(
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
                        "scope": {"level": "task-family", "value": "python-import-error"},
                        "source_episode_ids": ["ep_shared_001"],
                        "source_candidate_ids": ["cand_shared_001"],
                        "confidence": 0.95,
                        "status": "active",
                        "last_used_at": None,
                        "created_at": "2026-04-13T00:00:00+00:00",
                        "updated_at": "2026-04-13T00:00:00+00:00",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-start",
                    "--task",
                    "fix pytest import error",
                    "--workspace",
                    str(workspace),
                    "--constraint",
                    "不要改 public API",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
                env={**dict(os.environ), "CODEX_HOME": str(codex_home)},
            )

            payload = json.loads(completed.stdout)
            self.assertEqual(payload["activation_view"]["selected_assets"][0]["asset_id"], "pattern_local_001")
            self.assertEqual(payload["activation_view"]["selected_assets"][1]["asset_id"], "pattern_shared_001")
            self.assertTrue(payload["activation_view"]["selected_assets"][0]["knowledge_scope"] == "project")
            self.assertTrue(
                any("显式降权" in item for item in payload["activation_view"]["why_selected"])
            )
            self.assertTrue(
                any(
                    "跨项目经验" in item
                    for item in payload["activation_view"]["selected_assets"][1]["risk_flags"]
                )
            )


if __name__ == "__main__":
    unittest.main()
