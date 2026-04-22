import argparse
import json
from pathlib import Path
from typing import Any

from runtime.backends import resolve_backend_config
from runtime.core.engine import (
    activate_assets,
    apply_asset_effectiveness,
    apply_candidate_promotion_feedback,
    build_candidate_review_queue,
    build_trace_bundle,
    explain_object,
    extract_candidates,
    now_utc,
    promote_candidate,
    review_trace_bundle,
    should_promote_candidate,
)
from runtime.core.project_install import install_project_agents
from runtime.storage.fs_store import (
    default_activation_view_path,
    default_db_path,
    default_milvus_db_path,
    default_trace_bundle_path,
    iter_json_objects,
    load_json,
    memory_root_for_workspace,
    save_json,
    default_shared_asset_path,
    shared_db_path,
    shared_memory_root,
    shared_milvus_db_path,
    workspace_from_payload,
)
from runtime.storage.milvus_store import (
    milvus_available,
    milvus_backend_summary,
    sync_assets_directory,
    upsert_asset_vector,
)
from runtime.storage.sqlite_store import (
    ensure_db,
    find_latest_activation,
    get_asset,
    get_candidate,
    list_activation_logs,
    list_assets,
    list_candidates,
    log_activation,
    record_activation_feedback,
    summarize_asset_feedback,
    touch_assets_last_used,
    upsert_asset,
    upsert_candidate,
    upsert_episode,
    upsert_trace,
)

