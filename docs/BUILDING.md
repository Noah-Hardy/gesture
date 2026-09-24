# Building Gesture

Contributor documentation for building `Gesture.app` from source and publishing releases. This file is not included in the in-app help, which contains only the topics registered in `TOPICS` in `src/docs.py`.

## Running from source

```sh
uv venv
uv sync
uv run python main.py pose
```

This runs the engine directly. The OSC destination comes from `config.json` (`osc.host`, `osc.port`) unless `--host` or `--port` is given. `uv run python app.py` with no arguments opens the launcher; it is also the packaged app's entry point. The complete flag and configuration reference is in `docs/appendix-advanced.md`.

## Building the app bundle

### Requirements

- **Apple Silicon Mac, macOS 13 or later.** `ndi-python` 6.x publishes only `macosx_13_0_arm64` wheels, which sets the minimum macOS version.
- **Homebrew Python 3.11 with Tk 8.6:**

  ```sh
  brew install python@3.11 python-tk@3.11
  uv venv --clear --python /opt/homebrew/opt/python@3.11/bin/python3.11
  uv sync --group dev
  ```

  The launcher requires `python-tk@3.11`. System Pythons link against the deprecated Tcl/Tk 8.5 in `/System/Library`, which PyInstaller does not bundle.
- **Xcode Command Line Tools** (`xcode-select --install`), for `codesign`.
- **PyInstaller**, from the `dev` dependency group.
- **Internet access on the first build**, to download the landmarker models into `src/tasks/`.

The NDI SDK is not required; `libndi.dylib` is included in the `ndi-python` wheel.

### Build

```sh
./scripts/build_app.sh
open dist/Gesture.app
```

The script downloads the five landmarker models (about 64 MB, gitignored and reused across builds), runs PyInstaller with `gesture.spec`, and ad-hoc signs the result. A build takes a few minutes and produces about 300 MB in `dist/`.

### Bundle behaviour

- Configuration is stored in `~/Library/Application Support/Gesture/config.json`. When running from source, `config.json` in the working directory is used. `config.json` is gitignored; without one, the defaults in `DEFAULT_CONFIG` in `src/config.py` apply.
- All landmarker models are bundled; nothing is downloaded at runtime.
- `NSCameraUsageDescription` is declared, so macOS requests camera access on first use.
- NDI discovery uses Bonjour (`NSLocalNetworkUsageDescription`, `NSBonjourServices`), so the first NDI refresh may trigger a local network permission prompt.
- The bundle ID is `net.hardymail.mp-osc`. It must not change: the updater verifies new builds against it.

## Releasing

```sh
./scripts/release.sh --build
```

This produces `dist/Gesture-<version>-macos-arm64.zip` and its `.sha256`. The zip is created with `ditto`, which preserves bundle structure and code signatures; `zip` does not. The in-app updater selects releases by this filename (`_ASSET_RE` in `src/updater.py`) and ignores releases without it, so every release must include the zip under exactly this name.

When `GESTURE_CODESIGN_IDENTITY` is set, the script also produces `dist/Gesture-<version>-macos-arm64.dmg` and its `.sha256`: a disk image with an `Applications` link, for manual installation. Unsigned builds skip the DMG.

With `GESTURE_NOTARY_PROFILE` set, notarization runs twice:

1. The zip is notarized and the ticket is stapled to `Gesture.app`.
2. The DMG is built from the stapled app, then signed, notarized and stapled.

The order is required: a DMG built before stapling would contain an app without a ticket.

To publish manually:

```sh
git tag -a v<version> -m "Gesture v<version>"
git push origin v<version>
gh release create v<version> \
  dist/Gesture-<version>-macos-arm64.dmg dist/Gesture-<version>-macos-arm64.dmg.sha256 \
  dist/Gesture-<version>-macos-arm64.zip dist/Gesture-<version>-macos-arm64.zip.sha256 \
  --title "Gesture v<version>" --notes "..."
```

### Updater compatibility

