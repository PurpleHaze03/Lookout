#!/usr/bin/env bash
# ============================================================
#  lookout one-shot setup (Linux / macOS)
#  Creates a virtual environment, installs all dependencies,
#  the headless browser, and your starter config files.
# ============================================================
set -e
cd "$(dirname "$0")"

echo
echo " [1/5] Looking for Python..."
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else
    echo " ERROR: Python not found. Install python3 first." >&2
    exit 1
fi
"$PY" --version

echo
echo " [2/5] Creating virtual environment (.venv)..."
if [ ! -d .venv ]; then
    "$PY" -m venv .venv
else
    echo "        .venv already exists - reusing it."
fi

echo
echo " [3/5] Installing lookout + dependencies..."
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/python -m pip install -e .

echo
echo " [4/5] Installing the headless browser (Chromium, one-time ~150 MB)..."
.venv/bin/python -m playwright install chromium || \
    echo " WARNING: browser install failed - JS-heavy shops like AliExpress won't work until you run: .venv/bin/python -m playwright install chromium"

echo
echo " [5/5] Creating starter config files..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "        .env created  -> fill in your Gmail address + app password!"
else
    echo "        .env already exists - not touched."
fi
if [ ! -f watchlist.yaml ]; then
    cp watchlist.example.yaml watchlist.yaml
    echo "        watchlist.yaml created  -> add the products you want to watch!"
else
    echo "        watchlist.yaml already exists - not touched."
fi

echo
echo " ============================================================"
echo "  Setup complete! Next steps:"
echo
echo "    1. Edit .env             (nano .env)"
echo "    2. Edit watchlist.yaml   (nano watchlist.yaml)"
echo "    3. Activate the environment:"
echo "         source .venv/bin/activate"
echo "    4. Test without sending mail:"
echo "         lookout check --dry-run"
echo "    5. Go live:"
echo "         lookout check"
echo
echo "  Tip: run  lookout -h  to see all commands with examples."
echo " ============================================================"
