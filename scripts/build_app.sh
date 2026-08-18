#!/usr/bin/env bash
#
# Build the MP-OSC macOS app bundle.
#
# Produces dist/MP-OSC.app -- a self-contained, ad-hoc signed, arm64 onedir
# bundle containing Python, mediapipe, ndi-python (libndi.dylib) and the
# landmarker models from src/tasks/. Takes several minutes and roughly 2GB of
# scratch space in build/.
#
# Usage: ./scripts/build_app.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Fetching the heavy pose model so it ships inside the bundle"
uv run python -c "from src.model_downloader import download_pose_model; download_pose_model('heavy')"

echo "==> Running PyInstaller"
uv run pyinstaller --noconfirm --clean mp-osc.spec

echo "==> Ad-hoc signing the bundle"
codesign --force --deep -s - dist/MP-OSC.app

echo
echo "Built: $(pwd)/dist/MP-OSC.app"
echo "Run it with: open dist/MP-OSC.app"