- Updaters in 0.2.1 and later accept both `Gesture-` and `MP-OSC-` asset names. Updaters in 0.2.0 and earlier accept only `MP-OSC-`; they ignore Gesture releases and update to v0.2.1 first.
- Keep v0.2.1 published and among the ten most recent releases.

### GitHub Actions

`.github/workflows/release.yml` runs the same scripts on a GitHub-hosted Apple Silicon runner (`macos-15`). It runs only on manual dispatch: **Actions → Build macOS release → Run workflow**.

| Input | Default | Effect |
|---|---|---|
| `ref` | The dispatching branch | Branch, tag or SHA to build |
| `version` | Version in `pyproject.toml` | Version override |
| `publish` | Off | Create a GitHub Release |
| `draft` | On | Create the release as a draft |

Each run uploads the zip, the DMG (for signed builds) and their checksums as a build artifact, retained for 30 days.

Dispatch from a ref that contains `gesture.spec` (0.3.0 or later). GitHub uses the workflow file from the dispatching ref, and earlier versions reference `mp-osc.spec`.

Model files are cached between runs, keyed on `src/model_downloader.py`.

#### Signing secrets

Without secrets, the workflow produces an ad-hoc signed build. Configure these repository secrets (**Settings → Secrets and variables → Actions**) to sign and notarize:

| Secret | Required for | Value |
|---|---|---|
| `MACOS_CERTIFICATE_P12` | Signing | Base64-encoded `.p12`: `base64 -i cert.p12 \| pbcopy` |
| `MACOS_CERTIFICATE_PASSWORD` | Signing | `.p12` export password |
| `MACOS_SIGNING_IDENTITY` | Optional | For example `Developer ID Application: Name (TEAMID)`; detected from the certificate if unset |
| `APPLE_NOTARY_APPLE_ID` | Notarization | Apple ID email |
| `APPLE_NOTARY_PASSWORD` | Notarization | App-specific password |
| `APPLE_NOTARY_TEAM_ID` | Notarization | 10-character Team ID |

Notarization requires signing. If the notary secrets are set without a certificate, notarization is skipped with a warning. The certificate is imported into a temporary keychain for the duration of the job.

### Signing for distribution

Gatekeeper rejects ad-hoc signed builds on other machines. Recipients must remove the quarantine attribute:

```sh
xattr -dr com.apple.quarantine /Applications/Gesture.app
```

Distribution without this step requires an Apple Developer account and a **Developer ID Application** certificate:

```sh
# One-time: store notarization credentials
xcrun notarytool store-credentials gesture-notary \
  --apple-id you@example.com --team-id TEAMID --password <app-specific-password>

export GESTURE_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export GESTURE_NOTARY_PROFILE="gesture-notary"
./scripts/release.sh --build
```

The legacy `MPOSC_CODESIGN_IDENTITY` and `MPOSC_NOTARY_PROFILE` variables are also accepted.

Hardened runtime entitlements are defined in `scripts/entitlements.plist`: JIT and unsigned executable memory for TensorFlow Lite, disabled library validation for PyInstaller's bundled libraries, and camera access.

#### Signing order

`release.sh` signs each binary individually, from the innermost outwards, instead of using `codesign --deep`. Apple does not support `--deep` for distribution signing: it applies top-level entitlements to nested code and skips files it does not recognize as code. The notary service requires the hardened runtime and a secure timestamp on every nested Mach-O file.

The bundle contains about 176 Mach-O files, mostly extension modules and libraries from third-party wheels (the current count is maintained in the comments of `scripts/release.sh`). The script signs these first, then framework bundles, then the `.app`. Entitlements are applied only to the `.app`.

Each signature uses `--timestamp`, which contacts Apple's timestamp service; signing requires a network connection and takes several minutes.

### Runtime requirements

The bundle includes Python, MediaPipe, OpenCV, Tcl/Tk, `libndi.dylib` and the landmarker models. It requires an Apple Silicon Mac running macOS 13 or later.
