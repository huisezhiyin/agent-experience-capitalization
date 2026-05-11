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
from runtime.storage.fs_store import default_db_path, fallback_memory_root_for_workspace
from runtime.storage.sqlite_store import list_assets, upsert_asset
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
    knowledge_kind: str = "pattern",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "candidate_id": candidate_id,
                "source_episode_ids": ["ep_manual_review_001"],
                "workspace": str(workspace),
                "candidate_type": "pattern",
                "knowledge_kind": knowledge_kind,
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
    def test_filesystem_status_records_merge_primary_and_fallback_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"EXPCAP_STORAGE_PROFILE": "user-cache", "EXPCAP_HOME": str(Path(tmpdir) / "expcap-home")},
        ):
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)

            primary_root = cli_main.memory_root_for_workspace(workspace)
            fallback_root = fallback_memory_root_for_workspace(workspace)
            (primary_root / "assets" / "patterns").mkdir(parents=True, exist_ok=True)
            (fallback_root / "candidates").mkdir(parents=True, exist_ok=True)
            (fallback_root / "views").mkdir(parents=True, exist_ok=True)

            (primary_root / "assets" / "patterns" / "pattern_primary.json").write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_primary",
                        "asset_type": "pattern",
                        "status": "active",
                        "created_at": "2026-05-11T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (fallback_root / "candidates" / "cand_fallback.json").write_text(
                json.dumps(
                    {
                        "candidate_id": "cand_fallback",
                        "status": "needs_review",
                        "created_at": "2026-05-11T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (fallback_root / "views" / "act_fallback.json").write_text(
                json.dumps(
                    {
                        "activation_id": "act_fallback",
                        "task_query": "repair user-cache fallback",
                        "created_at": "2026-05-11T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            assets, candidates, activations = cli_main._filesystem_status_records(workspace=workspace)

            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0]["asset_id"], "pattern_primary")
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["candidate_id"], "cand_fallback")
            self.assertEqual(len(activations), 1)
            self.assertEqual(activations[0]["activation_id"], "act_fallback")

    def test_find_unresolved_activation_falls_back_to_activation_views(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"EXPCAP_STORAGE_PROFILE": "user-cache", "EXPCAP_HOME": str(Path(tmpdir) / "expcap-home")},
        ):
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            fallback_root = fallback_memory_root_for_workspace(workspace)
            views_dir = fallback_root / "views"
            views_dir.mkdir(parents=True, exist_ok=True)
            (views_dir / "act_fix-user-cache.json").write_text(
                json.dumps(
                    {
                        "activation_id": "act_fix-user-cache",
                        "task_query": "fix user-cache writes",
                        "created_at": "2026-05-11T00:00:00+00:00",
                        "selected_assets": [{"asset_id": "pattern_001"}],
                    }
                ),
                encoding="utf-8",
            )

            activation = cli_main._find_unresolved_activation_for_task(
                db_path=default_db_path(workspace),
                workspace=workspace,
                task="fix user-cache writes",
            )

            assert activation is not None
            self.assertEqual(activation["activation_id"], "act_fix-user-cache")

    def test_cli_auto_start_uses_fallback_sqlite_and_injection_paths_when_primary_root_is_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"EXPCAP_STORAGE_PROFILE": "user-cache", "EXPCAP_HOME": str(Path(tmpdir) / "expcap-home")},
        ):
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            primary_root = cli_main.memory_root_for_workspace(workspace)
            fallback_root = fallback_memory_root_for_workspace(workspace)
            original_save_json = cli_main.save_json

            def flaky_save_json(path: Path, payload: dict[str, object]) -> None:
                try:
                    Path(path).relative_to(primary_root)
                except ValueError:
                    original_save_json(path, payload)
                    return
                raise PermissionError("operation not permitted for primary user-cache root")

            args = argparse.Namespace(
                workspace=str(workspace),
                task="repair readonly auto-start fallback",
                constraints=[],
                output=None,
            )
            captured: dict[str, object] = {}

            with patch.object(cli_main, "save_json", side_effect=flaky_save_json), patch.object(
                cli_main,
                "_print_json",
                side_effect=lambda payload: captured.update(payload),
            ):
                result = cli_main._handle_auto_start(args)

            self.assertEqual(result, 0)
            self.assertNotIn("log_warning", captured)
            self.assertNotIn("injection_artifact_warning", captured)
            self.assertTrue(str(captured["saved_to"]).startswith(str(fallback_root)))
            injection_artifacts = captured["injection_artifacts"]
            assert isinstance(injection_artifacts, dict)
            self.assertTrue(Path(injection_artifacts["json_path"]).exists())
            self.assertTrue(Path(injection_artifacts["markdown_path"]).exists())

    def test_cli_feedback_can_update_activation_from_fallback_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"EXPCAP_STORAGE_PROFILE": "user-cache", "EXPCAP_HOME": str(Path(tmpdir) / "expcap-home")},
        ):
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            fallback_db = fallback_memory_root_for_workspace(workspace) / "index.sqlite3"
            ensure_db(fallback_db)
            activation = {
                "activation_id": "act_fallback_feedback",
                "workspace": str(workspace),
                "task_query": "repair readonly auto-start fallback",
                "selected_assets": [{"asset_id": "pattern_001", "asset_type": "pattern", "knowledge_scope": "project"}],
                "selected_asset_ids": ["pattern_001"],
                "created_at": "2026-05-11T00:00:00+00:00",
            }
            log_activation(fallback_db, activation)
            views_dir = fallback_memory_root_for_workspace(workspace) / "views"
            views_dir.mkdir(parents=True, exist_ok=True)
            (views_dir / "act_fallback_feedback.json").write_text(
                json.dumps(activation, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            args = argparse.Namespace(
                workspace=str(workspace),
                activation_id=None,
                help_signal="supported_strong",
                feedback_summary="fallback feedback recorded",
                feedback_at="2026-05-11T00:01:00+00:00",
                signal_source="manual_feedback",
            )
            captured: dict[str, object] = {}

            with patch.object(cli_main, "_print_json", side_effect=lambda payload: captured.update(payload)):
                result = cli_main._handle_feedback(args)

            self.assertEqual(result, 0)
            self.assertTrue(captured["updated"])
            activation_feedback = captured["activation_feedback"]
            assert isinstance(activation_feedback, dict)
            self.assertEqual(activation_feedback["activation_id"], "act_fallback_feedback")

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
            self.assertEqual(activation["pipeline"]["stages"], ["retrieve", "rerank", "route_injection", "assemble"])
            self.assertIn("injection_artifacts", activation)
            injection_markdown = Path(activation["injection_artifacts"]["markdown_path"])
            self.assertTrue(injection_markdown.exists())
            self.assertIn("Runtime Context", injection_markdown.read_text(encoding="utf-8"))
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

    def test_cli_ingest_docs_imports_markdown_as_codemap_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "README.md").write_text(
                "# Demo Repo\n\nUse the runtime package for CLI orchestration.\n",
                encoding="utf-8",
            )
            docs_dir = workspace / "docs"
            docs_dir.mkdir()
            (docs_dir / "architecture.md").write_text(
                "# Architecture\n\nThe engine owns activation ranking while CLI owns command orchestration.\n",
                encoding="utf-8",
            )
            ignored_dir = workspace / ".agent-memory"
            ignored_dir.mkdir()
            (ignored_dir / "ignored.md").write_text("# ignored\n", encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "ingest-docs",
                    "--workspace",
                    str(workspace),
                    "--max-chars",
                    "1200",
                ],
                cwd=REPO_ROOT,
                env={**os.environ, "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            report = payload["ingestion"]

            self.assertEqual(report["document_count"], 2)
            self.assertEqual(report["asset_count"], 2)
            self.assertEqual(report["pruned_existing_assets"], 0)
            self.assertTrue(Path(payload["saved_to"]).exists())
            self.assertEqual({item["knowledge_kind"] for item in report["assets"]}, {"codemap"})
            asset_path = Path(report["assets"][0]["saved_to"])
            asset = json.loads(asset_path.read_text(encoding="utf-8"))
            self.assertEqual(asset["asset_type"], "context")
            self.assertEqual(asset["knowledge_kind"], "codemap")
            self.assertTrue(asset["doc_chunk"]["preserved_raw_text"])
            self.assertIn("Document:", asset["content"])

            assets = list_assets(default_db_path(workspace), workspace=str(workspace))
            self.assertEqual(len(assets), 2)
            self.assertEqual({item["knowledge_kind"] for item in assets}, {"codemap"})

            status = subprocess.run(
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
            status_payload = json.loads(status.stdout)["status"]
            self.assertEqual(status_payload["knowledge_kind_summary"]["assets"]["by_kind"]["codemap"], 2)
            self.assertEqual(status_payload["knowledge_kind_summary"]["assets"]["local_prior_count"], 2)
            self.assertEqual(status_payload["asset_review_backlog"]["total_assets"], 0)
            self.assertEqual(status_payload["unproven_validation_queue"]["asset_count"], 0)

            benchmark = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "benchmark-milvus",
                    "--workspace",
                    str(workspace),
                    "--query",
                    "README runtime package CLI orchestration",
                    "--limit",
                    "2",
                    "--expect-kind",
                    "codemap",
                    "--expect-source-document",
                    "README.md",
                ],
                cwd=REPO_ROOT,
                env={**os.environ, "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )
            benchmark_payload = json.loads(benchmark.stdout)["benchmark"]
            self.assertEqual(benchmark_payload["summary"]["expected_kind_hit_rate"], 1.0)
            self.assertEqual(benchmark_payload["summary"]["expected_source_document_hit_rate"], 1.0)
            self.assertEqual(
                benchmark_payload["samples"][0]["results"][0]["source_document"],
                "README.md",
            )

            rerun = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "ingest-docs",
                    "--workspace",
                    str(workspace),
                    "--max-chars",
                    "1200",
                ],
                cwd=REPO_ROOT,
                env={**os.environ, "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )
            rerun_report = json.loads(rerun.stdout)["ingestion"]
            self.assertEqual(rerun_report["asset_count"], 2)
            self.assertEqual(rerun_report["pruned_existing_assets"], 2)

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
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"EXPCAP_STORAGE_PROFILE": "user-cache", "EXPCAP_HOME": str((Path(tmpdir) / "expcap-home").resolve())},
        ):
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
            self.assertEqual(warning["runtime_state"], "fallback_active")
            self.assertEqual(warning["severity"], "warn")
            self.assertEqual(warning["reason"], "default_activation_view_unwritable")
            self.assertEqual(warning["requested_path"], str(default_path))
            fallback_path = Path(captured["saved_to"])
            self.assertTrue(fallback_path.exists())
            self.assertEqual(json.loads(fallback_path.read_text(encoding="utf-8"))["activation_id"], "act_review-fallback")
            self.assertEqual(len(list_activation_logs(default_db_path(workspace), workspace=str(workspace))), 1)

    def test_cli_auto_start_warns_when_activation_log_is_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            captured: dict[str, object] = {}
            view = {
                "activation_id": "act_log-fallback",
                "task_query": "review log fallback handling",
                "workspace": str(workspace),
                "selected_assets": [],
                "selected_asset_ids": [],
                "retrieval_summary": {},
                "created_at": "2026-04-28T00:00:00+00:00",
            }
            args = argparse.Namespace(
                workspace=str(workspace),
                task="review log fallback handling",
                constraints=[],
                output=None,
            )

            with patch.object(cli_main, "activate_assets", return_value=view), patch.object(
                cli_main,
                "log_activation",
                side_effect=sqlite3.OperationalError("attempt to write a readonly database"),
            ), patch.object(cli_main, "_print_json", side_effect=lambda payload: captured.update(payload)):
                result = cli_main._handle_auto_start(args)

            self.assertEqual(result, 0)
            self.assertEqual(captured["activation_id"], "act_log-fallback")
            self.assertIn("log_warning", captured)
            warning = captured["log_warning"]
            assert isinstance(warning, dict)
            self.assertEqual(warning["reason"], "sqlite_activation_log_unwritable")
            self.assertIn("readonly database", warning["error"])

    def test_cli_auto_start_warns_when_feedback_cleanup_is_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            captured: dict[str, object] = {}
            view = {
                "activation_id": "act_feedback-cleanup-warning",
                "task_query": "review cleanup fallback handling",
                "workspace": str(workspace),
                "selected_assets": [],
                "selected_asset_ids": [],
                "retrieval_summary": {},
                "created_at": "2026-04-28T00:00:00+00:00",
            }
            args = argparse.Namespace(
                workspace=str(workspace),
                task="review cleanup fallback handling",
                constraints=[],
                output=None,
            )

            with patch.object(cli_main, "activate_assets", return_value=view), patch.object(
                cli_main,
                "_safe_feedback_cleanup",
                return_value=(
                    None,
                    cli_main._sqlite_degraded_warning(
                        db_path=default_db_path(workspace),
                        error=sqlite3.OperationalError("attempt to write a readonly database"),
                    ),
                ),
            ), patch.object(cli_main, "_print_json", side_effect=lambda payload: captured.update(payload)):
                result = cli_main._handle_auto_start(args)

            self.assertEqual(result, 0)
            self.assertEqual(captured["activation_id"], "act_feedback-cleanup-warning")
            self.assertIn("feedback_cleanup", captured)
            self.assertIn("feedback_cleanup_warning", captured)
            cleanup = captured["feedback_cleanup"]
            warning = captured["feedback_cleanup_warning"]
            assert isinstance(cleanup, dict)
            assert isinstance(warning, dict)
            self.assertEqual(cleanup["auto_resolved_count"], 0)
            self.assertEqual(warning["reason"], "sqlite_index_unavailable")
            self.assertIn("readonly database", warning["error"])

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
            self.assertEqual(view["progressive_recall"]["injection_layer"], "continuous_runtime_recall_injection")
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
                env={**dict(os.environ), "CODEX_HOME": str(codex_home), "EXPCAP_STORAGE_PROFILE": "local"},
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

            conn = sqlite3.connect(db_path)
            try:
                with conn:
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
            finally:
                conn.close()

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

    def test_cli_auto_finish_warns_when_feedback_cleanup_is_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"EXPCAP_STORAGE_PROFILE": "local"}):
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            captured: dict[str, object] = {}
            args = argparse.Namespace(
                workspace=str(workspace),
                task="fix cleanup fallback handling",
                user_request=None,
                constraints=[],
                commands=[],
                errors=[],
                files_changed=[],
                verification_status="passed",
                verification_summary="1 passed",
                result_status="success",
                result_summary="verified",
                host=None,
                session_id=None,
                trace_id="trace_feedback_cleanup_warning",
                no_promote=False,
                promote_threshold=0.75,
                knowledge_scope="project",
                knowledge_kind="pattern",
            )

            with patch.object(
                cli_main,
                "_safe_feedback_cleanup",
                return_value=(
                    None,
                    cli_main._sqlite_degraded_warning(
                        db_path=default_db_path(workspace),
                        error=sqlite3.OperationalError("attempt to write a readonly database"),
                    ),
                ),
            ), patch.object(cli_main, "_print_json", side_effect=lambda payload: captured.update(payload)):
                result = cli_main._handle_auto_finish(args)

            self.assertEqual(result, 0)
            self.assertIn("feedback_cleanup", captured)
            self.assertIn("feedback_cleanup_warning", captured)
            self.assertEqual(captured["trace"]["trace_id"], "trace_feedback_cleanup_warning")
            cleanup = captured["feedback_cleanup"]
            warning = captured["feedback_cleanup_warning"]
            assert isinstance(cleanup, dict)
            assert isinstance(warning, dict)
            self.assertEqual(cleanup["auto_resolved_count"], 0)
            self.assertEqual(warning["reason"], "sqlite_index_unavailable")
            self.assertIn("readonly database", warning["error"])

    def test_cli_auto_finish_falls_back_when_primary_memory_root_is_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"EXPCAP_STORAGE_PROFILE": "user-cache", "EXPCAP_HOME": str(Path(tmpdir) / "expcap-home")},
        ):
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            captured: dict[str, object] = {}
            primary_root = cli_main.memory_root_for_workspace(workspace)
            fallback_root = fallback_memory_root_for_workspace(workspace)
            original_save_json = cli_main.save_json

            def flaky_save_json(path: Path, payload: dict[str, object]) -> None:
                try:
                    Path(path).relative_to(primary_root)
                except ValueError:
                    original_save_json(path, payload)
                    return
                raise PermissionError("operation not permitted for primary user-cache root")

            args = argparse.Namespace(
                workspace=str(workspace),
                task="repair user-cache write path",
                user_request=None,
                constraints=[],
                commands=[],
                errors=[],
                files_changed=[],
                verification_status="passed",
                verification_summary="1 passed",
                result_status="success",
                result_summary="write fallback works",
                host=None,
                session_id=None,
                trace_id="trace_user_cache_fallback",
                no_promote=False,
                promote_threshold=0.75,
                knowledge_scope="project",
                knowledge_kind="pattern",
            )

            with patch.object(cli_main, "save_json", side_effect=flaky_save_json), patch.object(
                cli_main,
                "upsert_trace",
                side_effect=sqlite3.OperationalError("attempt to write a readonly database"),
            ), patch.object(
                cli_main,
                "upsert_episode",
                side_effect=sqlite3.OperationalError("attempt to write a readonly database"),
            ), patch.object(
                cli_main,
                "upsert_candidate",
                side_effect=sqlite3.OperationalError("attempt to write a readonly database"),
            ), patch.object(
                cli_main,
                "_print_json",
                side_effect=lambda payload: captured.update(payload),
            ):
                result = cli_main._handle_auto_finish(args)

            self.assertEqual(result, 0)
            self.assertTrue(str(captured["trace"]["path"]).startswith(str(fallback_root)))
            self.assertTrue(str(captured["episode"]["path"]).startswith(str(fallback_root)))
            self.assertIn("write_warnings", captured)
            self.assertIn("sqlite_warnings", captured)
            self.assertTrue(Path(captured["trace"]["path"]).exists())

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
                        "knowledge_kind": "preference",
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
                                "injection_channel": "system_prompt",
                            }
                        ],
                        "injection_plan": {
                            "policy": "local_prior_injection_v1",
                            "channel_counts": {
                                "system_prompt": 1,
                                "runtime_context": 0,
                                "reference_summary": 1,
                            },
                            "channels": {
                                "system_prompt": {"items": [{"asset_id": "pattern_dashboard_001"}]},
                                "runtime_context": {"items": []},
                                "reference_summary": {"items": [{"asset_id": "context_doc_dashboard_001"}]},
                            },
                        },
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
            self.assertIn("Backend Runtime", html)
            self.assertIn("Unproven Validation Queue", html)
            self.assertIn("Local Prior Distribution", html)
            self.assertIn("Injection Channels", html)
            self.assertIn("Injection Layers", html)
            self.assertEqual(payload["dashboard"]["cards"]["assets"], 1)
            self.assertEqual(payload["dashboard"]["cards"]["local_prior_assets"], 1)
            self.assertEqual(payload["dashboard"]["cards"]["high_priority_prior_assets"], 1)
            self.assertEqual(payload["dashboard"]["cards"]["system_prompt_items"], 1)
            self.assertEqual(payload["dashboard"]["cards"]["reference_summary_items"], 1)
            self.assertIn("effectiveness_snapshot", payload["dashboard"])
            self.assertEqual(payload["dashboard"]["unproven_validation_count"], 0)
            self.assertEqual(dashboard["cards"]["healthy_assets"], 1)
            self.assertEqual(dashboard["knowledge_kind_summary"]["assets"]["by_kind"]["preference"], 1)
            self.assertEqual(dashboard["knowledge_kind_summary"]["assets"]["high_priority_count"], 1)
            self.assertEqual(dashboard["injection_policy_summary"]["channel_counts"]["system_prompt"], 1)
            self.assertEqual(
                dashboard["injection_policy_summary"]["layer_counts"]["system_prompt_injection"],
                1,
            )
            self.assertIn("knowledge_save_layers", dashboard)
            self.assertEqual(dashboard["knowledge_save_layers"]["logs"]["role"], "raw_execution_evidence")
            self.assertEqual(dashboard["injection_policy_summary"]["channel_counts"]["reference_summary"], 1)
            self.assertEqual(dashboard["activations"][0]["injection_channel_counts"]["system_prompt"], 1)
            self.assertEqual(dashboard["effectiveness_snapshot"]["verdict"], "healthy")
            self.assertEqual(dashboard["retrieval"]["effectiveness"]["selected_from_milvus"], 1)
            self.assertEqual(dashboard["activations"][0]["help_signal"], "supported_strong")
            self.assertEqual(dashboard["unproven_validation_queue"]["asset_count"], 0)

    def test_dashboard_html_shows_backend_runtime_panel_for_fallback_sqlite(self) -> None:
        payload = {
            "workspace": "/tmp/workspace",
            "generated_at": "2026-05-11T00:00:00+00:00",
            "cards": {
                "assets": 0,
                "candidates": 0,
                "activation_logs": 1,
                "healthy_assets": 0,
                "unproven_assets": 0,
                "local_prior_assets": 0,
                "high_priority_prior_assets": 0,
                "system_prompt_items": 0,
                "reference_summary_items": 0,
                "milvus_selected_ratio": 0.0,
                "activation_selected_ratio": 0.0,
                "stale_missing_feedback": 0,
            },
            "effectiveness_snapshot": {
                "overall_score": 50,
                "verdict": "watch",
                "signals": [
                    {"label": "Asset quality", "ratio": 0.0, "value": "0/0 healthy"},
                    {"label": "Activation help", "ratio": 0.0, "value": "0/1 helpful"},
                    {"label": "Milvus contribution", "ratio": 0.0, "value": "0% activations"},
                    {"label": "Write activity", "ratio": 0.0, "value": "0 writes / 14d"},
                ],
            },
            "write_frequency": [],
            "assets": [],
            "activations": [],
            "candidates": [],
            "review_queue": {"candidate_count": 0, "top_items": []},
            "unproven_assets": [],
            "knowledge_kind_summary": {
                "assets": {"local_prior_count": 0, "high_priority_count": 0, "by_kind": {}, "high_priority_by_kind": {}},
                "candidates": {"local_prior_count": 0, "high_priority_count": 0, "by_kind": {}, "high_priority_by_kind": {}},
                "review_queue": {"local_prior_count": 0, "high_priority_count": 0, "by_kind": {}, "high_priority_by_kind": {}},
            },
            "injection_policy_summary": {
                "policy": "layered_knowledge_injection_v1",
                "plan_coverage_ratio": 0.0,
                "avg_items_per_activation": 0.0,
                "channel_counts": {"system_prompt": 0, "runtime_context": 0, "reference_summary": 0},
                "layer_counts": {"task_start_runtime_injection": 0, "system_prompt_injection": 0, "continuous_runtime_recall_injection": 0},
                "activations_with_channels": {"system_prompt": 0, "runtime_context": 0, "reference_summary": 0},
                "activations_with_layers": {"task_start_runtime_injection": 0, "system_prompt_injection": 0, "continuous_runtime_recall_injection": 0},
            },
            "retrieval": {"effectiveness": {"selected_from_milvus": 0, "selected_total": 0, "milvus_selected_ratio": 0.0, "activation_selected_ratio": 0.0, "avg_selected_vector_score": 0.0}},
            "quality": {
                "asset_effectiveness_summary": {"review_status": {}, "temperature": {}},
                "activation_feedback_summary": {},
            },
            "status": {
                "backend_runtime": {
                    "memory_root_mode": "fallback_active",
                    "primary_memory_root": "/readonly/root",
                    "fallback_memory_root": "/tmp/expcap-runtime",
                    "fallback_memory_root_present": True,
                    "state_index_mode": "fallback_sqlite",
                    "primary_state_index_path": "/readonly/root/index.sqlite3",
                    "active_state_index_path": "/tmp/expcap-runtime/index.sqlite3",
                    "fallback_state_index_in_use": True,
                },
                "retrieval_backends": {
                    "sqlite": {
                        "available": True,
                    }
                },
                "runtime_warnings": [],
            },
        }

        html = cli_main._render_dashboard_html(payload)
        self.assertIn("Backend Runtime", html)
        self.assertIn("fallback_sqlite", html)
        self.assertIn("/tmp/expcap-runtime/index.sqlite3", html)

    def test_cli_dashboard_falls_back_when_default_json_sidecar_is_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            captured: dict[str, object] = {}
            default_json_path = workspace / ".agent-memory" / "reviews" / "dashboard.json"
            original_save_json = cli_main.save_json

            def flaky_save_json(path: Path, payload: dict[str, object]) -> None:
                if Path(path) == default_json_path:
                    raise PermissionError("permission denied for default dashboard json")
                original_save_json(path, payload)

            args = argparse.Namespace(
                workspace=str(workspace),
                limit=10,
                days=3,
                deep_retrieval_check=False,
                output=None,
            )

            with patch.dict(os.environ, {"EXPCAP_STORAGE_PROFILE": "local"}), patch.object(
                cli_main,
                "save_json",
                side_effect=flaky_save_json,
            ), patch.object(cli_main, "_print_json", side_effect=lambda payload: captured.update(payload)):
                result = cli_main._handle_dashboard(args)

            self.assertEqual(result, 0)
            self.assertIn("save_warning", captured)
            warning = captured["save_warning"]
            assert isinstance(warning, dict)
            self.assertEqual(warning["reason"], "default_dashboard_output_unwritable")
            fallback_path = Path(captured["saved_to"])
            self.assertTrue(fallback_path.exists())
            self.assertTrue(Path(captured["data_saved_to"]).exists())
            self.assertIn("expcap-reviews", str(fallback_path))

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

    def test_cli_status_falls_back_when_default_output_is_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            captured: dict[str, object] = {}
            default_status_path = workspace / ".agent-memory" / "reviews" / "workspace_status.json"
            original_save_json = cli_main.save_json

            def flaky_save_json(path: Path, payload: dict[str, object]) -> None:
                if Path(path) == default_status_path:
                    raise PermissionError("permission denied for default status json")
                original_save_json(path, payload)

            args = argparse.Namespace(
                workspace=str(workspace),
                limit=3,
                deep_retrieval_check=False,
                output=None,
            )

            with patch.dict(os.environ, {"EXPCAP_STORAGE_PROFILE": "local"}), patch.object(
                cli_main,
                "save_json",
                side_effect=flaky_save_json,
            ), patch.object(cli_main, "_print_json", side_effect=lambda payload: captured.update(payload)):
                result = cli_main._handle_status(args)

            self.assertEqual(result, 0)
            self.assertIn("save_warning", captured)
            warning = captured["save_warning"]
            assert isinstance(warning, dict)
            self.assertEqual(warning["reason"], "default_status_output_unwritable")
            fallback_path = Path(captured["saved_to"])
            self.assertTrue(fallback_path.exists())
            self.assertIn("expcap-reviews", str(fallback_path))

    def test_cli_status_uses_filesystem_fallback_when_sqlite_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            memory_root = workspace / ".agent-memory"
            workspace.mkdir(parents=True, exist_ok=True)
            (memory_root / "assets" / "patterns").mkdir(parents=True, exist_ok=True)
            (memory_root / "views").mkdir(parents=True, exist_ok=True)
            (memory_root / "assets" / "patterns" / "pattern_fs_001.json").write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_fs_001",
                        "asset_type": "pattern",
                        "knowledge_kind": "pattern",
                        "title": "filesystem fallback asset",
                        "status": "active",
                        "confidence": 0.8,
                        "created_at": "2026-04-28T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (memory_root / "views" / "act_fs_001.json").write_text(
                json.dumps(
                    {
                        "activation_id": "act_fs_001",
                        "workspace": str(workspace),
                        "task_query": "status fallback",
                        "selected_assets": [],
                        "created_at": "2026-04-28T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            captured: dict[str, object] = {}
            args = argparse.Namespace(
                workspace=str(workspace),
                limit=3,
                deep_retrieval_check=False,
                output=None,
            )

            with patch.dict(os.environ, {"EXPCAP_STORAGE_PROFILE": "local"}), patch.object(
                cli_main,
                "ensure_db",
                side_effect=sqlite3.OperationalError("readonly sqlite index"),
            ), patch.object(cli_main, "_print_json", side_effect=lambda payload: captured.update(payload)):
                result = cli_main._handle_status(args)

            self.assertEqual(result, 0)
            status = captured["status"]
            assert isinstance(status, dict)
            self.assertFalse(status["retrieval_backends"]["sqlite"]["available"])
            self.assertEqual(status["retrieval_backends"]["sqlite"]["fallback"], "filesystem_json")
            self.assertEqual(status["backend_runtime"]["state_index_mode"], "filesystem_json")
            self.assertIn("knowledge_save_layers", status)
            self.assertEqual(status["knowledge_save_layers"]["sqlite"]["role"], "lightweight_state_index")
            self.assertEqual(status["knowledge_save_layers"]["milvus"]["role"], "semantic_retrieval_index")
            self.assertEqual(status["knowledge_save_layers"]["markdown_files"]["role"], "human_readable_knowledge_assets")
            self.assertEqual(status["knowledge_save_layers"]["logs"]["role"], "raw_execution_evidence")
            self.assertEqual(status["counts"]["assets"], 1)
            self.assertEqual(status["counts"]["activation_logs"], 1)
            self.assertTrue(status["runtime_warnings"])

    def test_cli_doctor_warns_when_sqlite_status_is_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            captured: dict[str, object] = {}
            args = argparse.Namespace(
                workspace=str(workspace),
                limit=3,
                deep_retrieval_check=False,
                output=None,
            )

            with patch.dict(os.environ, {"EXPCAP_STORAGE_PROFILE": "local"}), patch.object(
                cli_main,
                "ensure_db",
                side_effect=sqlite3.OperationalError("locked sqlite index"),
            ), patch.object(cli_main, "_print_json", side_effect=lambda payload: captured.update(payload)):
                result = cli_main._handle_doctor(args)

            self.assertEqual(result, 0)
            doctor = captured["doctor"]
            assert isinstance(doctor, dict)
            sqlite_check = next(item for item in doctor["checks"] if item["name"] == "sqlite_index")
            self.assertEqual(sqlite_check["status"], "warn")
            self.assertIn("filesystem JSON fallback", sqlite_check["summary"])

    def test_cli_status_marks_fallback_sqlite_as_active_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"EXPCAP_STORAGE_PROFILE": "user-cache", "EXPCAP_HOME": str(Path(tmpdir) / "expcap-home")},
        ):
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            fallback_db = fallback_memory_root_for_workspace(workspace) / "index.sqlite3"
            ensure_db(fallback_db)
            log_activation(
                fallback_db,
                {
                    "activation_id": "act_fallback_sqlite_status",
                    "workspace": str(workspace),
                    "task_query": "status with fallback sqlite",
                    "selected_assets": [],
                    "created_at": "2026-05-11T00:00:00+00:00",
                },
            )
            captured: dict[str, object] = {}
            args = argparse.Namespace(
                workspace=str(workspace),
                limit=3,
                deep_retrieval_check=False,
                output=None,
            )

            with patch.object(
                cli_main,
                "ensure_db",
                side_effect=lambda path: (_ for _ in ()).throw(sqlite3.OperationalError("primary sqlite unavailable"))
                if Path(path) == default_db_path(workspace)
                else None,
            ), patch.object(cli_main, "_print_json", side_effect=lambda payload: captured.update(payload)):
                result = cli_main._handle_status(args)

            self.assertEqual(result, 0)
            status = captured["status"]
            assert isinstance(status, dict)
            self.assertTrue(status["retrieval_backends"]["sqlite"]["available"])
            self.assertTrue(status["retrieval_backends"]["sqlite"]["degraded"])
            self.assertEqual(status["retrieval_backends"]["sqlite"]["source_mode"], "fallback_sqlite")
            self.assertTrue(status["backend_runtime"]["fallback_state_index_in_use"])
            self.assertEqual(status["backend_runtime"]["state_index_mode"], "fallback_sqlite")
            self.assertEqual(status["counts"]["activation_logs"], 1)

    def test_cli_status_surfaces_milvus_probe_fallback_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            captured: dict[str, object] = {}
            args = argparse.Namespace(
                workspace=str(workspace),
                limit=3,
                deep_retrieval_check=False,
                output=None,
            )
            local_summary = {
                "backend": "milvus-lite",
                "mode": "local",
                "available": True,
                "runtime_available": True,
                "status": "ready",
                "degraded_reason": None,
                "db_path": str(workspace / "milvus.db"),
                "runtime_probe": {
                    "available": True,
                    "successful_probe_path": "/tmp/m123.sock",
                    "probe_paths": [str(workspace), "/tmp"],
                    "errors": [{"path": str(workspace), "error": "AF_UNIX path too long"}],
                },
                "indexed_entities": None,
            }
            shared_summary = {
                "backend": "milvus-lite",
                "mode": "local",
                "available": True,
                "runtime_available": True,
                "status": "not_initialized",
                "degraded_reason": None,
                "db_path": "/tmp/shared-milvus.db",
                "runtime_probe": {
                    "available": True,
                    "successful_probe_path": "/tmp/m456.sock",
                    "probe_paths": ["/tmp"],
                    "errors": [],
                },
                "indexed_entities": None,
            }

            with patch.object(cli_main, "milvus_backend_summary", side_effect=[local_summary, shared_summary]), patch.object(
                cli_main, "_print_json", side_effect=lambda payload: captured.update(payload)
            ):
                result = cli_main._handle_status(args)

            self.assertEqual(result, 0)
            status = captured["status"]
            assert isinstance(status, dict)
            warning = next(item for item in status["runtime_warnings"] if item["reason"] == "milvus_runtime_probe_fallback")
            self.assertEqual(warning["backend"], "local")
            self.assertEqual(warning["successful_probe_path"], "/tmp/m123.sock")
            self.assertIn("AF_UNIX path too long", warning["errors"][0]["error"])
            self.assertEqual(warning["runtime_state"], "degraded_primary")

    def test_cli_doctor_describes_fallback_sqlite_without_calling_it_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"EXPCAP_STORAGE_PROFILE": "user-cache", "EXPCAP_HOME": str(Path(tmpdir) / "expcap-home")},
        ):
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            fallback_db = fallback_memory_root_for_workspace(workspace) / "index.sqlite3"
            ensure_db(fallback_db)
            log_activation(
                fallback_db,
                {
                    "activation_id": "act_fallback_sqlite_doctor",
                    "workspace": str(workspace),
                    "task_query": "doctor with fallback sqlite",
                    "selected_assets": [],
                    "created_at": "2026-05-11T00:00:00+00:00",
                },
            )
            captured: dict[str, object] = {}
            args = argparse.Namespace(
                workspace=str(workspace),
                limit=3,
                deep_retrieval_check=False,
                output=None,
            )

            with patch.object(
                cli_main,
                "ensure_db",
                side_effect=lambda path: (_ for _ in ()).throw(sqlite3.OperationalError("primary sqlite unavailable"))
                if Path(path) == default_db_path(workspace)
                else None,
            ), patch.object(cli_main, "_print_json", side_effect=lambda payload: captured.update(payload)):
                result = cli_main._handle_doctor(args)

            self.assertEqual(result, 0)
            doctor = captured["doctor"]
            assert isinstance(doctor, dict)
            sqlite_check = next(item for item in doctor["checks"] if item["name"] == "sqlite_index")
            self.assertIn("fallback SQLite is serving state", sqlite_check["summary"])

    def test_dashboard_html_shows_degraded_banner_when_sqlite_unavailable(self) -> None:
        payload = {
            "status": {
                "runtime_warnings": [
                    {
                        "runtime_state": "degraded_primary",
                        "reason": "sqlite_index_unavailable",
                        "error": "readonly sqlite index",
                    }
                ]
            }
        }

        html = cli_main._render_runtime_warnings(payload)

        self.assertIn("Degraded Mode", html)
        self.assertIn("sqlite_index_unavailable", html)
        self.assertIn("readonly sqlite index", html)
        self.assertIn("degraded_primary", html)

    def test_dashboard_html_shows_hard_failure_runtime_warning_summary(self) -> None:
        payload = {
            "status": {
                "runtime_warnings": [
                    {
                        "runtime_state": "hard_failure",
                        "reason": "sqlite_activation_log_unwritable",
                        "error": "all writes failed",
                    }
                ]
            }
        }

        html = cli_main._render_runtime_warnings(payload)

        self.assertIn("failed without a usable fallback", html)
        self.assertIn("hard_failure", html)

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

    def test_cli_validation_plan_emits_ranked_unproven_followups(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)

            db_path = default_db_path(workspace)
            ensure_db(db_path)
            upsert_asset(
                db_path,
                {
                    "asset_id": "pattern_validation_target",
                    "workspace": str(workspace),
                    "asset_type": "pattern",
                    "knowledge_scope": "project",
                    "knowledge_kind": "pattern",
                    "title": "validation target pattern",
                    "content": "use feedback after activation to validate this pattern",
                    "scope": {"level": "workspace", "value": "general-coding-task"},
                    "confidence": 0.91,
                    "status": "active",
                    "review_status": "unproven",
                    "temperature": "neutral",
                    "created_at": "2026-04-28T00:00:00+00:00",
                    "updated_at": "2026-04-28T00:00:00+00:00",
                },
            )
            log_activation(
                db_path,
                {
                    "activation_id": "act_validation_target",
                    "workspace": str(workspace),
                    "task_query": "continue activation feedback validation workflow",
                    "selected_asset_ids": [],
                    "selected_assets": [],
                    "feedback": {"help_signal": "supported_strong"},
                    "created_at": "2026-04-28T00:30:00+00:00",
                },
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "validation-plan",
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
            plan = payload["validation_plan"]
            saved_path = Path(payload["saved_to"])
            saved_plan = json.loads(saved_path.read_text(encoding="utf-8"))

            self.assertEqual(plan["kind"], "unproven_validation_plan")
            self.assertEqual(plan["plan_count"], 1)
            self.assertEqual(plan["summary"]["top_priority_asset_id"], "pattern_validation_target")
            self.assertEqual(plan["items"][0]["rank"], 1)
            self.assertIn("activation", plan["items"][0]["recent_topic_hits"])
            self.assertIn("真实任务", plan["items"][0]["recommended_followup"])
            self.assertEqual(saved_plan["items"][0]["asset_id"], "pattern_validation_target")

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

    def test_build_doctor_payload_warns_when_milvus_probe_requires_fallback_path(self) -> None:
        status_payload = {
            "counts": {
                "traces": 0,
                "episodes": 0,
                "candidates": 0,
                "assets": 0,
                "activation_logs": 0,
            },
            "retrieval_backends": {
                "sqlite": {
                    "available": True,
                    "source_mode": "primary_sqlite",
                    "asset_rows": 0,
                    "candidate_rows": 0,
                    "activation_log_rows": 0,
                },
                "milvus": {
                    "local": {
                        "mode": "local",
                        "status": "ready",
                        "degraded_reason": None,
                        "runtime_probe": {
                            "available": True,
                            "successful_probe_path": "/tmp/m123.sock",
                            "errors": [{"path": "/very/long/path", "error": "AF_UNIX path too long"}],
                        },
                    }
                },
            },
            "milvus_retrieval_effectiveness": {
                "selected_from_milvus": 0,
                "selected_total": 0,
                "activations_with_milvus_selected": 0,
                "activation_count": 0,
                "avg_selected_vector_score": 0.0,
                "milvus_selected_ratio": 0.0,
                "activation_selected_ratio": 0.0,
            },
            "activation_feedback_summary": {"supported_strong": 0, "supported_weak": 0, "pending": 0, "missing": 0},
            "unresolved_activations": [],
            "candidate_review_queue": {"candidate_count": 0, "top_items": []},
            "asset_effectiveness_summary": {"review_status": {"healthy": 0, "watch": 0, "needs_review": 0, "unproven": 0}},
            "asset_review_backlog": {"healthy_count": 0, "total_assets": 0, "unproven_count": 0, "unproven_ratio": 0.0},
            "unproven_validation_queue": {"asset_count": 0, "top_items": []},
            "hook_integration": {
                "integration_mode": cli_main.DEFAULT_INTEGRATION_MODE,
                "recent_events": [],
                "last_event": None,
                "codex": {"files_present": False},
                "claude": {"files_present": False},
            },
        }
        local_lock = {
            "lock_path": "/tmp/local.lock",
            "lock_exists": False,
            "locked": False,
            "lock_error": None,
            "metadata_raw": "",
            "metadata": {},
            "pid_exists": None,
            "age_seconds": None,
            "stale_hint": False,
        }
        shared_lock = dict(local_lock, lock_path="/tmp/shared.lock")

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            cli_main, "_build_status_payload", return_value=status_payload
        ), patch.object(cli_main, "milvus_lock_summary", side_effect=[local_lock, shared_lock]):
            doctor = cli_main._build_doctor_payload(
                workspace=(Path(tmpdir) / "workspace").resolve(),
                limit=3,
                deep_retrieval_check=False,
            )

        probe_check = next(item for item in doctor["checks"] if item["name"] == "local_milvus_probe")
        self.assertEqual(probe_check["status"], "warn")
        self.assertIn("AF_UNIX path too long", probe_check["summary"])
        self.assertIn("/tmp/m123.sock", probe_check["summary"])

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

    def test_cli_install_project_can_enable_claude_hooks_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "install-project",
                    "--workspace",
                    str(workspace),
                    "--integration-mode",
                    "claude-hooks",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)

            self.assertEqual(payload["integration_mode"], "claude-hooks")
            self.assertTrue((workspace / ".claude" / "settings.json").exists())
            self.assertTrue((workspace / ".claude" / "hooks" / "expcap_user_prompt_submit.sh").exists())
            self.assertTrue((workspace / ".claude" / "hooks" / "expcap_stop.sh").exists())

    def test_cli_install_project_can_enable_codex_hooks_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "install-project",
                    "--workspace",
                    str(workspace),
                    "--integration-mode",
                    "codex-hooks",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)

            self.assertEqual(payload["integration_mode"], "codex-hooks")
            self.assertTrue((workspace / ".codex" / "hooks.json").exists())
            self.assertTrue((workspace / ".codex" / "hooks" / "expcap_user_prompt_submit.sh").exists())
            self.assertTrue((workspace / ".codex" / "hooks" / "expcap_stop.sh").exists())
            self.assertTrue((workspace / ".codex" / "hooks" / "expcap_session_start.sh").exists())
            self.assertTrue((workspace / ".codex" / "hooks" / "expcap_pre_tool_use.sh").exists())
            self.assertTrue((workspace / ".codex" / "hooks" / "expcap_permission_request.sh").exists())
            self.assertTrue((workspace / ".codex" / "hooks" / "expcap_post_tool_use.sh").exists())

    def test_expcap_hook_user_prompt_submit_routes_to_auto_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            asset_path = workspace / ".agent-memory" / "assets" / "patterns" / "pattern_hook_001.json"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_hook_001",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "pattern",
                        "title": "hook based activation pattern",
                        "content": "route prompt-submit hooks into auto-start.",
                        "scope": {"level": "workspace", "value": "general-coding-task"},
                        "confidence": 0.88,
                        "status": "active",
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
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "user-prompt-submit",
                    "--host",
                    "claude",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                input=json.dumps({"prompt": "fix prompt routing with hook activation"}, ensure_ascii=False),
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)

            self.assertTrue(payload["continue"])
            self.assertIn("hookSpecificOutput", payload)
            self.assertIn("additionalContext", payload["hookSpecificOutput"])
            self.assertIn("expcap injection context", payload["hookSpecificOutput"]["additionalContext"])
            self.assertIn("Runtime Context", payload["hookSpecificOutput"]["additionalContext"])
            db_path = default_db_path(workspace)
            self.assertEqual(len(list_activation_logs(db_path, workspace=str(workspace.resolve()))), 1)

    def test_expcap_hook_session_start_injects_workspace_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            asset_path = workspace / ".agent-memory" / "assets" / "rules" / "rule_session_start_001.json"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_text(
                json.dumps(
                    {
                        "asset_id": "rule_session_start_001",
                        "workspace": str(workspace),
                        "asset_type": "rule",
                        "knowledge_scope": "project",
                        "knowledge_kind": "rule",
                        "title": "session start workspace convention",
                        "content": "load project conventions when a Codex session starts.",
                        "scope": {"level": "workspace", "value": "general-coding-task"},
                        "confidence": 0.9,
                        "status": "active",
                        "created_at": "2026-05-09T00:00:00+00:00",
                        "updated_at": "2026-05-09T00:00:00+00:00",
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
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "session-start",
                    "--host",
                    "codex",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}, ensure_ascii=False),
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            context = payload["hookSpecificOutput"]["additionalContext"]

            self.assertTrue(payload["continue"])
            self.assertIn("expcap injection context", context)
            self.assertIn("session start workspace convention", context)
            self.assertTrue((workspace / ".agent-memory" / "injections" / "latest.md").exists())
            db_path = default_db_path(workspace)
            activations = list_activation_logs(db_path, workspace=str(workspace.resolve()), limit=5)
            self.assertEqual(len(activations), 1)
            self.assertIn("session start", activations[0]["task_query"])

    def test_expcap_hook_stop_routes_to_auto_finish(self) -> None:
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
                    "complete hook stop flow",
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "stop",
                    "--host",
                    "claude",
                    "--workspace",
                    str(workspace),
                    "--result-status",
                    "success",
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                input="{}",
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)

            self.assertTrue(payload["continue"])
            traces_dir = workspace / ".agent-memory" / "traces" / "bundles"
            self.assertTrue(any(traces_dir.glob("trace_*.json")))

    def test_expcap_hook_stop_includes_recent_codex_tool_evidence(self) -> None:
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
                    "complete codex lifecycle evidence flow",
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )

            subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "post-tool-use",
                    "--host",
                    "codex",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                input=json.dumps(
                    {
                        "hook_event_name": "PostToolUse",
                        "cwd": str(workspace),
                        "tool_name": "Bash",
                        "tool_input": {"command": "python -m pytest tests/test_lifecycle.py"},
                        "tool_response": {"exit_code": 1, "stderr": "AssertionError: lifecycle evidence missing"},
                    },
                    ensure_ascii=False,
                ),
                check=True,
                capture_output=True,
                text=True,
            )

            subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "stop",
                    "--host",
                    "codex",
                    "--workspace",
                    str(workspace),
                    "--result-status",
                    "success",
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                input=json.dumps({"task": "complete codex lifecycle evidence flow"}, ensure_ascii=False),
                check=True,
                capture_output=True,
                text=True,
            )

            trace_path = next((workspace / ".agent-memory" / "traces" / "bundles").glob("trace_*.json"))
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            events = trace["events"]
            self.assertIn(
                {"type": "command", "content": "python -m pytest tests/test_lifecycle.py", "important": True},
                events,
            )
            self.assertIn(
                {"type": "error", "content": "AssertionError: lifecycle evidence missing", "important": True},
                events,
            )
            self.assertNotIn(
                {"type": "command", "content": "progressive-recall", "important": True},
                events,
            )

    def test_expcap_hook_post_tool_use_injects_progressive_recall_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            env = {**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"}

            with patch.dict(os.environ, {"EXPCAP_STORAGE_PROFILE": "local"}):
                db_path = default_db_path(workspace)
                ensure_db(db_path)
                upsert_asset(
                    db_path,
                    {
                        "asset_id": "pattern_hook_progressive_001",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "pattern",
                        "title": "hook progressive WebSocketTimeoutError repair",
                        "content": "When WebSocketTimeoutError appears in tests/test_websocket.py after a tool run, trigger continuous runtime recall and focus on the new stderr signal.",
                        "scope": {"level": "workspace", "value": "general-coding-task"},
                        "source_episode_ids": ["ep_hook_progressive_001"],
                        "source_candidate_ids": ["cand_hook_progressive_001"],
                        "confidence": 0.84,
                        "status": "active",
                        "review_status": "healthy",
                        "temperature": "warm",
                        "created_at": "2026-05-09T00:00:00+00:00",
                        "updated_at": "2026-05-09T00:00:00+00:00",
                    },
                )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "post-tool-use",
                    "--host",
                    "codex",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                env=env,
                input=json.dumps(
                    {
                        "hook_event_name": "PostToolUse",
                        "cwd": str(workspace),
                        "tool_name": "Bash",
                        "tool_input": {"command": "python -m pytest tests/test_websocket.py"},
                        "tool_response": {"exit_code": 1, "stderr": "WebSocketTimeoutError in tests/test_websocket.py"},
                    },
                    ensure_ascii=False,
                ),
                check=True,
                capture_output=True,
                text=True,
            )

            payload = json.loads(completed.stdout)
            context = payload["hookSpecificOutput"]["additionalContext"]
            latest = json.loads((workspace / ".agent-memory" / "hooks" / "latest.json").read_text(encoding="utf-8"))
            view_path = next((workspace / ".agent-memory" / "views").glob("*progressive.json"))
            view = json.loads(view_path.read_text(encoding="utf-8"))

            self.assertIn("continuous_runtime_recall_injection", context)
            self.assertIn("hook progressive WebSocketTimeoutError repair", context)
            self.assertEqual(latest["event"], "post-tool-use")
            self.assertEqual(latest["status"], "progressive-recall")
            self.assertIn("selected_count=1", latest["result_summary"])
            self.assertEqual(view["progressive_recall"]["injection_layer"], "continuous_runtime_recall_injection")

    def test_expcap_hook_pre_tool_use_blocks_destructive_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "pre-tool-use",
                    "--host",
                    "codex",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                input=json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "cwd": str(workspace),
                        "tool_name": "Bash",
                        "tool_input": {"command": "git reset --hard HEAD"},
                    },
                    ensure_ascii=False,
                ),
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            hook_output = payload["hookSpecificOutput"]
            self.assertEqual(hook_output["hookEventName"], "PreToolUse")
            self.assertEqual(hook_output["permissionDecision"], "deny")
            self.assertIn("destructive", hook_output["permissionDecisionReason"].lower())

            latest = json.loads((workspace / ".agent-memory" / "hooks" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["event"], "pre-tool-use")
            self.assertEqual(latest["status"], "blocked")

    def test_expcap_hook_pre_tool_use_blocks_runtime_data_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "pre-tool-use",
                    "--host",
                    "codex",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                input=json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "cwd": str(workspace),
                        "tool_name": "Bash",
                        "tool_input": {"command": "git add ~/.expcap/projects/demo/index.sqlite3"},
                    },
                    ensure_ascii=False,
                ),
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            hook_output = payload["hookSpecificOutput"]
            self.assertEqual(hook_output["hookEventName"], "PreToolUse")
            self.assertEqual(hook_output["permissionDecision"], "deny")
            self.assertIn("runtime data", hook_output["permissionDecisionReason"].lower())

            latest = json.loads((workspace / ".agent-memory" / "hooks" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["event"], "pre-tool-use")
            self.assertEqual(latest["status"], "blocked")

    def test_expcap_hook_pre_tool_use_allows_project_config_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "pre-tool-use",
                    "--host",
                    "codex",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                input=json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "cwd": str(workspace),
                        "tool_name": "Bash",
                        "tool_input": {"command": "git add .expcap-project.json"},
                    },
                    ensure_ascii=False,
                ),
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.stdout, "")
            latest = json.loads((workspace / ".agent-memory" / "hooks" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["event"], "pre-tool-use")
            self.assertEqual(latest["status"], "success")

    def test_expcap_hook_pre_tool_use_allows_safe_command_quietly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "pre-tool-use",
                    "--host",
                    "codex",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                input=json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "cwd": str(workspace),
                        "tool_name": "Bash",
                        "tool_input": {"command": "python3 -m unittest tests.test_install_project"},
                    },
                    ensure_ascii=False,
                ),
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.stdout, "")
            latest = json.loads((workspace / ".agent-memory" / "hooks" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["event"], "pre-tool-use")
            self.assertEqual(latest["status"], "success")

    def test_expcap_hook_permission_request_denies_destructive_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "permission-request",
                    "--host",
                    "codex",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                input=json.dumps(
                    {
                        "hook_event_name": "PermissionRequest",
                        "cwd": str(workspace),
                        "tool_name": "Bash",
                        "tool_input": {
                            "command": "git clean -xdf",
                            "description": "Escalate to clean untracked files.",
                        },
                    },
                    ensure_ascii=False,
                ),
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            decision = payload["hookSpecificOutput"]["decision"]
            self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "PermissionRequest")
            self.assertEqual(decision["behavior"], "deny")
            self.assertIn("destructive", decision["message"].lower())

            latest = json.loads((workspace / ".agent-memory" / "hooks" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["event"], "permission-request")
            self.assertEqual(latest["status"], "blocked")

    def test_expcap_hook_permission_request_declines_safe_request_quietly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "permission-request",
                    "--host",
                    "codex",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                input=json.dumps(
                    {
                        "hook_event_name": "PermissionRequest",
                        "cwd": str(workspace),
                        "tool_name": "Bash",
                        "tool_input": {
                            "command": "python3 -m unittest discover -s tests -v",
                            "description": "Run the project test suite.",
                        },
                    },
                    ensure_ascii=False,
                ),
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.stdout, "")
            latest = json.loads((workspace / ".agent-memory" / "hooks" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["event"], "permission-request")
            self.assertEqual(latest["status"], "success")
            self.assertEqual(latest["reason"], "Run the project test suite.")

    def test_expcap_hook_stop_reads_status_fields_from_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            asset_path = workspace / ".agent-memory" / "assets" / "patterns" / "pattern_stop_payload_001.json"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_stop_payload_001",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "pattern",
                        "title": "codex stop payload mapping",
                        "content": "map stop hook payload status fields into auto-finish feedback.",
                        "scope": {"level": "workspace", "value": "general-coding-task"},
                        "confidence": 0.88,
                        "status": "active",
                        "created_at": "2026-05-08T00:00:00+00:00",
                        "updated_at": "2026-05-08T00:00:00+00:00",
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
                    "--workspace",
                    str(workspace),
                    "--task",
                    "complete codex stop payload mapping",
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "stop",
                    "--host",
                    "codex",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                input=json.dumps(
                    {
                        "hook_event_name": "Stop",
                        "cwd": str(workspace),
                        "task": "complete codex stop payload mapping",
                        "verification_status": "passed",
                        "result_status": "success",
                        "result_summary": "Codex stop payload carried status fields.",
                    },
                    ensure_ascii=False,
                ),
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["continue"])

            db_path = default_db_path(workspace)
            activations = list_activation_logs(db_path, workspace=str(workspace.resolve()), limit=5)
            activation = next(item for item in activations if item.get("task_query") == "complete codex stop payload mapping")
            self.assertEqual(activation.get("feedback", {}).get("help_signal"), "supported_strong")
            self.assertIn("Codex stop payload carried status fields", activation.get("feedback", {}).get("feedback_summary", ""))

    def test_expcap_hook_user_prompt_submit_skips_duplicate_within_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            for _ in range(2):
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(REPO_ROOT / "scripts" / "expcap-hook"),
                        "user-prompt-submit",
                        "--host",
                        "claude",
                        "--workspace",
                        str(workspace),
                    ],
                    cwd=REPO_ROOT,
                    env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local", "EXPCAP_HOOK_COOLDOWN_MINUTES": "60"},
                    input=json.dumps({"prompt": "stabilize duplicate hook prompt handling"}, ensure_ascii=False),
                    check=True,
                    capture_output=True,
                    text=True,
                )
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["continue"])
            db_path = default_db_path(workspace)
            self.assertEqual(len(list_activation_logs(db_path, workspace=str(workspace.resolve()))), 1)

            status = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "status",
                    "--workspace",
                    str(workspace),
                    "--limit",
                    "5",
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )
            status_payload = json.loads(status.stdout)["status"]
            self.assertEqual(status_payload["hook_integration"]["last_event"]["status"], "skipped")
            self.assertIn("duplicate_cooldown", status_payload["hook_integration"]["last_event"]["reason"])

    def test_expcap_hook_stop_skips_when_user_requests_no_save(self) -> None:
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
                    "collect context but 不要记录 this flow",
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "stop",
                    "--host",
                    "claude",
                    "--workspace",
                    str(workspace),
                    "--result-summary",
                    "用户明确说不要记录这次结果",
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                input="{}",
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["continue"])
            traces_dir = workspace / ".agent-memory" / "traces" / "bundles"
            self.assertFalse(traces_dir.exists() and any(traces_dir.glob("trace_*.json")))

            status = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "status",
                    "--workspace",
                    str(workspace),
                    "--limit",
                    "5",
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )
            status_payload = json.loads(status.stdout)["status"]
            self.assertEqual(status_payload["hook_integration"]["last_event"]["status"], "skipped")
            self.assertEqual(status_payload["hook_integration"]["last_event"]["reason"], "explicit_no_save_signal")

    def test_status_and_doctor_report_claude_hook_activity(self) -> None:
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
                    "--integration-mode",
                    "claude-hooks",
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )

            subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "user-prompt-submit",
                    "--host",
                    "claude",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                input=json.dumps({"prompt": "inspect hook health visibility"}, ensure_ascii=False),
                check=True,
                capture_output=True,
                text=True,
            )

            status = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "status",
                    "--workspace",
                    str(workspace),
                    "--limit",
                    "5",
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )
            status_payload = json.loads(status.stdout)["status"]
            hook_integration = status_payload["hook_integration"]
            self.assertEqual(hook_integration["integration_mode"], "claude-hooks")
            self.assertTrue(hook_integration["claude"]["files_present"])
            self.assertGreaterEqual(hook_integration["event_count"], 1)
            self.assertEqual(hook_integration["last_event"]["status"], "success")

            doctor = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "doctor",
                    "--workspace",
                    str(workspace),
                    "--limit",
                    "5",
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )
            doctor_payload = json.loads(doctor.stdout)["doctor"]
            hook_check = next(item for item in doctor_payload["checks"] if item["name"] == "hook_runtime")
            self.assertEqual(hook_check["status"], "pass")
            self.assertIn("Claude hook integration is configured", hook_check["summary"])

    def test_status_and_doctor_report_codex_hook_activity(self) -> None:
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
                    "--integration-mode",
                    "codex-hooks",
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )

            subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "expcap-hook"),
                    "user-prompt-submit",
                    "--host",
                    "codex",
                    "--workspace",
                    str(workspace),
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                input=json.dumps(
                    {
                        "hook_event_name": "UserPromptSubmit",
                        "cwd": str(workspace),
                        "prompt": "inspect codex hook health visibility",
                    },
                    ensure_ascii=False,
                ),
                check=True,
                capture_output=True,
                text=True,
            )

            status = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "status",
                    "--workspace",
                    str(workspace),
                    "--limit",
                    "5",
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )
            status_payload = json.loads(status.stdout)["status"]
            hook_integration = status_payload["hook_integration"]
            self.assertEqual(hook_integration["integration_mode"], "codex-hooks")
            self.assertTrue(hook_integration["codex"]["files_present"])
            self.assertGreaterEqual(hook_integration["event_count"], 1)
            self.assertEqual(hook_integration["last_event"]["host"], "codex")
            self.assertEqual(hook_integration["last_event"]["status"], "success")

            doctor = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "doctor",
                    "--workspace",
                    str(workspace),
                    "--limit",
                    "5",
                ],
                cwd=REPO_ROOT,
                env={**dict(os.environ), "EXPCAP_STORAGE_PROFILE": "local"},
                check=True,
                capture_output=True,
                text=True,
            )
            doctor_payload = json.loads(doctor.stdout)["doctor"]
            hook_check = next(item for item in doctor_payload["checks"] if item["name"] == "hook_runtime")
            self.assertEqual(hook_check["status"], "pass")
            self.assertIn("Codex hook integration is configured", hook_check["summary"])

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

    def test_cli_auto_finish_does_not_feedback_unrelated_pending_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"EXPCAP_STORAGE_PROFILE": "local"}):
            workspace = (Path(tmpdir) / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)

            asset_path = workspace / ".agent-memory" / "assets" / "patterns" / "pattern_task_match_001.json"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_text(
                json.dumps(
                    {
                        "asset_id": "pattern_task_match_001",
                        "workspace": str(workspace),
                        "asset_type": "pattern",
                        "knowledge_scope": "project",
                        "knowledge_kind": "pattern",
                        "title": "task matched activation feedback",
                        "content": "only attach auto-finish feedback to an activation with the same task query.",
                        "scope": {"level": "workspace", "value": "general-coding-task"},
                        "confidence": 0.9,
                        "status": "active",
                        "created_at": "2026-04-13T00:00:00+00:00",
                        "updated_at": "2026-04-13T00:00:00+00:00",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            for task in ("old unrelated task", "current task"):
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "runtime.cli",
                        "auto-start",
                        "--task",
                        task,
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
                    "feedback",
                    "--workspace",
                    str(workspace),
                    "--activation-id",
                    "act_current-task",
                    "--help-signal",
                    "supported_strong",
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
                    "current task",
                    "--verification-status",
                    "passed",
                    "--result-status",
                    "success",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(auto_finish.stdout)
            self.assertIsNone(payload["activation_feedback"])

            db_path = default_db_path(workspace)
            old_activation = next(
                item
                for item in list_activation_logs(db_path, workspace=str(workspace))
                if item["task_query"] == "old unrelated task"
            )
            self.assertNotIn("feedback", old_activation)

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
            self.assertIn("knowledge_kind_summary", payload["review_queue"])
            self.assertGreaterEqual(payload["review_queue"]["knowledge_kind_summary"]["local_prior_count"], 0)
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

    def test_cli_review_candidates_filters_and_prioritizes_high_priority_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            _write_candidate(
                workspace / ".agent-memory" / "candidates" / "cand_pattern_filter_001.json",
                workspace=workspace,
                candidate_id="cand_pattern_filter_001",
                status="new",
                promotion_readiness="unknown",
                help_signal=None,
            )
            _write_candidate(
                workspace / ".agent-memory" / "candidates" / "cand_preference_filter_001.json",
                workspace=workspace,
                candidate_id="cand_preference_filter_001",
                status="new",
                promotion_readiness="unknown",
                help_signal=None,
                knowledge_kind="preference",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "review-candidates",
                    "--workspace",
                    str(workspace),
                    "--status",
                    "new",
                    "--knowledge-kind",
                    "preference",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            items = payload["review_queue"]["items"]

            self.assertEqual(payload["candidate_count"], 1)
            self.assertEqual(items[0]["candidate_id"], "cand_preference_filter_001")
            self.assertEqual(items[0]["knowledge_kind"], "preference")
            self.assertEqual(items[0]["suggested_action"], "review")
            self.assertTrue(any("高优先级本地先验" in item for item in items[0]["reasons"]))

    def test_cli_save_prior_creates_active_high_priority_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runtime.cli",
                    "save-prior",
                    "--workspace",
                    str(workspace),
                    "--knowledge-kind",
                    "dont_repeat",
                    "--title",
                    "不要重复询问存储 profile",
                    "--content",
                    "不要重复询问存储 profile；本项目默认使用 user-cache 和 EXPCAP_HOME=$HOME/.expcap。",
                    "--source-note",
                    "user explicitly asked not to repeat stable setup preferences",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            asset_path = Path(payload["asset"]["path"])
            asset = json.loads(asset_path.read_text(encoding="utf-8"))

            self.assertEqual(asset["knowledge_kind"], "dont_repeat")
            self.assertEqual(asset["asset_type"], "rule")
            self.assertEqual(asset["status"], "active")
            self.assertEqual(asset["review_status"], "healthy")
            self.assertEqual(asset["temperature"], "warm")

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
            self.assertEqual(status_payload["knowledge_kind_summary"]["assets"]["high_priority_count"], 1)

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
            self.assertIn("knowledge_kind_summary", payload)
            self.assertIn("knowledge_kind_summary", payload["candidate_review_queue"])
            self.assertIn("injection_policy_summary", payload)
            self.assertIn("injection_channel_counts", payload["recent_activations"][0])
            self.assertEqual(payload["candidate_review_queue"]["top_items"][0]["status"], "needs_review")
            self.assertIn("knowledge_kind", payload["recent_candidates"][0])
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
