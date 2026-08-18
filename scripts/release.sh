#!/usr/bin/env bash
#
# Package MP-OSC.app into a distributable archive for a GitHub release.
#
# Produces dist/MP-OSC-<version>-macos-arm64.zip plus a .sha256 checksum.
# The zip is created with ditto, which is the only archiver that reliably
# preserves macOS bundle structure and code signatures.
#
# Usage:
#   ./scripts/release.sh              # package the existing dist/MP-OSC.app
#   ./scripts/release.sh --build      # rebuild the app first, then package
#
# Signing and notarization (optional, needs a paid Apple Developer account):
#   export MPOSC_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#   export MPOSC_NOTARY_PROFILE="mp-osc-notary"   # see notarytool store-credentials
#   ./scripts/release.sh --build
#
# Without MPOSC_CODESIGN_IDENTITY the build is ad-hoc signed. It runs fine on
# this machine, but Gatekeeper rejects it anywhere else and users must strip the
# quarantine attribute by hand. See the README for what that means.
#
set -euo pipefail

cd "$(dirname "$0")/.."

APP="dist/MP-OSC.app"
VERSION="$(grep -m1 '^version' pyproject.toml | sed 's/.*"\(.*\)".*/\1/')"
ARCHIVE="dist/MP-OSC-${VERSION}-macos-arm64.zip"

# ----------------------------------------------------------------------------
# Optional rebuild
# ----------------------------------------------------------------------------
if [[ "${1:-}" == "--build" ]]; then
    if [[ -n "${MPOSC_CODESIGN_IDENTITY:-}" ]]; then
        export MPOSC_ENTITLEMENTS="$(pwd)/scripts/entitlements.plist"
        echo "==> Building with Developer ID: ${MPOSC_CODESIGN_IDENTITY}"
    else
        echo "==> Building ad-hoc signed (no MPOSC_CODESIGN_IDENTITY set)"
    fi
    ./scripts/build_app.sh
fi

if [[ ! -d "$APP" ]]; then
    echo "error: $APP not found. Run ./scripts/build_app.sh first, or pass --build" >&2
    exit 1
fi

# ----------------------------------------------------------------------------
# Sign for distribution, when an identity is available
# ----------------------------------------------------------------------------
if [[ -n "${MPOSC_CODESIGN_IDENTITY:-}" ]]; then
    echo "==> Re-signing the bundle with the hardened runtime"
    codesign --force --deep --timestamp --options runtime \
        --entitlements scripts/entitlements.plist \
        -s "$MPOSC_CODESIGN_IDENTITY" "$APP"
fi

echo "==> Verifying the signature"
codesign --verify --deep --strict --verbose=1 "$APP" 2>&1 | tail -2

# ----------------------------------------------------------------------------
# Archive (ditto preserves the bundle and its signature; plain zip does not)
# ----------------------------------------------------------------------------
echo "==> Creating $ARCHIVE"
rm -f "$ARCHIVE"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ARCHIVE"

# ----------------------------------------------------------------------------
# Notarize, when credentials are available
# ----------------------------------------------------------------------------
if [[ -n "${MPOSC_NOTARY_PROFILE:-}" ]]; then
    echo "==> Submitting to Apple for notarization (this takes a few minutes)"
    xcrun notarytool submit "$ARCHIVE" \
        --keychain-profile "$MPOSC_NOTARY_PROFILE" --wait

    echo "==> Stapling the ticket to the app"
    xcrun stapler staple "$APP"

    echo "==> Repackaging so the archive contains the stapled app"
    rm -f "$ARCHIVE"
    ditto -c -k --sequesterRsrc --keepParent "$APP" "$ARCHIVE"

    echo "==> Verifying Gatekeeper acceptance"
    spctl -a -vvv -t exec "$APP" 2>&1 | tail -3
else
    echo "==> Skipping notarization (MPOSC_NOTARY_PROFILE not set)"
fi

# ----------------------------------------------------------------------------
# Checksum and summary
# ----------------------------------------------------------------------------
shasum -a 256 "$ARCHIVE" > "${ARCHIVE}.sha256"

echo
echo "Archive : $(pwd)/$ARCHIVE  ($(du -h "$ARCHIVE" | cut -f1))"
echo "Checksum: $(cut -d' ' -f1 < "${ARCHIVE}.sha256")"
echo
echo "Gatekeeper status:"
if spctl -a -t exec "$APP" >/dev/null 2>&1; then
    echo "  accepted - installs on other machines with no extra steps"
else
    echo "  REJECTED - ad-hoc signed. Other users must run:"
    echo "    xattr -dr com.apple.quarantine /Applications/MP-OSC.app"
fi
echo
echo "Publish with:"
echo "  git tag -a v${VERSION} -m 'MP-OSC v${VERSION}' && git push origin v${VERSION}"
echo "  gh release create v${VERSION} '${ARCHIVE}' '${ARCHIVE}.sha256' \\"
echo "    --title 'MP-OSC v${VERSION}' --notes-file RELEASE_NOTES.md"
