# Appendix: CLI & config.json

Running the engine from the command line, configuration reference, and building from source.

## Precedence

Settings resolve in this order, highest first: command-line flags, environment variables, `config.json`, built-in defaults. The app's **Start** button builds a command line from the configuration and runs the same engine.

## Command-line flags

```
python main.py {pose,hand,all} [options]
```

The positional `mode` argument is required.

| Flag | Effect |
|---|---|
| `--host HOST` | OSC destination host |
| `--port PORT` | OSC destination port |
| `--osc-protocol {legacy,json,float}` | OSC output format (default `float`); see **OSC Output** |
| `--camera N` | Camera device ID |
| `--ndi` | Use NDI input |
| `--ndi-source NAME` | NDI source name (case-insensitive; a partial name must match exactly one source) |
| `--list-ndi` | List NDI sources and exit |
| `--pose-model {lite,full,heavy}` | Pose model |
| `--fps-cap N` | Frame rate cap (0 or omitted: uncapped) |
| `--fps` | Print the FPS/stats line |
| `--mirror` / `--no-mirror` | Mirror the preview (display only) |
| `--preview-window` | Show a local OpenCV preview window (command line only; the app draws its preview in the Live pane). `--preview` is the same flag; `--no-preview` is accepted and does nothing |
| `--no-holistic` | In `all` mode, use separate pose and hand models |
| `--force-cpu` | Force the CPU delegate |
| `--force-gpu` | Force the GPU delegate (leaks memory on Apple Silicon) |
| `--config PATH` | Use the specified configuration file |
| `--create-config` | Write a default `config.json` and exit |
| `--show-config` | Print the resolved configuration and exit |

Flags override `config.json`. `python main.py --help` lists the current flags.

## config.json

Most keys are editable in the settings panes (**Input**, **OSC Output**, **Tracking**, **Preview**, **Advanced**). Keys without a pane are available only in the file.

| Section | Keys |
|---|---|
| `osc` | `host`, `port`, `queue_size` (packets queued before the oldest is dropped; minimum 32), `protocol` (`float`, `json` or `legacy`; unknown values fall back to `float`), `protocol_migrated` (set by the one-time 0.4.0 migration; do not edit), `tracking_hold` (seconds, default 0.3; **Advanced** pane) |
| `camera` | `device_id`, `width` / `height` (capture resolution), `processing_width` / `processing_height` (see **Camera & NDI**), `use_ndi`, `ndi_source`, `ndi_bandwidth` (`lowest` or `highest`, default `lowest`), `reconnect_timeout` (seconds, default 30, `0` for no limit) |
| `mediapipe` | `pose_model_type`, `num_poses` (values above 1 disable the Holistic model in `all` mode), confidence thresholds |
| `hand` | `num_hands`, confidence thresholds, preview colours |
| `performance` | `target_fps`, `show_fps`, `gc_enabled`, `max_pending_frames` (default and minimum 1); see **Models & Performance** |
| `display` | `mirror_preview`, `draw_landmarks`, `render_in_background`, preview colours and stroke sizes |
| `updates` | Update checker state; see **Updates** |

`config.json` is created the first time settings are saved. The packaged app stores it at `~/Library/Application Support/Gesture/config.json`; when running from source, it is in the working directory. Missing keys use the defaults in `DEFAULT_CONFIG` in `src/config.py`.

## Settings reference

Every setting in the panes, with its `config.json` key (`section.key`). **Restart** settings are read when the engine starts; **live** settings apply to a running engine when applied; **app** settings are read by the app itself, not the engine. This table is generated from the panes by `scripts/gen_config_table.py`; do not edit it by hand.

<!-- BEGIN GENERATED: config-table (scripts/gen_config_table.py) -->

