#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${CODEX_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
export EXPCAP_STORAGE_PROFILE="${EXPCAP_STORAGE_PROFILE:-user-cache}"
export EXPCAP_HOME="${EXPCAP_HOME:-$HOME/.expcap}"
HOOK_SCRIPT="$PROJECT_DIR/scripts/expcap-hook"
if [[ ! -f "$HOOK_SCRIPT" ]]; then
  HOOK_SCRIPT="/Users/wuyue/github_project/agent-experience-capitalization/scripts/expcap-hook"
fi

exec python3 "$HOOK_SCRIPT" pre-tool-use --host codex --workspace "$PROJECT_DIR"
