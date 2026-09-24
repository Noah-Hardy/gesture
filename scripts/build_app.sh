#!/usr/bin/env bash
#
# Build the Gesture macOS app bundle.
#
# Produces dist/Gesture.app -- a self-contained, ad-hoc signed, arm64 onedir
# bundle containing Python, mediapipe, ndi-python (libndi.dylib) and the
# landmarker models. Takes several minutes and roughly 2GB of scratch space
# in build/.
#
# The .task models are not kept in the repository. This script downloads every
# model the app can select into src/tasks/ first, so the bundle ships with all
# of them and a packaged app never has to reach the network for a model.
# Already-downloaded models are reused, so repeat builds do not re-fetch.
#
# Usage: ./scripts/build_app.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Fetching the landmarker models so they ship inside the bundle"
uv run python - <<'PY'
import sys

from src.model_downloader import (
    download_hand_model,
    download_holistic_model,
    download_pose_model,
)

jobs = [(f"pose ({t})", lambda t=t: download_pose_model(t)) for t in ("lite", "full", "heavy")]
jobs += [("hand", download_hand_model), ("holistic", download_holistic_model)]

for label, fetch in jobs:
    if not fetch():
        sys.exit(f"Failed to obtain the {label} model; aborting the build")
PY

echo "==> Running PyInstaller"
uv run pyinstaller --noconfirm --clean gesture.spec

# An ad-hoc signature is the floor, not a preference: arm64 macOS refuses to
# execute unsigned code, so the bundle needs one to launch at all. Skip it when
# a real identity is set, because --deep ad-hoc would overwrite the Developer ID
# signature the spec just applied to the executable, and scripts/release.sh is
# about to sign everything properly inside-out anyway.
# GESTURE_CODESIGN_IDENTITY, or its pre-rename name MPOSC_CODESIGN_IDENTITY
CODESIGN_IDENTITY="${GESTURE_CODESIGN_IDENTITY:-${MPOSC_CODESIGN_IDENTITY:-}}"
if [[ -n "$CODESIGN_IDENTITY" ]]; then
    echo "==> Leaving the bundle unsealed for release.sh to sign as ${CODESIGN_IDENTITY}"
else
    echo "==> Ad-hoc signing the bundle"
    codesign --force --deep -s - dist/Gesture.app
fi

echo
echo "Built: $(pwd)/dist/Gesture.app"
echo "Run it with: open dist/Gesture.app"