ALL_CANDIDATE_STATUSES = ("new", "needs_review", "approved", "rejected", "promoted")
DEFAULT_REVIEW_QUEUE_STATUSES = ("needs_review", "approved", "new")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="expcap",
        description="Local-first runtime for agent experience capitalization.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Create and persist a trace bundle from task facts.")
    ingest.add_argument("--workspace", required=True, help="Workspace path for the task.")
    ingest.add_argument("--task", required=True, help="Task summary used as the trace hint.")
    ingest.add_argument("--user-request", help="Original user request. Defaults to --task.")
    ingest.add_argument("--constraint", dest="constraints", action="append", default=[], help="Explicit task constraint.")
    ingest.add_argument("--command", dest="commands", action="append", default=[], help="Important command executed during the task.")
    ingest.add_argument("--error", dest="errors", action="append", default=[], help="Important error observed during the task.")
    ingest.add_argument("--file-changed", dest="files_changed", action="append", default=[], help="Changed file path. May be passed multiple times.")
    ingest.add_argument("--verification-status", default="unknown", help="Verification status, e.g. passed/failed.")
    ingest.add_argument("--verification-summary", help="Verification summary.")
    ingest.add_argument("--result-status", default="success", help="Result status, e.g. success/partial/failed.")
    ingest.add_argument("--result-summary", help="Result summary.")
    ingest.add_argument("--host", default="codex", help="Host name. Defaults to codex.")
    ingest.add_argument("--session-id", help="Optional host session id.")
    ingest.add_argument("--trace-id", help="Optional explicit trace id.")
    ingest.add_argument("--output", help="Optional output path for the trace JSON.")

    auto_start = subparsers.add_parser(
        "auto-start",
        help="Run the default start-of-task experience get flow.",
    )
    auto_start.add_argument("--task", required=True, help="Current task description.")
    auto_start.add_argument("--workspace", required=True, help="Workspace path for the task.")
    auto_start.add_argument(
        "--constraint",
        dest="constraints",
        action="append",
        default=[],
        help="Explicit task constraint. May be passed multiple times.",
    )
    auto_start.add_argument("--output", help="Optional path for the activation view JSON.")

    auto_finish = subparsers.add_parser(
        "auto-finish",
        help="Run the default end-of-task save flow from trace to optional promoted asset.",
    )
    auto_finish.add_argument("--workspace", required=True, help="Workspace path for the task.")
    auto_finish.add_argument("--task", required=True, help="Task summary used as the trace hint.")
    auto_finish.add_argument("--user-request", help="Original user request. Defaults to --task.")
    auto_finish.add_argument("--constraint", dest="constraints", action="append", default=[], help="Explicit task constraint.")
    auto_finish.add_argument("--command", dest="commands", action="append", default=[], help="Important command executed during the task.")
    auto_finish.add_argument("--error", dest="errors", action="append", default=[], help="Important error observed during the task.")
    auto_finish.add_argument("--file-changed", dest="files_changed", action="append", default=[], help="Changed file path. May be passed multiple times.")
    auto_finish.add_argument("--verification-status", default="unknown", help="Verification status, e.g. passed/failed.")
    auto_finish.add_argument("--verification-summary", help="Verification summary.")
    auto_finish.add_argument("--result-status", default="success", help="Result status, e.g. success/partial/failed.")
    auto_finish.add_argument("--result-summary", help="Result summary.")
    auto_finish.add_argument("--host", default="codex", help="Host name. Defaults to codex.")
    auto_finish.add_argument("--session-id", help="Optional host session id.")
    auto_finish.add_argument("--trace-id", help="Optional explicit trace id.")
    auto_finish.add_argument(
        "--promote-threshold",
        type=float,
        default=0.70,
        help="Minimum per-score threshold required for auto promotion.",
    )
    auto_finish.add_argument(
        "--no-promote",
        action="store_true",
        help="Disable auto promotion even if the candidate meets the threshold.",
    )
    auto_finish.add_argument(
        "--knowledge-scope",
        choices=["project", "cross-project"],
        default="project",
        help="Scope to assign when auto-promoting an asset.",
    )
    auto_finish.add_argument(
        "--knowledge-kind",
        choices=["pattern", "anti_pattern", "rule", "context", "checklist"],
        help="Optional knowledge kind override for promoted assets.",
    )

    install_project = subparsers.add_parser(
        "install-project",
        help="Non-destructively integrate expcap into another project's agent instruction files.",
    )
    install_project.add_argument("--workspace", required=True, help="Target project workspace.")
    install_project.add_argument(
        "--include-claude",
        action="store_true",
        help="Also append an expcap block to CLAUDE.md for Claude Code users.",
    )

    sync_milvus = subparsers.add_parser(
        "sync-milvus",
        help="Backfill local and optional shared assets into Milvus Lite indexes.",
    )
    sync_milvus.add_argument("--workspace", required=True, help="Workspace path to sync.")
    sync_milvus.add_argument(
        "--include-shared",
        action="store_true",
        help="Also sync shared cross-project assets from ~/.codex/expcap-memory/assets.",
    )

    review = subparsers.add_parser("review", help="Generate an episode from a trace bundle.")
    review.add_argument("--input", required=True, help="Path to a trace bundle JSON file.")
    review.add_argument("--output", help="Optional path for the generated episode JSON.")

    extract = subparsers.add_parser("extract", help="Generate candidates from an episode.")
    extract.add_argument("--episode", required=True, help="Path to an episode JSON file.")
    extract.add_argument(
        "--output-dir",
        help="Optional directory for generated candidate JSON files.",
    )

    promote = subparsers.add_parser("promote", help="Promote one candidate into an asset.")
    promote.add_argument("--candidate", required=True, help="Path to a candidate JSON file.")
    promote.add_argument(
        "--output",
        help="Optional path for the promoted asset JSON file.",
    )
    promote.add_argument(
        "--knowledge-scope",
        choices=["project", "cross-project"],
        default="project",
        help="Whether to store this asset in the project layer or shared cross-project layer.",
    )
    promote.add_argument(
        "--knowledge-kind",
        choices=["pattern", "anti_pattern", "rule", "context", "checklist"],
        help="Optional knowledge kind override.",
    )

    activate = subparsers.add_parser("activate", help="Build an activation view for a task.")
    activate.add_argument("--task", required=True, help="Current task description.")
    activate.add_argument(
        "--workspace",
        required=True,
        help="Workspace path used to resolve the local memory store.",
    )
    activate.add_argument(
        "--constraints",
        action="append",
        default=[],
        help="Explicit task constraint. May be passed multiple times.",
    )
    activate.add_argument(
        "--assets-dir",
        help="Optional asset directory. Defaults to <workspace>/.agent-memory/assets.",
    )
    activate.add_argument(
        "--candidates-dir",
        help="Optional candidate directory used as a fallback source.",
    )
    activate.add_argument("--output", help="Optional path for the activation view JSON.")

    explain = subparsers.add_parser("explain", help="Explain a stored object.")
    explain.add_argument(
        "--input",
        required=True,
        help="Path to an episode, candidate, asset, or activation view JSON file.",
    )

    review_candidates = subparsers.add_parser(
        "review-candidates",
        help="Build the candidate review queue or apply manual review actions.",
    )
    review_candidates.add_argument("--workspace", required=True, help="Workspace path for the queue.")
    review_candidates.add_argument(
        "--status",
        dest="statuses",
        action="append",
        choices=list(ALL_CANDIDATE_STATUSES),
        help="Candidate statuses to include. Defaults to needs_review, approved, and new.",
    )
    review_candidates.add_argument(
        "--action",
        choices=["approve", "reject", "promote"],
        help="Optional review action to apply to one candidate before rebuilding the queue.",
    )
    review_candidates.add_argument(
        "--candidate-id",
        help="Candidate id used together with --action.",
    )
    review_candidates.add_argument(
        "--knowledge-scope",
        choices=["project", "cross-project"],
        default="project",
        help="Knowledge scope used when --action promote is selected.",
    )
    review_candidates.add_argument(
        "--knowledge-kind",
        choices=["pattern", "anti_pattern", "rule", "context", "checklist"],
        help="Optional knowledge kind override used when --action promote is selected.",
    )
    review_candidates.add_argument("--output", help="Optional output path for the review queue JSON.")

    status = subparsers.add_parser(
        "status",
        help="Build a short test readiness and usage summary for one workspace.",
    )
    status.add_argument("--workspace", required=True, help="Workspace path to summarize.")
    status.add_argument(
        "--limit",
        type=int,
        default=5,
        help="How many recent items to include per section. Defaults to 5.",
    )
    status.add_argument("--output", help="Optional output path for the status JSON.")
    status.add_argument(
        "--deep-retrieval-check",
        action="store_true",
        help="Open retrieval backends for deeper health checks. Defaults to lightweight checks only.",
    )

    return parser


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _infer_activation_help_signal(*, verification_status: str, result_status: str) -> str:
    if verification_status == "passed" and result_status == "success":
        return "supported_strong"
    if verification_status == "passed" or result_status == "success":
        return "supported_weak"
    return "unclear"


