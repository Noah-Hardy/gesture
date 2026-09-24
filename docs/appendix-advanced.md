# Appendix: CLI & config.json

Running the engine from the command line, configuration reference, and building from source.

## Precedence

Settings resolve in this order, highest first: command-line flags, environment variables, `config.json`, built-in defaults. The launcher's **Start** button builds a command line from its fields and runs the same engine.

## Command-line flags

```
python main.py {pose,hand,all} [options]
```

The positional `mode` argument is required.

| Flag | Effect |
|---|---|
| `--host HOST` | OSC destination host |
| `--port PORT` | OSC destination port |
| `--osc-protocol {legacy,json,float}` | OSC output format (default `legacy`); see **OSC Output** |
| `--camera N` | Camera device ID |
| `--ndi` | Use NDI input |
| `--ndi-source NAME` | NDI source name (case-insensitive; a partial name must match exactly one source) |
| `--list-ndi` | List NDI sources and exit |
| `--pose-model {lite,full,heavy}` | Pose model |
| `--fps-cap N` | Frame rate cap (0 or omitted: uncapped) |
| `--fps` | Print the FPS/stats line |
| `--mirror` / `--no-mirror` | Mirror the preview (display only) |
| `--preview` / `--no-preview` | Show or hide the preview window |
| `--no-holistic` | In `all` mode, use separate pose and hand models |
| `--force-cpu` | Force the CPU delegate |
| `--force-gpu` | Force the GPU delegate (leaks memory on Apple Silicon) |
| `--force-legacy` | Use the MediaPipe Solutions API (deprecated) |
| `--config PATH` | Use the specified configuration file |
| `--create-config` | Write a default `config.json` and exit |
| `--show-config` | Print the resolved configuration and exit |

Flags override `config.json`. `python main.py --help` lists the current flags.

## config.json

Most keys are editable in the **Settings** window. OSC host and port, tracking mode, pose model and FPS cap are in the launcher. Some keys, such as `osc.tracking_hold`, are available only in the file.

| Section | Keys |
|---|---|
| `osc` | `host`, `port`, `queue_size` (packets queued before the oldest is dropped; minimum 32), `protocol` (`legacy`, `json` or `float`; unknown values fall back to `legacy`), `tracking_hold` (optional; tracking hold time in seconds, default 0.3) |
| `camera` | `device_id`, `width` / `height` (capture resolution), `processing_width` / `processing_height` (see **Camera & NDI**), `use_ndi`, `ndi_source`, `ndi_bandwidth` (`lowest` or `highest`, default `lowest`), `reconnect_timeout` (seconds, default 30, `0` for no limit) |
| `mediapipe` | `pose_model_type`, `num_poses` (Tasks API; values above 1 disable the Holistic model in `all` mode), confidence thresholds, `model_complexity` / `enable_segmentation` / `smooth_landmarks` (legacy API only) |
| `hand` | `num_hands`, confidence thresholds, preview colours, `model_complexity` (legacy API only) |
| `performance` | `target_fps`, `show_fps`, `gc_enabled`, `max_pending_frames` (default and minimum 1); see **Models & Performance** |
| `display` | `show_window`, `window_title`, `mirror_preview`, preview colours and stroke sizes |
| `updates` | Update checker state; see **Updates** |

`config.json` is created the first time settings are saved. The packaged app stores it at `~/Library/Application Support/Gesture/config.json`; when running from source, it is in the working directory. Missing keys use the defaults in `DEFAULT_CONFIG` in `src/config.py`.

## Environment variables

| Variable | Key |
|---|---|
| `GESTURE_OSC_HOST` (or `MP_OSC_HOST`) | `osc.host` |
| `GESTURE_OSC_PORT` (or `MP_OSC_PORT`) | `osc.port` |
| `MP_CAMERA_ID` | `camera.device_id` |
| `MP_CAMERA_WIDTH`, `MP_CAMERA_HEIGHT` | `camera.width`, `camera.height` |
| `MP_SHOW_FPS` | `performance.show_fps` |
| `MP_MIRROR_PREVIEW` | `display.mirror_preview` |
| `MP_MIN_DETECTION_CONFIDENCE`, `MP_MIN_TRACKING_CONFIDENCE` | `mediapipe.min_detection_confidence`, `mediapipe.min_tracking_confidence` |

## Shutdown

The engine shuts down on `q` in the preview window, Ctrl-C or `SIGTERM`. It sends the clear messages described in **OSC Output** and flushes the send queue before exiting. When started by the launcher, the engine also exits within about one second if the launcher exits or crashes.

## Running from source

```sh
uv venv
uv sync
uv run python main.py all
```

`uv run python app.py` with no arguments opens the launcher. This is the packaged app's entry point: it opens the launcher when run without arguments and the engine otherwise.

## Building the macOS app

```sh
./scripts/build_app.sh
```

This downloads the landmarker models and produces an ad-hoc-signed `dist/Gesture.app`. On other machines, such a build requires removing the quarantine attribute (`xattr -dr com.apple.quarantine`). Distribution without that step requires a Developer ID certificate and notarization; see `docs/BUILDING.md`.
