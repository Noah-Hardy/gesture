# Building MP-OSC

This is contributor documentation for building `MP-OSC.app` from source and cutting a release. It is not shipped inside the app (the in-app Help menu only ships the topics registered in `src/docs.py`'s `TOPICS` tuple, and this file deliberately isn't one of them) — it lives in the repo for anyone building or releasing MP-OSC themselves.

If you just want to use MP-OSC, see the [README](../README.md) and download a release instead; nothing here is required to run the app.

## Running from source

```sh
uv venv
uv sync
uv run python main.py pose
```

This runs the tracking engine directly, without the packaged app. The OSC target defaults to the values in `config.json` (`osc.host` / `osc.port`); override at the command line with `--host` / `--port`. The launcher window itself is `uv run python app.py` with no arguments — the same entry point the packaged `.app` uses, which dispatches to the launcher when given no command-line arguments and to the tracking engine otherwise. The full CLI flag and `config.json` key reference lives in the in-app **Appendix: CLI & config.json** topic (Help menu, or `docs/appendix-advanced.md` in this repo).

## Building the app bundle

### Requirements

- **Apple Silicon Mac, macOS 13+** — `ndi-python` 6.x publishes only `macosx_13_0_arm64` wheels. That wheel tag, not the Python version, is what sets the app's minimum macOS.
- **Homebrew Python 3.11 with Tk 8.6**, which is what the project's virtual environment is built on:

  ```sh
  brew install python@3.11 python-tk@3.11
  uv venv --clear --python /opt/homebrew/opt/python@3.11/bin/python3.11
  uv sync --group dev
  ```

  Tkinter is a separate Homebrew formula (`python-tk@3.11`); without it the launcher window cannot start. This combination matters for packaging: Homebrew's Tcl/Tk 8.6 lives under `/opt/homebrew` and gets bundled into the app, whereas macOS system Pythons link against the deprecated `/System/Library` Tcl/Tk 8.5, which PyInstaller will not bundle.
- **Xcode Command Line Tools** (`xcode-select --install`) for `codesign`.
- **PyInstaller**, installed with the dev dependency group above.
- **Internet access on the first build**, to fetch the landmarker models into `src/tasks/`. Later builds reuse whatever is already there.

No NDI SDK installation is needed — `libndi.dylib` ships inside the `ndi-python` wheel and is bundled automatically.

### Build

```sh
./scripts/build_app.sh
open dist/MP-OSC.app
```

The script downloads all five landmarker models so they ship inside the bundle, runs PyInstaller against `mp-osc.spec`, and ad-hoc signs the result. The models are not kept in the repository — they are gitignored downloads of roughly 64 MB, fetched once and reused by later builds. Expect a few minutes and roughly 300 MB in `dist/`.

### Behavior inside the bundle

- Settings are read from and saved to `~/Library/Application Support/mp-osc/config.json`. Running from a terminal still uses `config.json` in the working directory, exactly as before. Neither is committed to the repo — `config.json` is gitignored, so a fresh clone or a fresh install starts from `src/config.py`'s built-in `DEFAULT_CONFIG` until something gets saved.
- All five landmarker models are bundled, so nothing is downloaded at runtime.
- The bundle declares `NSCameraUsageDescription`, so macOS prompts once for camera access. If tracking never starts, check **System Settings → Privacy & Security → Camera**.
- NDI discovery uses Bonjour, declared via `NSLocalNetworkUsageDescription` and `NSBonjourServices`, so the first NDI refresh may raise a local network prompt.
- The app is ad-hoc signed for personal use. Distributing it to other machines additionally requires a Developer ID, the hardened runtime, and notarization — see below.

## Releasing

`scripts/release.sh` packages the built app into distributable archives:

```sh
./scripts/release.sh --build     # build, then package
```

This always produces `dist/MP-OSC-<version>-macos-arm64.zip` and a matching `.sha256`. The zip is created with `ditto`, which is the only macOS archiver that reliably preserves bundle structure and code signatures — a plain `zip` corrupts the signature. The in-app updater (`src/updater.py`) matches release assets by this exact filename pattern (see `_ASSET_RE` / `_pick_release`), and skips a release entirely when no matching zip is attached — so the zip ships on every release, unconditionally, or existing installs silently stop being offered updates.

When `MPOSC_CODESIGN_IDENTITY` is set, the script also produces `dist/MP-OSC-<version>-macos-arm64.dmg` and its own `.sha256` — a disk image with an `Applications` shortcut, which is what the README and Releases page point people at for a fresh install (drag-to-Applications is the flow macOS users already know). An unsigned build skips the DMG: an unsigned disk image would hit the same Gatekeeper rejection as the app inside it, and there's no notarization ticket to staple to it anyway.

Building the DMG involves **two separate notarization submissions** when `MPOSC_NOTARY_PROFILE` is set, not one:

1. The interim zip is submitted first, and the returned ticket is stapled directly onto `MP-OSC.app` — this is what makes the `.app` itself carry proof of notarization, not just whatever archive happens to be wrapping it.
2. The DMG is built *from that already-stapled app*, signed, then submitted and stapled a second time, so the disk image itself also opens clean and offline.

Building the DMG before stapling the app would seal a disk image whose contents don't yet carry the ticket, so the order matters. The script prints the current Gatekeeper status and the commands to publish.

To publish on GitHub:

```sh
git tag -a v<version> -m "MP-OSC v<version>"
git push origin v<version>
gh release create v<version> \
  dist/MP-OSC-<version>-macos-arm64.dmg dist/MP-OSC-<version>-macos-arm64.dmg.sha256 \
  dist/MP-OSC-<version>-macos-arm64.zip dist/MP-OSC-<version>-macos-arm64.zip.sha256 \
  --title "MP-OSC v<version>" --notes "..."
```

