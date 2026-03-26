#!/usr/bin/env bash
set -euo pipefail

# Run Edict against the native macOS OpenClaw state stored in the project data home.
EDICT_REPO="${EDICT_REPO:-/Users/julimi/Documents/_Hub/02_Projects/edict}"
OPENCLAW_HOME="${OPENCLAW_HOME:-/Users/julimi/Documents/_Hub/02_Projects/openclaw/open_claw_data/home}"
OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-$OPENCLAW_HOME/.openclaw}"
OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-$OPENCLAW_STATE_DIR/openclaw.json}"
HOST_USER_HOME="${HOST_USER_HOME:-/Users/julimi}"

usage() {
  cat <<'EOF'
Usage: edict-openclaw.sh <command> [args...]

Commands:
  install             Run Edict install.sh against the native OpenClaw state
  loop [interval]     Run Edict sync loop (default interval from script)
  dashboard [args...] Start dashboard/server.py
  sync                Run one full sync pass (runtime -> config -> stats -> live)
  shell <cmd...>      Run an arbitrary command in Edict repo with OpenClaw env pinned
  doctor              Show OpenClaw gateway + status (via current openclaw CLI)
EOF
}

require_paths() {
  if [[ ! -d "$EDICT_REPO" ]]; then
    echo "Edict repo not found: $EDICT_REPO" >&2
    exit 1
  fi
  if [[ ! -d "$OPENCLAW_HOME" ]]; then
    echo "OpenClaw home not found: $OPENCLAW_HOME" >&2
    exit 1
  fi
  if [[ ! -f "$OPENCLAW_HOME/.openclaw/openclaw.json" ]]; then
    echo "OpenClaw config not found: $OPENCLAW_HOME/.openclaw/openclaw.json" >&2
    exit 1
  fi
}

run_in_edict() {
  (
    export HOME="$HOST_USER_HOME"
    export OPENCLAW_HOME
    export OPENCLAW_STATE_DIR
    export OPENCLAW_CONFIG_PATH
    cd "$EDICT_REPO"
    "$@"
  )
}

main() {
  local cmd="${1:-}"
  if [[ -z "$cmd" ]]; then
    usage
    exit 1
  fi

  require_paths

  case "$cmd" in
    install)
      run_in_edict chmod +x install.sh
      run_in_edict ./install.sh
      ;;
    loop)
      shift
      run_in_edict bash scripts/run_loop.sh "$@"
      ;;
    dashboard)
      shift
      run_in_edict python3 dashboard/server.py "$@"
      ;;
    sync)
      run_in_edict python3 scripts/sync_from_openclaw_runtime.py
      run_in_edict python3 scripts/sync_agent_config.py
      run_in_edict python3 scripts/apply_model_changes.py
      run_in_edict python3 scripts/sync_officials_stats.py
      run_in_edict python3 scripts/refresh_live_data.py
      ;;
    shell)
      shift
      if [[ $# -eq 0 ]]; then
        echo "shell command is required" >&2
        exit 1
      fi
      run_in_edict "$@"
      ;;
    doctor)
      run_in_edict openclaw gateway status --json
      run_in_edict openclaw status --json
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      echo "Unknown command: $cmd" >&2
      usage
      exit 1
      ;;
  esac
}

main "$@"
