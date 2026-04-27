import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.cli import main as cli_main
from runtime.storage.fs_store import default_db_path
from runtime.storage.sqlite_store import upsert_asset
from runtime.storage.sqlite_store import ensure_db, list_activation_logs, log_activation


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
            workspace = (Path(tmpdir) / "workspace").resolve()
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
            self.assertIn("retrieval_sources", activation["selected_assets"][0])
            self.assertIn("source_provenance", activation["selected_assets"][0])
            self.assertIn("llm_use_guidance", activation["selected_assets"][0])
            self.assertEqual(activation["selected_assets"][0]["llm_use_guidance"]["decision_owner"], "llm")
            self.assertIn("retrieval_summary", activation)
            self.assertEqual(activation["pipeline"]["kind"], "experience_rag_activation")
            self.assertEqual(activation["pipeline"]["stages"], ["retrieve", "rerank", "assemble"])
            self.assertTrue(any("SQLite" in item for item in activation["why_selected"]))
            self.assertTrue(any("最终是否采用由 LLM" in item for item in activation["why_selected"]))

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

    def test_cli_auto_start_falls_back_when_default_activation_view_path_is_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            captured: dict[str, object] = {}
            view = {
                "activation_id": "act_review-fallback",
                "task_query": "review fallback handling",
                "workspace": str(workspace),
                "selected_assets": [],
                "selected_asset_ids": [],
                "retrieval_summary": {},
                "created_at": "2026-04-27T00:00:00+00:00",
            }
            default_path = cli_main.default_activation_view_path(workspace, view)
            original_save_json = cli_main.save_json

            def flaky_save_json(path: Path, payload: dict[str, object]) -> None:
                if Path(path) == default_path:
                    raise PermissionError("permission denied for default activation path")
                original_save_json(path, payload)

            args = argparse.Namespace(
                workspace=str(workspace),
                task="review fallback handling",
                constraints=[],
                output=None,
            )

            with patch.object(cli_main, "activate_assets", return_value=view), patch.object(
                cli_main,
                "save_json",
                side_effect=flaky_save_json,
            ), patch.object(cli_main, "_print_json", side_effect=lambda payload: captured.update(payload)):
                result = cli_main._handle_auto_start(args)

            self.assertEqual(result, 0)
            self.assertEqual(captured["activation_id"], "act_review-fallback")
            self.assertIn("save_warning", captured)
            warning = captured["save_warning"]
            assert isinstance(warning, dict)
            self.assertEqual(warning["reason"], "default_activation_view_unwritable")
            self.assertEqual(warning["requested_path"], str(default_path))
            fallback_path = Path(captured["saved_to"])
            self.assertTrue(fallback_path.exists())
            self.assertEqual(json.loads(fallback_path.read_text(encoding="utf-8"))["activation_id"], "act_review-fallback")
            self.assertEqual(len(list_activation_logs(default_db_path(workspace), workspace=str(workspace))), 1)

    def test_cli_activate_uses_memory_root_source_dirs_in_user_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            captured: dict[str, object] = {}

            def fake_activate_assets(**kwargs: object) -> dict[str, object]:
                captured["assets_dir"] = kwargs["assets_dir"]
                captured["candidates_dir"] = kwargs["candidates_dir"]
                return {
                    "activation_id": "act_memory-root-check",
                    "task_query": "inspect memory root source dirs",
                    "workspace": str(workspace),
                    "selected_assets": [],
                    "selected_asset_ids": [],
                    "retrieval_summary": {},
                    "created_at": "2026-04-27T00:00:00+00:00",
                }

            args = argparse.Namespace(
                workspace=str(workspace),
                task="inspect memory root source dirs",
                constraints=[],
                assets_dir=None,
                candidates_dir=None,
                output=None,
            )

            with patch.dict(os.environ, {"EXPCAP_STORAGE_PROFILE": "user-cache"}), patch.object(
                cli_main,
                "activate_assets",
                side_effect=fake_activate_assets,
            ), patch.object(cli_main, "_print_json", side_effect=lambda payload: None):
                expected_memory_root = cli_main.memory_root_for_workspace(workspace)
                result = cli_main._handle_activate(args)

            self.assertEqual(result, 0)
            self.assertEqual(captured["assets_dir"], expected_memory_root / "assets")
            self.assertEqual(captured["candidates_dir"], expected_memory_root / "candidates")

    def test_cli_auto_start_uses_memory_root_source_dirs_in_user_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            captured: dict[str, object] = {}

            def fake_activate_assets(**kwargs: object) -> dict[str, object]:
                captured["assets_dir"] = kwargs["assets_dir"]
                captured["candidates_dir"] = kwargs["candidates_dir"]
                return {
                    "activation_id": "act_auto-start-memory-root-check",
                    "task_query": "inspect auto-start memory root source dirs",
                    "workspace": str(workspace),
                    "selected_assets": [],
                    "selected_asset_ids": [],
                    "retrieval_summary": {},
                    "created_at": "2026-04-27T00:00:00+00:00",
                }

            args = argparse.Namespace(
                workspace=str(workspace),
                task="inspect auto-start memory root source dirs",
                constraints=[],
                output=None,
            )

            with patch.dict(os.environ, {"EXPCAP_STORAGE_PROFILE": "user-cache"}), patch.object(
                cli_main,
                "activate_assets",
                side_effect=fake_activate_assets,
            ), patch.object(cli_main, "_print_json", side_effect=lambda payload: None):
                expected_memory_root = cli_main.memory_root_for_workspace(workspace)
                result = cli_main._handle_auto_start(args)

            self.assertEqual(result, 0)
            self.assertEqual(captured["assets_dir"], expected_memory_root / "assets")
            self.assertEqual(captured["candidates_dir"], expected_memory_root / "candidates")

    def test_cli_progressive_recall_skips_without_new_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            env = {**os.environ, "EXPCAP_STORAGE_PROFILE": "local"}

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-start",
                    "--workspace",
                    str(workspace),
                    "--task",
                    "inspect dashboard quality",
                ],
                cwd=REPO_ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "progressive-recall",
                    "--workspace",
                    str(workspace),
                    "--task",
                    "inspect dashboard quality",
                ],
                cwd=REPO_ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            payload = json.loads(completed.stdout)
            self.assertFalse(payload["triggered"])
            self.assertIn(payload["decision"]["skip_reason"], {"cooldown_active", "no_new_signal"})
            self.assertEqual(payload["selected_count"], 0)

    def test_cli_progressive_recall_triggers_delta_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            env = {**os.environ, "EXPCAP_STORAGE_PROFILE": "local"}

            with patch.dict(os.environ, {"EXPCAP_STORAGE_PROFILE": "local"}):
                db_path = default_db_path(workspace)
                ensure_db(db_path)
                upsert_asset(
                    db_path,
                    {
                        "asset_id": "pattern_progressive_001",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "pattern",
                        "title": "progressive recall websocket repair pattern",
                        "content": "when websocket failures appear mid-conversation, run a delta recall focused on the new error signal.",
                        "scope": {"level": "workspace", "value": "general-coding-task"},
                        "source_episode_ids": ["ep_progressive_001"],
                        "source_candidate_ids": ["cand_progressive_001"],
                        "confidence": 0.84,
                        "status": "active",
                        "review_status": "healthy",
                        "temperature": "warm",
                        "created_at": "2026-04-26T00:00:00+00:00",
                        "updated_at": "2026-04-26T00:00:00+00:00",
                    },
                )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "progressive-recall",
                    "--workspace",
                    str(workspace),
                    "--task",
                    "continue debugging chat transport",
                    "--message",
                    "new websocket timeout appears after switching to streaming",
                    "--error",
                    "WebSocketTimeoutError while reading stream",
                    "--phase",
                    "fix",
                    "--cooldown-minutes",
                    "10",
                ],
                cwd=REPO_ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            payload = json.loads(completed.stdout)
            view_path = Path(payload["saved_to"])
            view = json.loads(view_path.read_text(encoding="utf-8"))
            db_path = default_db_path(workspace)
            activations = list_activation_logs(db_path, workspace=str(workspace))

            self.assertTrue(payload["triggered"])
            self.assertIn("new_error_signal", payload["decision"]["reasons"])
            self.assertEqual(payload["selected_count"], 1)
            self.assertEqual(view["selected_assets"][0]["asset_id"], "pattern_progressive_001")
            self.assertEqual(view["retrieval_summary"]["selected_from_sqlite"], 1)
            self.assertEqual(view["progressive_recall"]["kind"], "event_driven_delta")
            self.assertEqual(activations[0]["progressive_recall"]["phase"], "fix")

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

    def test_cli_status_treats_recent_unresolved_activation_as_pending_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-start",
                    "--workspace",
                    str(workspace),
                    "--task",
                    "inspect current logging quality",
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
                env={**os.environ, "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )

            payload = json.loads(completed.stdout)["status"]
            self.assertEqual(payload["activation_feedback_summary"]["missing_total"], 1)
            self.assertEqual(payload["activation_feedback_summary"]["pending"], 1)
            self.assertEqual(payload["activation_feedback_summary"]["missing"], 0)
            self.assertEqual(payload["activation_feedback_summary"]["pending_hours"], 24.0)
            self.assertEqual(payload["feedback_cleanup"]["auto_resolved_count"], 0)
            self.assertEqual(payload["unresolved_activations"][0]["state"], "pending")
            self.assertEqual(
                payload["unresolved_activations"][0]["task_query"],
                "inspect current logging quality",
            )

    def test_cli_status_auto_resolves_stale_unresolved_activation_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            asset_path = workspace / ".agent-memory" / "assets" / "patterns" / "pattern_stale_001.json"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_stale_001",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "pattern",
                        "title": "stale feedback support pattern",
                        "content": "support explicit stale feedback cleanup testing.",
                        "scope": {"level": "workspace", "value": "general-coding-task"},
                        "source_episode_ids": ["ep_stale_001"],
                        "source_candidate_ids": ["cand_stale_001"],
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

            started = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-start",
                    "--workspace",
                    str(workspace),
                    "--task",
                    "inspect stale feedback cleanup",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            activation_id = json.loads(started.stdout)["activation_id"]
            db_path = workspace / ".agent-memory" / "index.sqlite3"

            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT payload_json FROM activation_logs WHERE activation_id = ?",
                    (activation_id,),
                ).fetchone()
                payload = json.loads(row[0])
                payload["created_at"] = "2026-04-10T00:00:00+00:00"
                conn.execute(
                    "UPDATE activation_logs SET created_at = ?, payload_json = ? WHERE activation_id = ?",
                    (
                        payload["created_at"],
                        json.dumps(payload, ensure_ascii=False),
                        activation_id,
                    ),
                )

            activation_path = workspace / ".agent-memory" / "views" / f"{activation_id}.json"
            activation_view = json.loads(activation_path.read_text(encoding="utf-8"))
            activation_view["created_at"] = "2026-04-10T00:00:00+00:00"
            activation_path.write_text(
                json.dumps(activation_view, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
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
            self.assertEqual(payload["feedback_cleanup"]["auto_resolved_count"], 1)
            self.assertEqual(payload["activation_feedback_summary"]["unclear"], 1)
            self.assertEqual(payload["activation_feedback_summary"]["missing_total"], 0)
            self.assertEqual(payload["activation_feedback_summary"]["pending"], 0)
            self.assertEqual(payload["recent_activations"][0]["help_signal"], "unclear")
            self.assertEqual(payload["unresolved_activations"], [])

    def test_cli_doctor_reports_workspace_health_and_recommendations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-start",
                    "--workspace",
                    str(workspace),
                    "--task",
                    "inspect current logging quality",
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
                    "doctor",
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

            payload = json.loads(completed.stdout)
            doctor = payload["doctor"]
            check_names = {item["name"] for item in doctor["checks"]}
            self.assertEqual(
                Path(payload["saved_to"]).resolve(),
                (workspace / ".agent-memory" / "reviews" / "doctor.json").resolve(),
            )
            self.assertIn(doctor["overall_status"], {"pass", "warn", "fail"})
            self.assertIn("sqlite_index", check_names)
            self.assertIn("activation_feedback", check_names)
            self.assertIn("asset_proof_coverage", check_names)
            self.assertIn("local_milvus", check_names)
            self.assertIn("milvus_locks", doctor)
            self.assertEqual(doctor["status"]["activation_feedback_summary"]["pending"], 1)
            self.assertEqual(doctor["status"]["unresolved_activations"][0]["state"], "pending")
            self.assertIn("milvus_retrieval_effectiveness", doctor["status"])
            self.assertIn("project_activity", doctor["status"])

    def test_cli_dashboard_generates_local_html_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)

            with patch.dict(os.environ, {"EXPCAP_STORAGE_PROFILE": "local"}):
                db_path = default_db_path(workspace)
                ensure_db(db_path)
                upsert_asset(
                    db_path,
                    {
                        "asset_id": "pattern_dashboard_001",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "pattern",
                        "title": "dashboard support pattern",
                        "content": "use a local dashboard to review asset quality and retrieval effectiveness.",
                        "scope": {"level": "workspace", "value": "dashboard-test"},
                        "source_episode_ids": ["ep_dashboard_001"],
                        "source_candidate_ids": ["cand_dashboard_001"],
                        "confidence": 0.86,
                        "status": "active",
                        "review_status": "healthy",
                        "temperature": "warm",
                        "created_at": "2026-04-26T00:00:00+00:00",
                        "updated_at": "2026-04-26T00:00:00+00:00",
                    },
                )
                log_activation(
                    db_path,
                    {
                        "activation_id": "act_dashboard_001",
                        "workspace": str(workspace),
                        "task_query": "inspect dashboard effectiveness",
                        "selected_asset_ids": ["pattern_dashboard_001"],
                        "selected_assets": [
                            {
                                "asset_id": "pattern_dashboard_001",
                                "title": "dashboard support pattern",
                                "retrieval_sources": ["milvus", "sqlite"],
                                "vector_score": 0.82,
                            }
                        ],
                        "retrieval_summary": {
                            "milvus_project_candidates": 1,
                            "milvus_shared_candidates": 0,
                            "selected_from_milvus": 1,
                        },
                        "feedback": {"help_signal": "supported_strong"},
                        "created_at": "2026-04-26T00:30:00+00:00",
                    },
                )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "dashboard",
                    "--workspace",
                    str(workspace),
                    "--limit",
                    "10",
                    "--days",
                    "3",
                ],
                cwd=REPO_ROOT,
                env={**os.environ, "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )

            payload = json.loads(completed.stdout)
            html_path = Path(payload["saved_to"])
            json_path = Path(payload["data_saved_to"])
            html = html_path.read_text(encoding="utf-8")
            dashboard = json.loads(json_path.read_text(encoding="utf-8"))

            self.assertTrue(html_path.exists())
            self.assertTrue(json_path.exists())
            self.assertIn("dashboard support pattern", html)
            self.assertIn("Retrieval Effectiveness", html)
            self.assertIn("Write Frequency", html)
            self.assertIn("Effectiveness Snapshot", html)
            self.assertIn("Unproven Validation Queue", html)
            self.assertEqual(payload["dashboard"]["cards"]["assets"], 1)
            self.assertIn("effectiveness_snapshot", payload["dashboard"])
            self.assertEqual(payload["dashboard"]["unproven_validation_count"], 0)
            self.assertEqual(dashboard["cards"]["healthy_assets"], 1)
            self.assertEqual(dashboard["effectiveness_snapshot"]["verdict"], "healthy")
            self.assertEqual(dashboard["retrieval"]["effectiveness"]["selected_from_milvus"], 1)
            self.assertEqual(dashboard["activations"][0]["help_signal"], "supported_strong")
            self.assertEqual(dashboard["unproven_validation_queue"]["asset_count"], 0)

    def test_cli_doctor_reports_unproven_assets_without_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)

            db_path = workspace / ".agent-memory" / "index.sqlite3"
            for index in range(10):
                upsert_asset(
                    db_path,
                    {
                        "asset_id": f"pattern_unproven_{index:03d}",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "pattern",
                        "title": f"unproven pattern {index}",
                        "content": "newly promoted asset without enough usage feedback yet.",
                        "scope": {"level": "workspace", "value": "general-coding-task"},
                        "source_episode_ids": [f"ep_unproven_{index:03d}"],
                        "source_candidate_ids": [f"cand_unproven_{index:03d}"],
                        "confidence": 0.7,
                        "status": "active",
                        "review_status": "unproven",
                        "temperature": "neutral",
                        "created_at": "2026-04-13T00:00:00+00:00",
                        "updated_at": "2026-04-13T00:00:00+00:00",
                    },
                )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "doctor",
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

            doctor = json.loads(completed.stdout)["doctor"]
            proof_check = next(
                item for item in doctor["checks"] if item["name"] == "asset_proof_coverage"
            )
            self.assertEqual(proof_check["status"], "pass")
            self.assertIn("10 unproven", proof_check["summary"])
            self.assertNotIn("recommendation", proof_check)

    def test_cli_status_builds_unproven_validation_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)

            db_path = workspace / ".agent-memory" / "index.sqlite3"
            upsert_asset(
                db_path,
                {
                    "asset_id": "pattern_unproven_priority",
                    "workspace": str(workspace),
                    "asset_type": "pattern",
                    "knowledge_scope": "project",
                    "knowledge_kind": "pattern",
                    "title": "priority unproven pattern",
                    "content": "needs first validation run",
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "confidence": 0.92,
                    "status": "active",
                    "review_status": "unproven",
                    "temperature": "neutral",
                    "created_at": "2026-04-26T00:00:00+00:00",
                    "updated_at": "2026-04-26T00:00:00+00:00",
                },
            )
            upsert_asset(
                db_path,
                {
                    "asset_id": "pattern_healthy_existing",
                    "workspace": str(workspace),
                    "asset_type": "pattern",
                    "knowledge_scope": "project",
                    "knowledge_kind": "pattern",
                    "title": "healthy pattern",
                    "content": "already proven",
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "confidence": 0.85,
                    "status": "active",
                    "review_status": "healthy",
                    "temperature": "warm",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                },
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
            queue = payload["unproven_validation_queue"]
            self.assertEqual(queue["asset_count"], 1)
            self.assertEqual(queue["top_items"][0]["asset_id"], "pattern_unproven_priority")
            self.assertIn("Needs first real activation", queue["top_items"][0]["validation_hint"])

    def test_cli_status_prioritizes_unproven_assets_relevant_to_recent_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)

            db_path = default_db_path(workspace)
            ensure_db(db_path)
            upsert_asset(
                db_path,
                {
                    "asset_id": "pattern_activation_feedback",
                    "workspace": str(workspace),
                    "asset_type": "pattern",
                    "knowledge_scope": "project",
                    "knowledge_kind": "pattern",
                    "title": "activation feedback workflow",
                    "content": "record feedback after activation validation",
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "confidence": 0.75,
                    "status": "active",
                    "review_status": "unproven",
                    "temperature": "neutral",
                    "created_at": "2026-04-27T00:00:00+00:00",
                    "updated_at": "2026-04-27T00:00:00+00:00",
                },
            )
            upsert_asset(
                db_path,
                {
                    "asset_id": "pattern_branch_protection",
                    "workspace": str(workspace),
                    "asset_type": "pattern",
                    "knowledge_scope": "project",
                    "knowledge_kind": "pattern",
                    "title": "branch protection workflow",
                    "content": "configure repository branch protection",
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "confidence": 0.75,
                    "status": "active",
                    "review_status": "unproven",
                    "temperature": "neutral",
                    "created_at": "2026-04-27T00:00:00+00:00",
                    "updated_at": "2026-04-27T00:00:00+00:00",
                },
            )
            log_activation(
                db_path,
                {
                    "activation_id": "act_recent_activation_feedback",
                    "workspace": str(workspace),
                    "task_query": "continue improving activation feedback workflow",
                    "selected_asset_ids": [],
                    "selected_assets": [],
                    "feedback": {"help_signal": "supported_strong"},
                    "created_at": "2026-04-27T00:30:00+00:00",
                },
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
            queue = payload["unproven_validation_queue"]
            self.assertEqual(queue["top_items"][0]["asset_id"], "pattern_activation_feedback")
            self.assertIn("activation", queue["top_items"][0]["recent_topic_hits"])
            self.assertIn("Recent task overlap", queue["top_items"][0]["validation_hint"])

    def test_cli_feedback_records_signal_and_refreshes_asset_effectiveness(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)

            db_path = default_db_path(workspace)
            ensure_db(db_path)
            upsert_asset(
                db_path,
                {
                    "asset_id": "pattern_feedback_target",
                    "workspace": str(workspace),
                    "asset_type": "pattern",
                    "knowledge_scope": "project",
                    "knowledge_kind": "pattern",
                    "title": "feedback target pattern",
                    "content": "validate feedback command",
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "confidence": 0.8,
                    "status": "active",
                    "review_status": "unproven",
                    "temperature": "neutral",
                    "created_at": "2026-04-27T00:00:00+00:00",
                    "updated_at": "2026-04-27T00:00:00+00:00",
                },
            )
            log_activation(
                db_path,
                {
                    "activation_id": "act_feedback_target",
                    "workspace": str(workspace),
                    "task_query": "validate feedback target",
                    "selected_asset_ids": ["pattern_feedback_target"],
                    "selected_assets": [
                        {
                            "asset_id": "pattern_feedback_target",
                            "asset_type": "pattern",
                            "knowledge_scope": "project",
                            "title": "feedback target pattern",
                        }
                    ],
                    "created_at": "2026-04-27T00:30:00+00:00",
                },
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "feedback",
                    "--workspace",
                    str(workspace),
                    "--activation-id",
                    "act_feedback_target",
                    "--help-signal",
                    "supported_strong",
                    "--feedback-summary",
                    "validated through direct feedback command",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            payload = json.loads(completed.stdout)
            self.assertTrue(payload["updated"])
            self.assertEqual(payload["activation_feedback"]["activation_id"], "act_feedback_target")
            self.assertEqual(payload["activation_feedback"]["help_signal"], "supported_strong")
            activation = list_activation_logs(db_path, workspace=str(workspace), limit=1)[0]
            self.assertEqual(activation["feedback"]["help_signal"], "supported_strong")
            asset = cli_main.get_asset(db_path, asset_id="pattern_feedback_target")
            self.assertEqual(asset["review_status"], "healthy")
            self.assertEqual(asset["temperature"], "warm")
            self.assertEqual(asset["historical_help"]["supported_count"], 1)

    def test_build_doctor_payload_surfaces_stale_milvus_lock(self) -> None:
        status_payload = {
            "counts": {"traces": 0, "episodes": 0, "candidates": 0, "assets": 0, "activation_logs": 1},
            "retrieval_backends": {
                "sqlite": {
                    "db_exists": True,
                    "asset_rows": 0,
                    "candidate_rows": 0,
                    "activation_log_rows": 1,
                },
                "milvus": {
                    "local": {"status": "degraded", "mode": "local", "degraded_reason": "unix_socket_bind_unavailable"},
                },
            },
            "milvus_retrieval_effectiveness": {
                "selected_from_milvus": 0,
                "selected_total": 0,
                "activations_with_milvus_selected": 0,
                "activation_count": 1,
                "activation_selected_ratio": 0.0,
                "avg_selected_vector_score": 0.0,
            },
            "activation_feedback_summary": {
                "supported_strong": 0,
                "supported_weak": 0,
                "pending": 0,
                "missing": 0,
            },
            "unresolved_activations": [],
            "candidate_review_queue": {"candidate_count": 0},
            "unproven_validation_queue": {"asset_count": 2, "top_items": [{"asset_id": "pattern_x", "priority_score": 0.91}]},
            "asset_effectiveness_summary": {"review_status": {"healthy": 0, "watch": 0, "needs_review": 0, "unproven": 0}},
            "asset_review_backlog": {
                "healthy_count": 0,
                "total_assets": 0,
                "unproven_count": 0,
                "unproven_ratio": 0.0,
            },
        }
        stale_lock = {
            "lock_path": "/tmp/local.lock",
            "lock_exists": True,
            "locked": None,
            "lock_error": "operation not permitted",
            "metadata_raw": "pid=123 acquired_at=1.0",
            "metadata": {"pid": 123, "acquired_at": 1.0},
            "pid_exists": False,
            "age_seconds": 10.0,
            "stale_hint": True,
        }
        shared_lock = {
            "lock_path": "/tmp/shared.lock",
            "lock_exists": False,
            "locked": False,
            "lock_error": None,
            "metadata_raw": "",
            "metadata": {},
            "pid_exists": None,
            "age_seconds": None,
            "stale_hint": False,
        }

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            cli_main, "_build_status_payload", return_value=status_payload
        ), patch.object(cli_main, "milvus_lock_summary", side_effect=[stale_lock, shared_lock]):
            doctor = cli_main._build_doctor_payload(
                workspace=(Path(tmpdir) / "workspace").resolve(),
                limit=3,
                deep_retrieval_check=False,
            )

        local_milvus_check = next(item for item in doctor["checks"] if item["name"] == "local_milvus")
        lock_check = next(item for item in doctor["checks"] if item["name"] == "local_milvus_lock")
        validation_check = next(item for item in doctor["checks"] if item["name"] == "unproven_validation_queue")
        self.assertIn("dead pid", local_milvus_check["recommendation"])
        self.assertIn("stale pid", lock_check["summary"])
        self.assertIn("safe cleanup/reset", lock_check["recommendation"])
        self.assertIn("2 assets", validation_check["summary"])

    def test_cli_auto_start_still_runs_for_inactive_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "install-project",
                    "--workspace",
                    str(workspace),
                    "--project-status",
                    "inactive",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            started = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "auto-start",
                    "--workspace",
                    str(workspace),
                    "--task",
                    "inactive workspace still activates on new chat",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(started.stdout)

            self.assertEqual(payload["project_activity"]["project_status"], "inactive")
            self.assertEqual(payload["project_activity"]["auto_start_mode"], "always_on_new_chat")
            self.assertEqual(payload["selected_count"], 0)

            status = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "status",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            status_payload = json.loads(status.stdout)["status"]
            self.assertEqual(status_payload["project_activity"]["project_status"], "inactive")
            self.assertTrue(status_payload["project_activity"]["auto_start_enabled"])
            self.assertEqual(status_payload["project_activity"]["auto_start_mode"], "always_on_new_chat")
            self.assertEqual(status_payload["counts"]["activation_logs"], 1)

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
            self.assertEqual(payload["retrieval_backends"]["sqlite"]["role"], "lightweight-state-index")
            self.assertFalse(payload["retrieval_backends"]["sqlite"]["core_retrieval"])
            self.assertTrue(payload["retrieval_backends"]["sqlite"]["db_exists"])
            self.assertEqual(payload["retrieval_backends"]["milvus"]["role"], "core-semantic-retrieval")
            self.assertTrue(payload["retrieval_backends"]["milvus"]["core_retrieval"])
            self.assertEqual(payload["retrieval_backends"]["milvus"]["local"]["backend"], "milvus-lite")
            self.assertIn("available", payload["retrieval_backends"]["milvus"])
            self.assertEqual(payload["retrieval_backends"]["milvus"]["embedding"]["provider"], "hash")
            self.assertEqual(payload["retrieval_backends"]["milvus"]["embedding"]["dim"], 128)
            self.assertEqual(payload["retrieval_backends"]["milvus"]["embedding"]["version"], "1")
            self.assertEqual(
                payload["retrieval_backends"]["milvus"]["embedding"]["profile"],
                "hash-token-sha256-signhash-128",
            )
            self.assertTrue(
                payload["retrieval_backends"]["milvus"]["local"]["db_path"].endswith(
                    "milvus.hash-token-sha25-a1e82f9f.db"
                )
            )
            self.assertTrue(payload["retrieval_backends"]["milvus"]["legacy_local_path"].endswith("milvus.db"))
            self.assertFalse(payload["retrieval_backends"]["milvus"]["local"]["deep_check"])
            self.assertIn("collection_exists", payload["retrieval_backends"]["milvus"]["local"])
            self.assertTrue(payload["retrieval_backends"]["milvus"]["asset_coverage"]["deep_check_required"])
            self.assertIn("milvus_retrieval_effectiveness", payload)
            self.assertEqual(payload["milvus_retrieval_effectiveness"]["selected_total"], 1)
            self.assertEqual(payload["backend_configuration"]["profile"], "local")
            self.assertEqual(payload["backend_configuration"]["storage_profile"], "local")
            self.assertEqual(payload["backend_configuration"]["source_of_truth"], "local-json")
            self.assertEqual(payload["backend_configuration"]["state_index"], "sqlite")
            self.assertEqual(payload["backend_configuration"]["retrieval"], "milvus-lite")
            self.assertEqual(payload["backend_configuration"]["state_index_role"], "lightweight-state-index")
            self.assertEqual(payload["backend_configuration"]["retrieval_role"], "core-semantic-retrieval")
            self.assertEqual(payload["backend_configuration"]["asset_portability"], "local-deliverable")
            self.assertEqual(payload["storage_layout"]["storage_profile"], "local")
            self.assertEqual(payload["storage_layout"]["retrieval_index_profile"], "hash-token-sha256-signhash-128")
            self.assertTrue(payload["storage_layout"]["local_runtime_data_in_project"])

    def test_milvus_benchmark_uses_recent_activation_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            db_path = default_db_path(workspace)
            ensure_db(db_path)
            log_activation(
                db_path,
                {
                    "activation_id": "act_benchmark_1",
                    "workspace": str(workspace),
                    "task_query": "milvus embedding benchmark",
                    "selected_assets": [{"asset_id": "asset_hit"}],
                    "created_at": "2026-04-26T00:00:00+00:00",
                },
            )

            def fake_search(*args, **kwargs):
                self.assertEqual(kwargs["query_text"], "milvus embedding benchmark")
                return [
                    {
                        "asset_id": "asset_hit",
                        "title": "Milvus benchmark asset",
                        "knowledge_scope": "project",
                        "knowledge_kind": "pattern",
                        "vector_score": 0.73,
                        "embedding": {"provider": "hash", "model": "token-sha256-signhash"},
                    }
                ]

            with patch.object(cli_main, "search_asset_vectors", side_effect=fake_search), patch.object(
                cli_main,
                "milvus_available",
                return_value=True,
            ):
                payload = cli_main._build_milvus_benchmark_payload(
                    workspace=workspace,
                    queries=[],
                    sample_size=5,
                    limit=3,
                    include_shared=False,
                )

            self.assertEqual(payload["sample_count"], 1)
            self.assertEqual(payload["summary"]["queries_with_results"], 1)
            self.assertEqual(payload["summary"]["comparable_queries"], 1)
            self.assertEqual(payload["summary"]["queries_with_expected_hit"], 1)
            self.assertEqual(payload["summary"]["expected_hit_rate"], 1.0)
            self.assertEqual(payload["samples"][0]["hit_asset_ids"], ["asset_hit"])
            self.assertEqual(payload["samples"][0]["results"][0]["embedding"]["provider"], "hash")

    def test_milvus_benchmark_syncs_active_indexes_before_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)

            with patch.object(
                cli_main,
                "sync_assets_directory_with_report",
                side_effect=[
                    {"synced": 2, "pruned": 0},
                    {"synced": 1, "pruned": 0},
                ],
            ) as sync_mock, patch.object(cli_main, "search_asset_vectors", return_value=[]), patch.object(
                cli_main,
                "milvus_available",
                return_value=True,
            ):
                payload = cli_main._build_milvus_benchmark_payload(
                    workspace=workspace,
                    queries=["profile sync benchmark"],
                    sample_size=5,
                    limit=3,
                    include_shared=True,
                )

            self.assertEqual(sync_mock.call_count, 2)
            local_db_path, local_assets_dir = sync_mock.call_args_list[0].args
            shared_db_path, shared_assets_dir = sync_mock.call_args_list[1].args
            self.assertTrue(str(local_db_path).endswith(".db"))
            self.assertEqual(local_assets_dir.name, "assets")
            self.assertTrue(str(shared_db_path).endswith(".db"))
            self.assertEqual(shared_assets_dir.name, "assets")
            self.assertEqual(payload["preflight_sync"]["local"]["synced"], 2)
            self.assertEqual(payload["preflight_sync"]["shared"]["synced"], 1)

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
