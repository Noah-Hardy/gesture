# MediaPipe Pose & Hand Tracking with OSC Streaming

## Overview

`main.py` uses [MediaPipe](https://developers.google.com/mediapipe) to perform real-time detection and tracking of human pose and hand landmarks from a webcam or an [NDI](https://ndi.video/) video stream. It visualizes the landmarks on the video feed and streams the landmark data over [OSC (Open Sound Control)](https://opensoundcontrol.stanford.edu/) to a configurable network address, enabling integration with multimedia and creative coding environments (TouchDesigner, Max/MSP, Unity, etc.).

It also calculates pose/hand "bounds" — the landmarks with the minimum and maximum x, y, and z coordinates — and streams them on dedicated OSC channels.

## Features

- **Real-time pose and hand landmark detection** using the MediaPipe Tasks API (with automatic fallback to the legacy MediaPipe Solutions API).
- **NDI input support**: capture directly from NDI sources on the network instead of a camera, with lower latency than NDI virtual cameras.
- **Landmark visualization**: draws landmarks and connections on the video feed, with distinct colors for left/right hands.
- **Threaded OSC streaming**: landmark data is sent as compact JSON on dedicated OSC channels from a background thread so network I/O never blocks frame processing.
- **Bounds calculation**: min/max landmark extremes in x, y, z streamed on separate channels.
- **JSON configuration** (`config.json`) with CLI flag and environment variable overrides.
- **GPU/CPU delegate selection** with an automatic CPU fallback on Apple Silicon (the MediaPipe GPU delegate leaks memory there).
- **macOS application bundle**: a double-clickable `MP-OSC.app` with a settings window for every parameter. The command line interface is unchanged and remains fully supported.

## Prerequisites

- **Python 3.11+** (the project's virtual environment is built on Homebrew Python 3.11 — see [macOS Application](#macos-application))
- **A working webcam** or an NDI source on the network
- **Network access** to the OSC target (if not running locally)
- **[uv](https://github.com/astral-sh/uv) package manager** (recommended)

Building the macOS app bundle has additional requirements — see [macOS Application](#macos-application).

## Setup & Usage

```sh
uv venv
uv sync
uv run python main.py pose
```

The OSC target defaults to the values in `config.json` (`osc.host` / `osc.port`). Override at the command line with `--host` / `--port`.

## macOS Application

`MP-OSC.app` wraps the same engine in a double-clickable bundle. Launching it opens a settings window where the tracking mode, input source, OSC destination, model and performance options are set, then **Start** runs the tracking engine and the preview appears in the usual OpenCV window.

The launcher does not run MediaPipe in-process. It builds a command line from the form and launches the engine as a subprocess of the same executable, so the app and the command line always exercise identical code. **Stop** sends `SIGINT`, which triggers the engine's normal cleanup path.

### Building the app

```sh
./scripts/build_app.sh
open dist/MP-OSC.app
```

The script downloads all five landmarker models so they ship inside the bundle, runs PyInstaller against `mp-osc.spec`, and ad-hoc signs the result. The models are not kept in the repository — they are gitignored downloads of roughly 64 MB, fetched once and reused by later builds. Expect a few minutes and roughly 300 MB in `dist/`.

Build requirements:

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

### Behavior inside the bundle

- Settings are read from and saved to `~/Library/Application Support/mp-osc/config.json`. Running from a terminal still uses `config.json` in the working directory, exactly as before.
- All five landmarker models are bundled, so nothing is downloaded at runtime.
- The bundle declares `NSCameraUsageDescription`, so macOS prompts once for camera access. If tracking never starts, check **System Settings → Privacy & Security → Camera**.
- NDI discovery uses Bonjour, declared via `NSLocalNetworkUsageDescription` and `NSBonjourServices`, so the first NDI refresh may raise a local network prompt.
- The app is ad-hoc signed for personal use. Distributing it to other machines additionally requires a Developer ID, the hardened runtime, and notarization — see below.

## Releasing

`scripts/release.sh` packages the built app into a distributable archive:

```sh
./scripts/release.sh --build     # build, then package
```

This produces `dist/MP-OSC-<version>-macos-arm64.zip` and a matching `.sha256`. The archive is created with `ditto`, which is the only macOS archiver that reliably preserves bundle structure and code signatures — a plain `zip` corrupts the signature. The script prints the current Gatekeeper status and the commands to publish.

To publish on GitHub:

```sh
git tag -a v0.1.0 -m "MP-OSC v0.1.0"
git push origin v0.1.0
gh release create v0.1.0 dist/MP-OSC-0.1.0-macos-arm64.zip dist/MP-OSC-0.1.0-macos-arm64.zip.sha256 \
  --title "MP-OSC v0.1.0" --notes "..."
```

`gh` is the GitHub CLI (`brew install gh`, then `gh auth login`). The GitHub web release form works just as well — attach the same two files.

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

Every run uploads the zip and its checksum as a build artifact (kept 30 days),
so `publish` is only needed when the build should become a Release.

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

That distinction matters here because the bundle contains **178 Mach-O files**: CPython extension modules and dylibs shipped inside third-party wheels, none of them build products this project produced. The script signs each one, then any framework bundles, then the `.app` last. Entitlements are applied only to the outer bundle, since they govern the process that runs rather than the libraries it loads.

Each signature carries `--timestamp`, which is a network round trip to Apple's timestamp authority, so this stage takes a few minutes and needs to be online. It is not optional: without a secure timestamp, signatures stop validating when the certificate expires, and notarization refuses them.

### What recipients need

Nothing — no Python, no uv, no Homebrew, no NDI SDK. The bundle carries its own interpreter, MediaPipe, OpenCV, Tcl/Tk, `libndi.dylib` and all five landmarker models. The only requirements are an **Apple Silicon Mac on macOS 13 or later**. Intel Macs cannot run it, because `ndi-python` publishes no x86_64 macOS wheels.

## Command Line Options

A tracking mode is required as the positional argument:
- `pose` — track body pose landmarks
- `hand` — track hand landmarks
- `all` — track both simultaneously (uses the MediaPipe Holistic Landmarker: pose + hands in a single model pass; falls back to separate pose + hand landmarkers if holistic setup fails, `num_poses > 1` is configured, or `--no-holistic` is passed)

```sh
# Show help
python main.py --help

# Pose tracking only
python main.py pose

# Both pose and hand tracking (holistic landmarker, one model pass)
python main.py all

# Both, but with separate pose + hand landmarkers instead of holistic
python main.py all --no-holistic

# Pose model selection (lite = fastest, full = balanced, heavy = most accurate)
# If omitted, the value from config.json (mediapipe.pose_model_type) is used
python main.py pose --pose-model full

# Show FPS / memory / OSC stats counter
python main.py pose --fps

# Use specific camera device
python main.py hand --camera 1

# NDI input
python main.py all --ndi --ndi-source "My NDI Source"
python main.py --list-ndi          # discover NDI sources and exit

# Cap the frame rate for stability
python main.py all --fps-cap 30

# Delegate control
python main.py pose --force-cpu    # force CPU delegate
python main.py hand --force-gpu    # force GPU (warning: memory leak on Apple Silicon)
python main.py pose --force-legacy # force legacy MediaPipe Solutions API

# OSC target override
python main.py all --host 192.168.1.100 --port 9000

# Config file management
python main.py pose --config myconfig.json
python main.py pose --create-config
python main.py pose --show-config
```

Press `q` in the video window to quit.

### Environment Variable Overrides

`MP_OSC_HOST`, `MP_OSC_PORT`, `MP_CAMERA_ID`, `MP_CAMERA_WIDTH`, `MP_CAMERA_HEIGHT`, `MP_SHOW_FPS`, `MP_PREFER_GPU`, `MP_MIN_DETECTION_CONFIDENCE`, `MP_MIN_TRACKING_CONFIDENCE`

## OSC Message Structure

All payloads are compact JSON strings. Coordinates are normalized and rounded to 3 decimal places.

### Pose Channels

| Channel | Content |
|---|---|
| `/pose/raw` | Landmarks for one detected pose (normalized image coords) |
| `/pose/world` | World-space landmarks for one detected pose |
| `/pose/raw_bounds` | Min/max landmark extremes for one pose |
| `/pose/world_bounds` | World-space bounds for one pose |
| `/pose/multi_raw` | All detected poses combined (`poses`, `count`) |
| `/pose/multi_world` | All world landmarks combined |
| `/pose/multi_raw_bounds` | Bounds for all poses combined |
| `/pose/multi_world_bounds` | World bounds for all poses combined |
| `/mp/status` | `{"status": N}` — number of poses currently detected |

**Landmark payload** (`/pose/raw`, `/pose/world`):
```json
{
  "timestamp": 1720000000.123,
  "landmarks": [
    {"type": "pose_0", "id": 0, "x": 0.52, "y": 0.48, "z": -0.12, "visibility": 0.98}
  ]
}
```

**Bounds payload** (`/pose/raw_bounds`):
```json
{
  "max_x": {"id": 23, "x": 0.9, "y": 0.5, "z": -0.1, "visibility": 0.99},
  "min_x": {"id": 11, "x": 0.1, "y": 0.6, "z": -0.2, "visibility": 0.98},
  "max_y": {"id": 27, "x": 0.5, "y": 0.95, "z": -0.3, "visibility": 0.97},
  "min_y": {"id": 0,  "x": 0.4, "y": 0.05, "z": -0.4, "visibility": 0.96},
  "max_z": {"id": 12, "x": 0.6, "y": 0.4, "z": 0.2, "visibility": 0.97},
  "min_z": {"id": 5,  "x": 0.3, "y": 0.7, "z": -0.5, "visibility": 0.95}
}
```

### Hand Channels

Each detected hand is routed by handedness:

| Channel | Content |
|---|---|
| `/left_hand/raw`, `/right_hand/raw` | Landmarks for that hand (includes `handedness`) |
| `/left_hand/world`, `/right_hand/world` | World-space landmarks for that hand |
| `/left_hand/bounds`, `/right_hand/bounds` | Min/max extremes for that hand |
| `/left_hand/world_bounds`, `/right_hand/world_bounds` | World-space bounds |
| `/hand/multi_raw` | All hands combined (`hands`, `handedness`, `count`) |
| `/hand/multi_bounds` | Bounds for all hands combined |
| `/hand/status` | `{"status": N}` — number of hands currently detected |

**Hand payload** (`/left_hand/raw`):
```json
{
  "timestamp": 1720000000.123,
  "handedness": "Left",
  "landmarks": [
    {"type": "hand_0", "id": 0, "x": 0.52, "y": 0.48, "z": -0.12, "visibility": null}
  ]
}
```

When tracking is lost, an empty-landmark payload and empty bounds `{}` are sent once on the affected channels so receivers can clear stale data, and the status channels report `0`.

## Configuration

See `config.json`. Key sections:

- `osc` — target host/port and send-queue size
- `camera` — device id, capture and processing resolution, NDI options (`use_ndi`, `ndi_source`)
- `mediapipe` — pose model type (`lite`/`full`/`heavy`), confidences, `num_poses`
- `hand` — hand count, confidences, left/right display colors
- `performance` — FPS cap (`target_fps`, 0 = uncapped), FPS display, garbage-collection tuning
- `display` — window visibility/title, landmark drawing style

## Troubleshooting

- If the webcam does not open, ensure no other application is using it and try a different `--camera` index.
- If no NDI sources are found, check that the sender and receiver are on the same network/subnet and that mDNS is not blocked.
- If OSC messages are not received, check your firewall, network, and OSC receiver address/port.
- On Apple Silicon the CPU delegate is used by default because the MediaPipe GPU delegate has a known memory leak; `--force-gpu` overrides this at your own risk.
- MediaPipe 0.10.21's holistic landmarker aborts the process (SIGABRT, "The packet is empty") when a person is detected while the hand sub-graphs have not produced output yet — typically the instant someone enters frame. `src/holistic_processor.py` patches the upstream result builder to treat empty streams as absent landmarks. If that patch ever stops applying after a MediaPipe upgrade, `--no-holistic` avoids the affected code path entirely.

## License

This project is based on MediaPipe and is licensed under the Apache License 2.0.

---
#### Author:
Noah Hardy