`gh` is the GitHub CLI (`brew install gh`, then `gh auth login`). The GitHub web release form works just as well — attach the same four files (the DMG is optional if the build was unsigned). The zip's filename has to stay `MP-OSC-<version>-macos-arm64.zip` exactly, for the updater-compatibility reason above; the DMG's name isn't load-bearing the same way, but keeping it consistent (`MP-OSC-<version>-macos-arm64.dmg`) is what `.github/workflows/release.yml` expects and validates.

### Building a release on GitHub Actions

`.github/workflows/release.yml` runs the same two scripts on a GitHub-hosted
Apple Silicon runner. It is **manual only** — there is no push or tag trigger.
Start it from **Actions → Build macOS release → Run workflow**:

| Input | Default | Effect |
|---|---|---|
| `ref` | the branch the run was started from | Branch, tag or SHA to build |
| `version` | version in `pyproject.toml` | Overrides the version for this build |
| `publish` | off | Create a GitHub Release, not just a build artifact |
| `draft` | on | When publishing, create the release as a draft |

Every run uploads the zip (and the DMG, when the build is signed) plus their
checksums as a build artifact (kept 30 days), so `publish` is only needed
when the build should become a Release.

The runner is `macos-15`, which is arm64. This is not optional: `ndi-python`
publishes only arm64 macOS wheels. The workflow installs Homebrew
`python@3.11` and `python-tk@3.11` for the same reason the local build needs
them — macOS system Pythons link the deprecated `/System/Library` Tcl/Tk 8.5,
which PyInstaller will not bundle.

The `.task` models are cached between runs, keyed on `src/model_downloader.py`,
so only the first run after a model change pays for the ~64 MB download.

#### Signing secrets

With no secrets configured the workflow produces an **ad-hoc signed** build,
exactly like a local `./scripts/release.sh --build`. Add these repository
secrets (Settings → Secrets and variables → Actions) to sign and notarize:

| Secret | Required for | Value |
|---|---|---|
| `MACOS_CERTIFICATE_P12` | Developer ID signing | Base64 of the exported `.p12`: `base64 -i cert.p12 \| pbcopy` |
| `MACOS_CERTIFICATE_PASSWORD` | Developer ID signing | Password set when exporting the `.p12` |
| `MACOS_SIGNING_IDENTITY` | optional | e.g. `Developer ID Application: Your Name (TEAMID)`. Auto-detected from the certificate when unset |
| `APPLE_NOTARY_APPLE_ID` | notarization | Apple ID email |
| `APPLE_NOTARY_PASSWORD` | notarization | App-specific password, not the account password |
| `APPLE_NOTARY_TEAM_ID` | notarization | 10-character Team ID |

Notarization builds on signing rather than being independent of it. The
certificate secrets alone give a Developer ID signed build with the hardened
runtime; adding the three `APPLE_NOTARY_*` secrets on top also notarizes and
staples it. The reverse does not work — Apple only notarizes Developer ID
signed builds — so setting the `APPLE_NOTARY_*` secrets *without* a certificate
skips notarization and logs a warning on the run, rather than submitting an
ad-hoc build that Apple would reject with a confusing signature error.

The certificate is imported into a temporary keychain that exists only for that
job.

### Signing for distribution

An ad-hoc signed build runs on the machine that produced it, but **Gatekeeper rejects it everywhere else**. Recipients see "Apple could not verify this app is free of malware" and have to clear the quarantine flag manually:

```sh
xattr -dr com.apple.quarantine /Applications/MP-OSC.app
```

To ship an app that opens with no extra steps, you need a paid Apple Developer account ($99/year) and a **Developer ID Application** certificate. With one, the release script signs with the hardened runtime and notarizes automatically:

```sh
# One-time: store an app-specific password for notarytool
xcrun notarytool store-credentials mp-osc-notary \
  --apple-id you@example.com --team-id TEAMID --password <app-specific-password>

export MPOSC_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export MPOSC_NOTARY_PROFILE="mp-osc-notary"
./scripts/release.sh --build
```

The hardened runtime entitlements this requires are in `scripts/entitlements.plist`: JIT and unsigned executable memory for TensorFlow Lite, disabled library validation for PyInstaller's bundled dylibs, and camera access.

#### How the bundle is signed

`release.sh` signs **inside-out**, one binary at a time, rather than using `codesign --deep`. Apple documents `--deep` as unsuitable for distribution signing — it applies the top-level entitlements to nested code and quietly skips files it does not recognise as code — while the notary service checks *every* nested Mach-O for the hardened runtime and a secure timestamp and rejects the submission if one is missing either.

That distinction matters here because a PyInstaller bundle of this project carries roughly **176 Mach-O files** (see the count in `scripts/release.sh`'s own comments, which is where this number should be kept up to date rather than asserted separately here): CPython extension modules and dylibs shipped inside third-party wheels, none of them build products this project produced. The script signs each one, then any framework bundles, then the `.app` last. Entitlements are applied only to the outer bundle, since they govern the process that runs rather than the libraries it loads.

Each signature carries `--timestamp`, which is a network round trip to Apple's timestamp authority, so this stage takes a few minutes and needs to be online. It is not optional: without a secure timestamp, signatures stop validating when the certificate expires, and notarization refuses them.

### What recipients need

Nothing — no Python, no uv, no Homebrew, no NDI SDK. The bundle carries its own interpreter, MediaPipe, OpenCV, Tcl/Tk, `libndi.dylib` and all five landmarker models. The only requirements are an **Apple Silicon Mac on macOS 13 or later**. Intel Macs cannot run it, because `ndi-python` publishes no x86_64 macOS wheels.
