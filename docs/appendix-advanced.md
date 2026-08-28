# Appendix: CLI & config.json

This appendix is for running MP-OSC without the launcher window — from the command line, or building the app from source. Everything above this page describes the launcher; nothing here is required to use it.

## Precedence

Settings are resolved in this order, highest priority first: **command-line flags** > **environment variables** > **`config.json`** > **built-in defaults**. The launcher's Start button builds a command line from the form and launches the same engine you'd run by hand — so the GUI and the CLI are always the same code path.

## Command line flags

```
python main.py {pose,hand,all} [options]
```

The positional `mode` argument is required and selects `pose`, `hand`, or `all` tracking.

| Flag | Effect |
|---|---|
| `--host HOST` | OSC destination host (overrides `config.json`) |
| `--port PORT` | OSC destination port (overrides `config.json`) |
| `--camera N` | Camera device ID (overrides `config.json`) |
| `--ndi` | Use NDI input instead of the camera |
| `--ndi-source NAME` | NDI source name to connect to (substring match) |
| `--list-ndi` | List available NDI sources on the network and exit |
| `--pose-model {lite,full,heavy}` | Pose model accuracy/speed tradeoff |
| `--fps-cap N` | Cap the frame rate (0 or omitted = uncapped) |
| `--fps` | Show the FPS/stats line in the console |
| `--mirror` / `--no-mirror` | Mirror the preview window (display only; overrides `config.json`) |
| `--preview` / `--no-preview` | Show or hide the preview window (overrides `config.json`) |
| `--no-holistic` | In `all` mode, use separate pose + hand models instead of the combined holistic model |
| `--force-cpu` | Force the CPU delegate |
| `--force-gpu` | Force the GPU delegate (has a known memory leak on Apple Silicon — use with caution) |
| `--force-legacy` | Use MediaPipe's older synchronous API instead of the modern Tasks API (**deprecated** — removed in a future release) |
| `--config PATH` | Use a specific configuration file instead of `config.json` |
| `--create-config` | Write a default `config.json` and exit |
| `--show-config` | Print the fully-resolved configuration and exit |

Run `python main.py --help` for the authoritative, always-current list.

## config.json

The **Settings** window now exposes almost everything in this file directly — Tracking, Preview and Advanced between them cover most sections below. Only OSC host/port, tracking mode, pose model and FPS cap stay in the main launcher window; a small handful of things (like `camera.processing_width`/`processing_height`) exist only as raw config keys, with no field anywhere in the UI. Key sections:

| Section | Notable keys |
|---|---|
| `osc` | `host`, `port`, `queue_size` (outgoing message queue depth before drops begin — see **OSC Output**) |
| `camera` | `device_id`, `width`/`height` (capture resolution), `processing_width`/`processing_height` (see **Processing resolution** in **Camera & NDI** — not exposed in Settings), `use_ndi`, `ndi_source` |
| `mediapipe` | `pose_model_type`, `num_poses` (Tasks API only; `>1` disables the combined holistic model in `all` mode), detection/tracking confidence thresholds, `model_complexity`/`enable_segmentation`/`smooth_landmarks` (**Legacy API only** — see `--force-legacy`; dead weight once the legacy path is removed in a future release) |
| `hand` | `num_hands`, confidence thresholds, left/right landmark and connection colors used in the preview, `model_complexity` (**Legacy API only** — same future removal as above) |
| `performance` | `target_fps`, `show_fps`, `gc_enabled`/`gc_interval` (see **Models & Performance**) |
| `display` | `show_window`, `window_title`, `mirror_preview`, landmark/connection colors and stroke sizes used in the preview |
| `updates` | Update-checker state — see the **Updates** guide |

`config.json` is not part of the repository or the app bundle — it's written the first time you save something from the launcher or Settings, at `~/Library/Application Support/mp-osc/config.json` in the packaged app (or `config.json` in the working directory when running from source). A fresh clone or a fresh install has no config file at all until then; every key falls back to the built-in default shown in `src/config.py`'s `DEFAULT_CONFIG`, which matches this appendix.

Environment variable overrides exist for a handful of the most common settings: `MP_OSC_HOST`, `MP_OSC_PORT`, `MP_CAMERA_ID`, `MP_CAMERA_WIDTH`, `MP_CAMERA_HEIGHT`, `MP_SHOW_FPS`, `MP_MIRROR_PREVIEW`, `MP_MIN_DETECTION_CONFIDENCE`, `MP_MIN_TRACKING_CONFIDENCE`.

## Running from source

```sh
uv venv
uv sync
uv run python main.py all
```

The launcher window itself is `uv run python app.py` with no arguments — the same entry point the packaged `.app` uses, which dispatches to the launcher when given no command-line arguments and to the tracking engine otherwise.

## Building the macOS app

```sh
./scripts/build_app.sh
```

Downloads every landmarker model, then produces an ad-hoc-signed `dist/MP-OSC.app`. Distributing that build to another machine requires either clearing the quarantine flag by hand (`xattr -dr com.apple.quarantine`) or, for a build that opens with no extra steps, a paid Apple Developer ID certificate and notarization — see `scripts/release.sh` and `docs/BUILDING.md` for the full signing and notarization process.
