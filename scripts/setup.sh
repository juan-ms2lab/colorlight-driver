#!/usr/bin/env bash
# Set up ledpanels on a new computer: install dependencies, allow raw network access, find the panels.
set -euo pipefail
cd "$(dirname "$0")/.."

if command -v uv >/dev/null 2>&1; then
    uv sync
else
    python3 -m venv .venv
    .venv/bin/pip install -e .
fi

PY="$(readlink -f .venv/bin/python)"
echo
echo "Sending frames needs raw network access (CAP_NET_RAW) for this Python binary:"
echo "  $PY"
read -r -p "Run 'sudo setcap cap_net_raw+ep' on it now? [y/N] " answer
if [[ "$answer" == [yY]* ]]; then
    sudo setcap cap_net_raw+ep "$PY"
else
    echo "Skipped. Run the commands with sudo instead, or grant it later."
fi

echo
echo "Looking for panels (cable must be connected)..."
.venv/bin/ledpanels setup --write || true

echo
echo "Done. Start the control panel with: .venv/bin/ledpanels serve"
