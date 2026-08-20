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
#
# Signed inside-out, one binary at a time, rather than with `codesign --deep`.
# Apple documents --deep as unsuitable for distribution signing: it applies the
# top-level entitlements to nested code and quietly skips files it does not
# recognise as code. The notary service, meanwhile, checks *every* nested
# Mach-O for the hardened runtime and a secure timestamp, and rejects the
# submission if any one of them is missing either.
#
# That matters here more than it would for a normal app. A PyInstaller bundle
# of this project carries ~176 Mach-O files - CPython extension modules and
# dylibs shipped inside third-party wheels - none of which are build products
# this script produced. Each is signed on its own, then any framework bundles,
# then the .app last so the outer seal covers the finished contents.
#
# Entitlements are applied only to the outer bundle. They govern the process
# that actually runs (Contents/MacOS/mp-osc); a dylib does not get its own
# JIT permission, it inherits the hosting process's. The main executable's
# disable-library-validation is what lets these third-party dylibs load.
# ----------------------------------------------------------------------------
sign_inside_out() {
    local identity="$1"
    local app="$2"
    local -a flags=(--force --timestamp --options runtime --sign "$identity")

    echo "==> Finding nested Mach-O binaries in $app"
    local -a binaries=()
    local path
    while IFS= read -r -d '' path; do
        # Signatures live in _CodeSignature; never treat them as code.
        case "$(file -b "$path" 2>/dev/null)" in
            Mach-O*) binaries+=("$path") ;;
        esac
    done < <(find "$app" -type f ! -path '*/_CodeSignature/*' -print0)

    local total=${#binaries[@]}
    echo "    $total to sign"

    # Each --timestamp is a round trip to Apple's timestamp authority, so this
    # is network-bound and takes a few minutes. It cannot be skipped: without a
    # secure timestamp the signature stops validating the day the certificate
    # expires, and notarization refuses it outright.
    local i=0
    for path in "${binaries[@]}"; do
        i=$((i + 1))
        # Overwrite one line when a human is watching; in CI, where \r just
        # produces one unreadable mega-line, log a milestone instead.
        if [[ -t 1 ]]; then
            printf '\r    signing %d/%d' "$i" "$total"
        elif (( i % 50 == 0 || i == total )); then
            printf '    signing %d/%d\n' "$i" "$total"
        fi
        if ! codesign "${flags[@]}" "$path" 2>/dev/null; then
            [[ -t 1 ]] && printf '\n'
            echo "error: failed to sign $path" >&2
            return 1
        fi
    done
    [[ -t 1 ]] && printf '\n'

    # Framework bundles are sealed after their contents. -depth walks deepest
    # first, so a nested framework is always signed before its parent.
    local fw
    while IFS= read -r -d '' fw; do
        echo "    sealing framework $(basename "$fw")"
        codesign "${flags[@]}" "$fw"
    done < <(find "$app" -depth -type d -name '*.framework' -print0)

    # The app bundle last, and the only place entitlements are applied.
    echo "    sealing $(basename "$app") with entitlements"
    codesign "${flags[@]}" --entitlements scripts/entitlements.plist "$app"
}

if [[ -n "${MPOSC_CODESIGN_IDENTITY:-}" ]]; then
    echo "==> Signing with the hardened runtime (inside-out)"
    sign_inside_out "$MPOSC_CODESIGN_IDENTITY" "$APP"
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
