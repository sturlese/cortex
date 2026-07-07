#!/usr/bin/env bash
# Bootstrap a local venv (once) and run the offline golden evals.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/evals/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "==> bootstrapping eval venv (one-time)"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" -q install -r "$ROOT/pipeline/clean/requirements.txt" -r "$ROOT/pipeline/graph/requirements.txt"
fi
exec "$VENV/bin/python" "$ROOT/evals/run_evals.py"