| Key | Pane | Type | Range or options | Default | Restart |
|---|---|---|---|---|---|
| `camera.use_ndi` | Input | choice | `false` (Camera), `true` (NDI) | `false` | restart |
| `camera.device_id` | Input | choice | device index | `0` | restart |
| `camera.ndi_source` | Input | choice | source name | `""` | restart |
| `camera.ndi_bandwidth` | Input | choice | `lowest`, `highest` | `lowest` | restart |
| `camera.width` | Input | integer | 160 to 3840 | `640` | restart |
| `camera.height` | Input | integer | 120 to 2160 | `480` | restart |
| `camera.fps` | Input | integer | 1 to 240 | `30` | restart |
| `camera.processing_width` | Input | integer | 160 to 3840 | `640` | restart |
| `camera.processing_height` | Input | integer | 120 to 2160 | `480` | restart |
| `camera.buffer_size` | Input | integer | 1 to 16 | `1` | restart |
| `camera.reconnect_timeout` | Input | integer | 0 to 3600 | `30` | restart |
| `osc.host` | OSC Output | string | host name or IP address | `127.0.0.1` | live |
| `osc.port` | OSC Output | integer | 1 to 65535 | `1234` | live |
| `osc.queue_size` | OSC Output | integer | 32 to 1000 | `32` | live |
| `osc.protocol` | OSC Output | choice | `legacy`, `json`, `float` | `float` | restart |
| `ui.mode` | OSC Output | choice | `pose`, `hand`, `all` | `all` | restart |
| `mediapipe.pose_model_type` | Tracking | choice | `lite`, `full`, `heavy` | `lite` | restart |
| `mediapipe.num_poses` | Tracking | integer | 1 to 4 | `1` | restart |
| `mediapipe.min_detection_confidence` | Tracking | number | 0 to 1 | `0.7` | live |
| `mediapipe.min_tracking_confidence` | Tracking | number | 0 to 1 | `0.5` | live |
| `mediapipe.min_pose_presence_confidence` | Tracking | number | 0 to 1 | `0.5` | live |
| `hand.num_hands` | Tracking | integer | 0 to 2 | `2` | restart |
| `hand.min_detection_confidence` | Tracking | number | 0 to 1 | `0.5` | live |
| `hand.min_presence_confidence` | Tracking | number | 0 to 1 | `0.5` | live |
| `hand.min_tracking_confidence` | Tracking | number | 0 to 1 | `0.5` | live |
| `display.draw_landmarks` | Preview | boolean |  | `true` | live |
| `display.mirror_preview` | Preview | boolean |  | `false` | live |
| `display.render_in_background` | Preview | boolean |  | `false` | live |
| `display.landmark_color` | Preview | colour (BGR list) |  | `[245, 117, 66]` | live |
| `display.landmark_thickness` | Preview | integer | 1 to 10 | `1` | live |
| `display.landmark_radius` | Preview | integer | 1 to 20 | `2` | live |
| `display.connection_color` | Preview | colour (BGR list) |  | `[245, 66, 230]` | live |
| `display.connection_thickness` | Preview | integer | 1 to 10 | `1` | live |
| `display.connection_radius` | Preview | integer | 1 to 20 | `1` | live |
| `performance.target_fps` | Advanced | integer | 0 to 240 | `0` | live |
| `performance.show_fps` | Advanced | boolean |  | `false` | live |
| `performance.gc_enabled` | Advanced | boolean |  | `true` | restart |
| `performance.max_pending_frames` | Advanced | integer | 1 to 8 | `1` | restart |
| `osc.tracking_hold` | Advanced | number | 0 to 5 | `0.3` | live |
| `performance.force_cpu` | Advanced | boolean |  | `false` | restart |
| `performance.force_gpu` | Advanced | boolean |  | `false` | restart |
| `performance.no_holistic` | Advanced | boolean |  | `false` | restart |
| `ui.launch_animation` | Advanced | boolean |  | `true` | app |
| `updates.check_on_launch` | Advanced | boolean |  | `true` | app |
| `updates.include_prereleases` | Advanced | boolean |  | `false` | app |

<!-- END GENERATED: config-table -->

Keys that have no pane, such as `ui.*` window state and `updates` bookkeeping, are listed in the section table above.

## Environment variables

| Variable | Key |
|---|---|
| `GESTURE_OSC_HOST` | `osc.host` |
| `GESTURE_OSC_PORT` | `osc.port` |
| `GESTURE_CAMERA_ID` | `camera.device_id` |
| `GESTURE_CAMERA_WIDTH`, `GESTURE_CAMERA_HEIGHT` | `camera.width`, `camera.height` |
| `GESTURE_SHOW_FPS` | `performance.show_fps` |
| `GESTURE_MIRROR_PREVIEW` | `display.mirror_preview` |
| `GESTURE_MIN_DETECTION_CONFIDENCE`, `GESTURE_MIN_TRACKING_CONFIDENCE` | `mediapipe.min_detection_confidence`, `mediapipe.min_tracking_confidence` |

The pre-rename names (the same names with an `MP_` prefix in place of `GESTURE_`) are still accepted. When both are set, the `GESTURE_` name takes precedence.

## Shutdown

The engine shuts down on Ctrl-C or `SIGTERM`, and on `q` in the `--preview-window` window. It sends the clear messages described in **OSC Output** and flushes the send queue before exiting. When started by the app, the engine is connected to it by a private socket and exits within about one second if that socket closes, that is, if the app quits or crashes.

## Running from source

```sh
uv venv
uv sync
uv run python main.py all
```

`uv run python app.py` with no arguments opens the app window. This is the packaged app's entry point: it opens the window when run without arguments and the engine otherwise.

## Building the macOS app

```sh
./scripts/build_app.sh
```

This downloads the landmarker models and produces an ad-hoc-signed `dist/Gesture.app`. On other machines, such a build requires removing the quarantine attribute (`xattr -dr com.apple.quarantine`). Distribution without that step requires a Developer ID certificate and notarization; see `docs/BUILDING.md`.
