#!/usr/bin/env bash
# UAV Planner - Linux/Mac environment setup
# Requires: Python 3.10+ (no auto-download on non-Windows)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==================================================="
echo "  UAV Planner - Environment Setup (Linux/Mac)"
echo "==================================================="
echo

# Find Python
PY_EXE=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c "import sys; v=sys.version_info; print(v.major*10+v.minor)" 2>/dev/null | grep -qE "^(3[1-9]|[4-9][0-9])$"; then
            PY_EXE="$cand"
            break
        fi
    fi
done

if [ -z "$PY_EXE" ]; then
    echo "[!] Python 3.10+ not found."
    echo
    echo "Install it via your package manager:"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-venv python3-pip"
    echo "  Fedora:        sudo dnf install python3 python3-pip"
    echo "  macOS:         brew install python@3.11"
    echo
    exit 1
fi

echo "[OK] Using Python: $PY_EXE"
$PY_EXE --version
echo

# Run the core installer
$PY_EXE scripts/setup_core.py