def _candidate_path_for_workspace(workspace: Path, candidate_id: str) -> Path:
    return memory_root_for_workspace(workspace) / "candidates" / f"{candidate_id}.json"


def _load_candidate_for_workspace(workspace: Path, db_path: Path, candidate_id: str) -> tuple[dict[str, Any], Path]:
    candidate = get_candidate(db_path, candidate_id=candidate_id)
    candidate_path = _candidate_path_for_workspace(workspace, candidate_id)
    if candidate is None and candidate_path.exists():
        candidate = load_json(candidate_path)
    if candidate is None:
        raise SystemExit(f"candidate not found: {candidate_id}")
    return candidate, candidate_path


def _persist_candidate(workspace: Path, db_path: Path, candidate: dict[str, Any]) -> Path:
    candidate_path = _candidate_path_for_workspace(workspace, candidate["candidate_id"])
    save_json(candidate_path, candidate)
    upsert_candidate(db_path, candidate)
    return candidate_path


def _persist_promoted_asset(
    candidate: dict[str, Any],
    *,
    knowledge_scope: str,
    knowledge_kind: str | None,
) -> tuple[dict[str, Any], Path]:
    workspace = workspace_from_payload(candidate, Path.cwd())
    asset = promote_candidate(
        candidate,
        knowledge_scope=knowledge_scope,
        knowledge_kind=knowledge_kind,
    )
    output_path = (
        default_shared_asset_path(asset)
        if knowledge_scope == "cross-project"
        else memory_root_for_workspace(workspace) / "assets" / f"{asset['asset_type']}s" / f"{asset['asset_id']}.json"
    )
    asset_db_path = shared_db_path() if knowledge_scope == "cross-project" else default_db_path(workspace)
    ensure_db(asset_db_path)
    save_json(output_path, asset)
    upsert_asset(asset_db_path, asset)
    upsert_asset_vector(
        shared_milvus_db_path() if knowledge_scope == "cross-project" else default_milvus_db_path(workspace),
        asset,
    )
    return asset, output_path


