# Gesture

Gesture tracks body pose and hand landmarks from a camera or [NDI](https://ndi.video/) video feed using [MediaPipe](https://developers.google.com/mediapipe), and sends the results over [OSC](https://opensoundcontrol.stanford.edu/) in real time. Any OSC-capable application, such as TouchDesigner, Max/MSP, Unity, Unreal, Resolume or Ableton, can receive the stream.

## Features

- Real-time pose and hand tracking with the MediaPipe Tasks API, with automatic fallback to the Solutions API.
- Camera or NDI input.
- Three OSC output formats: `legacy` (JSON strings, the default), `json` (compact bundled JSON) and `float` (one numeric message per landmark, for receivers such as Isadora). Bounds, status, debounced tracking and a 1 Hz heartbeat are sent in every format.
- A preview window with landmark overlay.
- A native macOS launcher with a Settings window and built-in updater.
- A command-line interface that runs the same engine as the launcher.

## Requirements

Apple Silicon Mac running macOS 13 or later.

## Installation

Download the latest `.dmg` from the [Releases page](https://github.com/Noah-Hardy/gesture/releases), open it, and drag `Gesture.app` into **Applications**. The app is signed and notarized.

## Quick start

1. Open Gesture.
2. Under **OSC Output**, set **Host** (default `127.0.0.1`, for a receiver on the same Mac) and **Port** to match the receiver.
3. Under **Input**, select **Camera** or **NDI** and a source.
4. Click **Start**.

**Tracking mode** selects what is tracked:

| Mode | Tracks |
|---|---|
| `pose` | Body pose |
| `hand` | Both hands |
| `all` | Body pose and both hands (default) |

The in-app **Quick Start** guide (**Help** menu) covers this in detail.

## Updates

Gesture checks for new releases at launch and can install them in place. See the in-app **Updates** guide.

## OSC output

Pose, left-hand and right-hand landmarks are sent on separate addresses, in the format selected under **Settings → Advanced → OSC → Output format** or with `--osc-protocol`:

| Format | Pose encoding |
|---|---|
| `legacy` (default) | `/pose/raw` with one JSON string argument |
| `json` | `/pose/raw` with compact JSON (a `person` index; no per-landmark `type` or `id`), in OSC bundles |
| `float` | `/pose/lm/0` to `/pose/lm/32`, each with float arguments `x y z visibility`, in OSC bundles |

All formats also send:

- **World landmarks**: coordinates in metres, alongside normalized image coordinates.
- **Bounds**: the extent of each detection on each axis.
- **Status and tracking**: the per-frame and debounced counts of detected poses and hands.
- **Heartbeat**: engine FPS and send-queue counters, once per second.

The in-app guides **OSC Output**, **OSC Address Reference** and **TouchDesigner, Max, Unity, Isadora** cover format selection, every address and payload, and receiver setup.

## Configuration

The launcher holds OSC host and port, tracking mode, pose model and FPS cap. **Gesture → Settings…** contains the remaining options:

- **General**: update checks and access to `config.json`.
- **Tracking**: detection thresholds, smoothing, and the number of poses and hands.
- **Preview**: preview visibility, mirroring and landmark styling.
- **Advanced**: camera capture, performance, OSC queue size and output format, and backend options.

The in-app **Appendix: CLI & config.json** documents every configuration key, command-line flag and environment variable.

## Troubleshooting

The in-app **Troubleshooting** guide explains the messages shown in the log pane.

## License

Based on MediaPipe. Licensed under the Apache License 2.0.

---
#### Author:
Noah Hardy
