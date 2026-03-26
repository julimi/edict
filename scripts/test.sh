#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "python3 not found" >&2
  exit 1
fi

if "$PYTHON_BIN" -m pytest --version >/dev/null 2>&1; then
  :
else
  echo "pytest is not available for $PYTHON_BIN" >&2
  echo "Create/activate .venv and install pytest first." >&2
  exit 1
fi

DEFAULT_TESTS=(
  tests/test_skill_service.py
  tests/test_scheduler_state.py
  tests/test_dispatch_service.py
  tests/test_scheduler_service.py
  tests/test_runtime_activity_service.py
  tests/test_runtime_state.py
  tests/test_openclaw_adapter.py
  tests/test_server.py
  tests/test_workflow_service.py
  tests/test_workflow_rules.py
  tests/test_kanban.py
)

if [[ "$#" -gt 0 ]]; then
  exec "$PYTHON_BIN" -m pytest -q "$@"
else
  exec "$PYTHON_BIN" -m pytest -q "${DEFAULT_TESTS[@]}"
fi