def _apply_review_candidate_action(
    *,
    args: argparse.Namespace,
    workspace: Path,
    db_path: Path,
) -> dict[str, Any] | None:
    if not args.action:
        return None
    if not args.candidate_id:
        raise SystemExit("--candidate-id is required when --action is used")

    candidate, _ = _load_candidate_for_workspace(workspace, db_path, args.candidate_id)
    previous_status = candidate.get("status")
    reviewed_at = now_utc()

    if args.action == "approve":
        candidate["status"] = "approved"
    elif args.action == "reject":
        candidate["status"] = "rejected"
    else:
        candidate["status"] = "promoted"

    decision = {
        "action": args.action,
        "review_source": "review-candidates",
        "reviewed_at": reviewed_at,
        "previous_status": previous_status,
        "status_after": candidate["status"],
    }
    if args.action == "promote":
        decision["knowledge_scope"] = args.knowledge_scope
        decision["knowledge_kind"] = args.knowledge_kind or candidate.get("knowledge_kind")

    history = list(candidate.get("review_history") or [])
    history.append(decision)
    candidate["review_history"] = history
    candidate["review_decision"] = decision
    candidate["updated_at"] = reviewed_at
    candidate_path = _persist_candidate(workspace, db_path, candidate)

    asset_result = None
    if args.action == "promote":
        asset, asset_path = _persist_promoted_asset(
            candidate,
            knowledge_scope=args.knowledge_scope,
            knowledge_kind=args.knowledge_kind,
        )
        asset_result = {
            "asset_id": asset["asset_id"],
            "path": str(asset_path),
            "knowledge_scope": asset["knowledge_scope"],
            "knowledge_kind": asset["knowledge_kind"],
        }

    return {
        "candidate_id": candidate["candidate_id"],
        "candidate_path": str(candidate_path),
        "action": args.action,
        "previous_status": previous_status,
        "status": candidate["status"],
        "reviewed_at": reviewed_at,
        "asset": asset_result,
    }


