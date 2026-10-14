# Gesture

Gesture tracks body pose and hand landmarks from a camera or [NDI](https://ndi.video/) video feed using [MediaPipe](https://developers.google.com/mediapipe), and sends the results over [OSC](https://opensoundcontrol.stanford.edu/) in real time. Any OSC-capable application, such as TouchDesigner, Max/MSP, Unity, Unreal, Resolume or Ableton, can receive the stream.

## Features

- Real-time pose and hand tracking with the MediaPipe Tasks API.
- Camera or NDI input.
- Three OSC output formats: `float` (one numeric message per landmark, for receivers such as Isadora; the default since 0.4.0), `json` (compact bundled JSON) and `legacy` (the 0.2.x JSON strings; deprecated). Bounds, status, debounced tracking and a 1 Hz heartbeat are sent in every format.
- One native window: a live preview with landmark overlay, meters and an OSC activity column, settings panes with Apply / Revert, a console drawer and a built-in updater.
- A macOS menu bar, keyboard shortcuts and a status-bar item that keeps the engine running with the window closed.
- A command-line interface that runs the same engine as the app.

## Requirements

Apple Silicon Mac running macOS 13 or later.

## Installation

Download the latest `.dmg` from the [Releases page](https://github.com/Noah-Hardy/gesture/releases), open it, and drag `Gesture.app` into **Applications**. The app is signed and notarized.

## Quick start

1. Open Gesture.
2. In the **OSC Output** pane, set **Host** (default `127.0.0.1`, for a receiver on the same Mac) and **Port** to match the receiver.
3. In the **Input** pane, select **Camera** or **NDI** and a source.
4. Apply the changes (⌘S) and click **Start** (⌘↩). The preview appears in the **Live** pane.

The first time Gesture opens, a short setup does this for you (**Help → Set Up…** reopens it).

**Tracking mode** selects what is tracked:

| Mode | Tracks |
|---|---|
| `pose` | Body pose |
| `hand` | Both hands |
| `all` | Body pose and both hands (default) |

The in-app **Quick Start** guide (**Help** menu) covers this in detail.

## Menus and keyboard

The menu bar has Gesture, File, Engine, View, Window and Help menus. The main shortcuts:

| Key | Action |
|---|---|
| ⌘↩ | Start or stop the engine |
| ⌘R | Restart the engine |
| ⌘S / ⌘Z | Apply / revert pending changes |
| ⌘, | Settings |
| ⌘] / ⌘[ | Next / previous pane |
| ⇧⌘C | Toggle the console |
| ⇧⌘O | Toggle the OSC activity column |

A status-bar item shows whether the engine is streaming, the destination and the frame rate. It can start and stop the engine and reopen the window. Closing the window leaves the engine running; **Quit Gesture** stops it.

## Updates

Gesture checks for new releases at launch and can install them in place. See the in-app **Updates** guide.

## OSC output

Pose, left-hand and right-hand landmarks are sent on separate addresses, in the format selected in the **OSC Output** pane or with `--osc-protocol`:

| Format | Pose encoding |
|---|---|
| `float` (default) | `/pose/lm/0` to `/pose/lm/32`, each with float arguments `x y z visibility`, in OSC bundles |
| `json` | `/pose/raw` with compact JSON (a `person` index; no per-landmark `type` or `id`), in OSC bundles |
| `legacy` (deprecated) | `/pose/raw` with one JSON string argument |

All formats also send:

- **World landmarks**: coordinates in metres, alongside normalized image coordinates.
- **Bounds**: the extent of each detection on each axis.
- **Status and tracking**: the per-frame and debounced counts of detected poses and hands.
- **Heartbeat**: engine FPS and send-queue counters, once per second.

The in-app guides **OSC Output**, **OSC Address Reference** and **TouchDesigner, Max, Unity, Isadora** cover format selection, every address and payload, and receiver setup.

## Configuration

Settings are in five panes, opened from the rail or with **Gesture → Settings…** (⌘,). Changes are pending until applied with **Apply** (⌘S), or discarded with **Revert** (⌘Z). Settings that can change in a running engine apply live; the rest restart it.

- **Input**: camera or NDI source, capture and processing resolution, reconnect timeout.
- **OSC Output**: host, port, send queue, output format and tracking mode.
- **Tracking**: pose model, detection thresholds, and the number of poses and hands.
- **Preview**: preview drawing, mirroring and landmark styling.
- **Advanced**: performance, backend options, update checks and access to `config.json`.

The in-app **Appendix: CLI & config.json** documents every configuration key, command-line flag and environment variable.

## Troubleshooting

When the engine cannot start, a card over the preview says why and links to the in-app **Troubleshooting** guide, which also explains the messages shown in the console.

## License

Based on MediaPipe. Licensed under the Apache License 2.0.

---
#### Author:
Noah Hardy
