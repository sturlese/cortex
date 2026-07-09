#!/usr/bin/env bash
# Bootstrap a local venv (once) and run the offline golden evals.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/evals/.venv"
# Gate on a `.deps-ok` stamp written only after a successful install, so an interrupted bootstrap
# (Ctrl-C / network blip) is retried rather than leaving a venv whose bin/python exists but whose
# dependencies are missing.
if [ ! -f "$VENV/.deps-ok" ]; then
  echo "==> bootstrapping eval venv (one-time)"
  rm -rf "$VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" -q install -r "$ROOT/pipeline/clean/requirements.txt" -r "$ROOT/pipeline/graph/requirements.txt"
  touch "$VENV/.deps-ok"
fi
exec "$VENV/bin/python" "$ROOT/evals/run_evals.py"