def _handle_ingest(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    trace = build_trace_bundle(
        workspace=workspace,
        task=args.task,
        user_request=args.user_request,
        constraints=args.constraints,
        commands=args.commands,
        errors=args.errors,
        files_changed=args.files_changed,
        verification_status=args.verification_status,
        verification_summary=args.verification_summary,
        result_status=args.result_status,
        result_summary=args.result_summary,
        host=args.host,
        session_id=args.session_id,
        trace_id=args.trace_id,
    )
    output_path = Path(args.output) if args.output else default_trace_bundle_path(workspace, trace)
    save_json(output_path, trace)
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    upsert_trace(db_path, trace)
    _print_json({"saved_to": str(output_path), "trace_id": trace["trace_id"], "trace_bundle": trace})
    return 0


def _handle_auto_start(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    view = activate_assets(
        task=args.task,
        workspace=workspace,
        constraints=args.constraints,
        assets_dir=workspace / ".agent-memory" / "assets",
        candidates_dir=workspace / ".agent-memory" / "candidates",
        db_path=db_path,
    )
    output_path = Path(args.output) if args.output else default_activation_view_path(workspace, view)
    save_json(output_path, view)
    log_activation(db_path, view)
    touch_assets_last_used(
        db_path,
        [item["asset_id"] for item in view.get("selected_assets", [])],
        view["created_at"],
    )
    _print_json(
        {
            "saved_to": str(output_path),
            "activation_id": view["activation_id"],
            "selected_count": len(view.get("selected_assets", [])),
            "activation_view": view,
        }
    )
    return 0


def _handle_review(args: argparse.Namespace) -> int:
    trace = load_json(Path(args.input))
    episode = review_trace_bundle(trace)
    workspace = workspace_from_payload(trace, Path.cwd())
    memory_root = memory_root_for_workspace(workspace)
    output_path = Path(args.output) if args.output else memory_root / "episodes" / f"{episode['episode_id']}.json"
    save_json(output_path, episode)
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    upsert_episode(db_path, episode)
    _print_json({"saved_to": str(output_path), "episode_id": episode["episode_id"], "episode": episode})
    return 0


def _handle_install_project(args: argparse.Namespace) -> int:
    result = install_project_agents(Path(args.workspace), include_claude=args.include_claude)
    _print_json(result)
    return 0


def _handle_auto_finish(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    memory_root = memory_root_for_workspace(workspace)
    db_path = default_db_path(workspace)
    ensure_db(db_path)

    trace = build_trace_bundle(
        workspace=workspace,
        task=args.task,
        user_request=args.user_request,
        constraints=args.constraints,
        commands=args.commands,
        errors=args.errors,
        files_changed=args.files_changed,
        verification_status=args.verification_status,
        verification_summary=args.verification_summary,
        result_status=args.result_status,
        result_summary=args.result_summary,
        host=args.host,
        session_id=args.session_id,
        trace_id=args.trace_id,
    )
    trace_path = default_trace_bundle_path(workspace, trace)
    save_json(trace_path, trace)
    upsert_trace(db_path, trace)

    episode = review_trace_bundle(trace)
    episode_path = memory_root / "episodes" / f"{episode['episode_id']}.json"
    save_json(episode_path, episode)
    upsert_episode(db_path, episode)

    activation_feedback = None
    latest_activation = find_latest_activation(
        db_path,
        workspace=str(workspace),
        unresolved_only=True,
    )
    if latest_activation and latest_activation.get("selected_assets"):
        feedback = {
            "help_signal": _infer_activation_help_signal(
                verification_status=args.verification_status,
                result_status=args.result_status,
            ),
            "signal_source": "auto_finish_outcome",
            "feedback_summary": (
                args.result_summary
                or args.verification_summary
                or f"result={args.result_status}, verification={args.verification_status}"
            ),
            "feedback_at": episode.get("created_at"),
            "trace_id": trace["trace_id"],
            "episode_id": episode["episode_id"],
        }
        updated_activation = record_activation_feedback(
            db_path,
            activation_id=latest_activation["activation_id"],
            feedback=feedback,
        )
        activation_view_path = memory_root / "views" / f"{latest_activation['activation_id']}.json"
        if updated_activation and activation_view_path.exists():
            save_json(activation_view_path, updated_activation)
        if updated_activation:
            linked_asset_ids = updated_activation.get("selected_asset_ids") or [
                item["asset_id"]
                for item in updated_activation.get("selected_assets", [])
                if isinstance(item, dict) and item.get("asset_id")
            ]
            feedback_stats = summarize_asset_feedback(
                db_path,
                asset_ids=linked_asset_ids,
            )
            for selected_asset in updated_activation.get("selected_assets", []):
                asset_id = selected_asset.get("asset_id")
                if not asset_id:
                    continue
                selected_scope = selected_asset.get("knowledge_scope", "project")
                asset_db_path = shared_db_path() if selected_scope == "cross-project" else db_path
                asset = get_asset(asset_db_path, asset_id=asset_id)
                asset_path = (
                    default_shared_asset_path(
                        {
                            "asset_type": selected_asset["asset_type"],
                            "asset_id": asset_id,
                        }
                    )
                    if selected_scope == "cross-project"
                    else memory_root / "assets" / f"{selected_asset['asset_type']}s" / f"{asset_id}.json"
                )
                if not asset and asset_path.exists():
                    asset = load_json(asset_path)
                if not asset:
                    continue
                asset = apply_asset_effectiveness(
                    asset,
                    feedback_stats.get(asset_id, {}),
                    updated_at=feedback["feedback_at"],
                )
                upsert_asset(asset_db_path, asset)
                save_json(asset_path, asset)
            activation_feedback = {
                "activation_id": updated_activation["activation_id"],
                "help_signal": feedback["help_signal"],
                "linked_asset_ids": linked_asset_ids,
                "feedback_summary": feedback["feedback_summary"],
            }

    saved_candidates = []
    promoted_assets = []
    for candidate in extract_candidates(episode):
        candidate = apply_candidate_promotion_feedback(
            candidate,
            activation_feedback=activation_feedback,
        )
        candidate_path = memory_root / "candidates" / f"{candidate['candidate_id']}.json"
        save_json(candidate_path, candidate)
        upsert_candidate(db_path, candidate)

        if not args.no_promote and should_promote_candidate(
            candidate,
            verification_status=args.verification_status,
            result_status=args.result_status,
            min_score=args.promote_threshold,
        ):
            candidate["status"] = "promoted"
            save_json(candidate_path, candidate)
            upsert_candidate(db_path, candidate)
            asset = promote_candidate(
                candidate,
                knowledge_scope=args.knowledge_scope,
                knowledge_kind=args.knowledge_kind,
            )
            asset_path = (
                default_shared_asset_path(asset)
                if args.knowledge_scope == "cross-project"
                else memory_root / "assets" / f"{asset['asset_type']}s" / f"{asset['asset_id']}.json"
            )
            save_json(asset_path, asset)
            asset_db_path = shared_db_path() if args.knowledge_scope == "cross-project" else db_path
            ensure_db(asset_db_path)
            upsert_asset(asset_db_path, asset)
            asset_milvus_db = shared_milvus_db_path() if args.knowledge_scope == "cross-project" else default_milvus_db_path(workspace)
            upsert_asset_vector(asset_milvus_db, asset)
            promoted_assets.append({"asset_id": asset["asset_id"], "path": str(asset_path)})
        elif candidate.get("promotion_readiness") in {"boosted", "encouraging"}:
            candidate["status"] = "needs_review"
            save_json(candidate_path, candidate)
            upsert_candidate(db_path, candidate)

        saved_candidates.append(
            {
                "candidate_id": candidate["candidate_id"],
                "path": str(candidate_path),
                "status": candidate["status"],
            }
        )

    _print_json(
        {
            "trace": {"trace_id": trace["trace_id"], "path": str(trace_path)},
            "episode": {"episode_id": episode["episode_id"], "path": str(episode_path)},
            "candidates": saved_candidates,
            "promoted_assets": promoted_assets,
            "activation_feedback": activation_feedback,
            "auto_promote_enabled": not args.no_promote,
            "promote_threshold": args.promote_threshold,
        }
    )
    return 0


def _handle_extract(args: argparse.Namespace) -> int:
    episode = load_json(Path(args.episode))
    candidates = extract_candidates(episode)
    workspace = workspace_from_payload(episode, Path.cwd())
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else memory_root_for_workspace(workspace) / "candidates"
    )
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    saved_paths = []
    for candidate in candidates:
        path = output_dir / f"{candidate['candidate_id']}.json"
        save_json(path, candidate)
        upsert_candidate(db_path, candidate)
        saved_paths.append(str(path))
    _print_json({"saved": saved_paths, "candidates": candidates})
    return 0


def _handle_promote(args: argparse.Namespace) -> int:
    candidate = load_json(Path(args.candidate))
    candidate["status"] = "promoted"
    workspace = workspace_from_payload(candidate, Path.cwd())
    save_json(Path(args.candidate), candidate)
    candidate_db_path = default_db_path(workspace)
    ensure_db(candidate_db_path)
    upsert_candidate(candidate_db_path, candidate)
    asset, generated_path = _persist_promoted_asset(
        candidate,
        knowledge_scope=args.knowledge_scope,
        knowledge_kind=args.knowledge_kind,
    )
    output_path = Path(args.output) if args.output else generated_path
    if args.output:
        save_json(output_path, asset)
    _print_json({"saved_to": str(output_path), "asset_id": asset["asset_id"], "asset": asset})
    return 0


def _handle_sync_milvus(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    local_assets_dir = memory_root_for_workspace(workspace) / "assets"
    local_synced = sync_assets_directory(default_milvus_db_path(workspace), local_assets_dir)
    shared_synced = 0
    if args.include_shared:
        shared_synced = sync_assets_directory(shared_milvus_db_path(), shared_memory_root() / "assets")
    _print_json(
        {
            "milvus_available": milvus_available(),
            "workspace": str(workspace),
            "local_synced": local_synced,
            "shared_synced": shared_synced,
            "local_milvus_db": str(default_milvus_db_path(workspace)),
            "shared_milvus_db": str(shared_milvus_db_path()) if args.include_shared else None,
        }
    )
    return 0


def _handle_activate(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    assets_dir = Path(args.assets_dir) if args.assets_dir else workspace / ".agent-memory" / "assets"
    candidates_dir = (
        Path(args.candidates_dir)
        if args.candidates_dir
        else workspace / ".agent-memory" / "candidates"
    )
    view = activate_assets(
        task=args.task,
        workspace=workspace,
        constraints=args.constraints,
        assets_dir=assets_dir,
        candidates_dir=candidates_dir,
        db_path=db_path,
    )
    output_path = Path(args.output) if args.output else default_activation_view_path(workspace, view)
    save_json(output_path, view)
    log_activation(db_path, view)
    touch_assets_last_used(
        db_path,
        [item["asset_id"] for item in view.get("selected_assets", [])],
        view["created_at"],
    )
    _print_json({"saved_to": str(output_path), "activation_id": view["activation_id"], "activation_view": view})
    return 0


def _handle_explain(args: argparse.Namespace) -> int:
    payload = load_json(Path(args.input))
    _print_json(explain_object(payload))
    return 0


def _handle_review_candidates(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    db_path = default_db_path(workspace)
    ensure_db(db_path)
    action_result = _apply_review_candidate_action(args=args, workspace=workspace, db_path=db_path)
    statuses = tuple(args.statuses or DEFAULT_REVIEW_QUEUE_STATUSES)
    candidates = list_candidates(
        db_path,
        workspace=str(workspace),
        statuses=statuses,
    )
    if not candidates:
        candidates_dir = memory_root_for_workspace(workspace) / "candidates"
        candidates = [
            candidate
            for candidate in iter_json_objects(candidates_dir) or []
            if candidate.get("status") in statuses
        ]
    queue = build_candidate_review_queue(candidates, workspace=str(workspace))
    output_path = (
        Path(args.output)
        if args.output
        else memory_root_for_workspace(workspace) / "reviews" / "candidate_review_queue.json"
    )
    save_json(output_path, queue)
    _print_json(
        {
            "saved_to": str(output_path),
            "candidate_count": queue["candidate_count"],
            "review_queue": queue,
            "action_result": action_result,
        }
    )
    return 0


def _handle_status(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    memory_root = memory_root_for_workspace(workspace)
    db_path = default_db_path(workspace)
    ensure_db(db_path)

    assets = list_assets(db_path, workspace=str(workspace))
    candidates = list_candidates(
        db_path,
        workspace=str(workspace),
        statuses=ALL_CANDIDATE_STATUSES,
    )
    activations = list_activation_logs(db_path, workspace=str(workspace))
    traces = list(iter_json_objects(memory_root / "traces" / "bundles"))
    episodes = list(iter_json_objects(memory_root / "episodes"))

    feedback_summary = {
        "supported_strong": 0,
        "supported_weak": 0,
        "unclear": 0,
        "missing": 0,
    }
    for activation in activations:
        help_signal = activation.get("feedback", {}).get("help_signal")
        if help_signal in feedback_summary:
            feedback_summary[help_signal] += 1
        else:
            feedback_summary["missing"] += 1

    temperature_summary = {"hot": 0, "warm": 0, "neutral": 0, "cool": 0}
    review_status_summary = {"healthy": 0, "watch": 0, "needs_review": 0, "unproven": 0}
    for asset in assets:
        temperature_summary[asset.get("temperature", "neutral")] = (
            temperature_summary.get(asset.get("temperature", "neutral"), 0) + 1
        )
        review_status_summary[asset.get("review_status", "unproven")] = (
            review_status_summary.get(asset.get("review_status", "unproven"), 0) + 1
        )

    candidate_status_summary = {status: 0 for status in ALL_CANDIDATE_STATUSES}
    for candidate in candidates:
        candidate_status_summary[candidate.get("status", "new")] = (
            candidate_status_summary.get(candidate.get("status", "new"), 0) + 1
        )

    review_queue = build_candidate_review_queue(
        [
            candidate
            for candidate in candidates
            if candidate.get("status") in DEFAULT_REVIEW_QUEUE_STATUSES
        ],
        workspace=str(workspace),
    )

    recent_assets = [
        {
            "asset_id": asset.get("asset_id"),
            "title": asset.get("title"),
            "knowledge_scope": asset.get("knowledge_scope", "project"),
            "knowledge_kind": asset.get("knowledge_kind", asset.get("asset_type", "pattern")),
            "temperature": asset.get("temperature", "neutral"),
            "review_status": asset.get("review_status", "unproven"),
            "updated_at": asset.get("updated_at") or asset.get("created_at"),
        }
        for asset in sorted(
            assets,
            key=lambda item: item.get("updated_at") or item.get("created_at") or "",
            reverse=True,
        )[: args.limit]
    ]
    recent_candidates = [
        {
            "candidate_id": candidate.get("candidate_id"),
            "title": candidate.get("title"),
            "status": candidate.get("status"),
            "promotion_readiness": candidate.get("promotion_readiness"),
            "help_signal": candidate.get("promotion_feedback", {}).get("help_signal"),
            "updated_at": candidate.get("updated_at") or candidate.get("created_at"),
            "created_at": candidate.get("created_at"),
        }
        for candidate in sorted(
            candidates,
            key=lambda item: item.get("updated_at") or item.get("created_at") or "",
            reverse=True,
        )[: args.limit]
    ]
    recent_activations = [
        {
            "activation_id": activation.get("activation_id"),
            "task_query": activation.get("task_query"),
            "selected_count": len(activation.get("selected_assets", [])),
            "help_signal": activation.get("feedback", {}).get("help_signal"),
            "created_at": activation.get("created_at"),
        }
        for activation in activations[: args.limit]
    ]
    local_milvus = milvus_backend_summary(
        default_milvus_db_path(workspace),
        deep_check=args.deep_retrieval_check,
    )
    shared_milvus = milvus_backend_summary(
        shared_milvus_db_path(),
        deep_check=args.deep_retrieval_check,
    )
    milvus_indexed_entities = local_milvus.get("indexed_entities")
    possible_stale_entities = (
        max(milvus_indexed_entities - len(assets), 0)
        if isinstance(milvus_indexed_entities, int)
        else None
    )
    milvus_asset_coverage = (
        round(min(float(milvus_indexed_entities), float(len(assets))) / len(assets), 4)
        if isinstance(milvus_indexed_entities, int) and assets
        else None
    )

    payload = {
        "workspace": str(workspace),
        "generated_at": now_utc(),
        "backend_configuration": resolve_backend_config(),
        "retrieval_backends": {
            "sqlite": {
                "backend": "sqlite",
                "available": True,
                "db_path": str(db_path),
                "db_exists": db_path.exists(),
                "asset_rows": len(assets),
                "candidate_rows": len(candidates),
                "activation_log_rows": len(activations),
            },
            "milvus": {
                "available": milvus_available(),
                "local": local_milvus,
                "shared": shared_milvus,
                "asset_coverage": {
                    "indexed_entities": milvus_indexed_entities,
                    "asset_rows": len(assets),
                    "coverage_ratio": milvus_asset_coverage,
                    "possible_stale_entities": possible_stale_entities,
                    "deep_check_required": milvus_asset_coverage is None,
                },
            },
        },
        "counts": {
            "traces": len(traces),
            "episodes": len(episodes),
            "candidates": len(candidates),
            "assets": len(assets),
            "activation_logs": len(activations),
        },
        "activation_feedback_summary": feedback_summary,
        "asset_effectiveness_summary": {
            "temperature": temperature_summary,
            "review_status": review_status_summary,
        },
        "candidate_status_summary": candidate_status_summary,
        "candidate_review_queue": {
            "candidate_count": review_queue["candidate_count"],
            "status_summary": review_queue["status_summary"],
            "top_items": review_queue["items"][: args.limit],
        },
        "recent_assets": recent_assets,
        "recent_candidates": recent_candidates,
        "recent_activations": recent_activations,
    }
    output_path = (
        Path(args.output)
        if args.output
        else memory_root / "reviews" / "workspace_status.json"
    )
    save_json(output_path, payload)
    _print_json({"saved_to": str(output_path), "status": payload})
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "ingest":
        return _handle_ingest(args)
    if args.command == "auto-start":
        return _handle_auto_start(args)
    if args.command == "auto-finish":
        return _handle_auto_finish(args)
    if args.command == "install-project":
        return _handle_install_project(args)
    if args.command == "sync-milvus":
        return _handle_sync_milvus(args)
    if args.command == "review":
        return _handle_review(args)
    if args.command == "extract":
        return _handle_extract(args)
    if args.command == "promote":
        return _handle_promote(args)
    if args.command == "activate":
        return _handle_activate(args)
    if args.command == "explain":
        return _handle_explain(args)
    if args.command == "review-candidates":
        return _handle_review_candidates(args)
    if args.command == "status":
        return _handle_status(args)

    parser.error(f"unknown command: {args.command}")
    return 2


def entrypoint() -> None:
    raise SystemExit(main())
