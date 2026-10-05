#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3.13}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.13 not found. Install Python 3.13+ first, then rerun this script."
  exit 1
fi

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python tools/portable_setup.py configure
.venv/bin/python tools/portable_setup.py verify

echo "MyAgent Mac setup complete."
echo "Next: relink each copied workspace with:"
echo ".venv/bin/python tools/portable_setup.py relink PROJECT_ID /absolute/path/to/workspace"
