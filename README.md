# MP-OSC

MP-OSC watches a camera or an [NDI](https://ndi.video/) video feed, detects a person's body pose and hand positions in real time with [MediaPipe](https://developers.google.com/mediapipe), and streams the result over [OSC (Open Sound Control)](https://opensoundcontrol.stanford.edu/) to any address on your network. Anything that can receive OSC — TouchDesigner, Max/MSP, Unity, Unreal, Resolume, Ableton — can subscribe to that stream and react to where a person's body and hands are, live.

## Overview

- **Real-time pose and hand tracking**, using MediaPipe's Tasks API with an automatic fallback to its legacy Solutions API if the modern one fails to initialize.
- **Camera or NDI input** — capture from a local webcam, or receive video over the network from an NDI source (a switcher, another Mac, OBS with the NDI plugin, etc).
- **Compact JSON over OSC**, on dedicated channels for pose and each hand, plus bounds channels reporting the min/max extremes of each detection.
- **Landmark visualization** in a live preview window, purely for your own confirmation — it's not part of what gets sent over the network.
- **A native macOS app**: a dark-themed launcher, a full tabbed Settings window covering everything from tracking thresholds to preview styling, and a self-updater — no Python, no terminal, no dependencies to install.
- **A command-line interface** underneath it all — the app builds a command line from its own form and runs the exact same engine, so nothing behaves differently between the GUI and the CLI.

## Download

Grab the latest release from the [Releases page](https://github.com/Noah-Hardy/mp-osc/releases).

MP-OSC requires an **Apple Silicon Mac running macOS 13 or later**. Intel Macs aren't supported — the NDI library MP-OSC depends on (`ndi-python`) doesn't publish x86_64 wheels for macOS, so there's no way to build an Intel-compatible bundle.

## Install

Drag `MP-OSC.app` into your **Applications** folder and open it.

If you see a Gatekeeper warning ("Apple could not verify this app is free of malware"), that means you have an ad-hoc-signed build rather than a notarized one. Clear the quarantine flag and it opens normally after that:

```sh
xattr -dr com.apple.quarantine /Applications/MP-OSC.app
```

## Quick Start

1. Open MP-OSC.
2. Under **OSC Output**, set the **Host** (defaults to `127.0.0.1` — leave it alone if the receiver runs on the same Mac) and **Port** your receiving software is listening on.
3. Under **Input**, choose **Camera** or **NDI** and pick a source.
4. Click **Start**.

A preview window opens showing the camera feed with detected landmarks drawn over it, and the launcher's log pane fills with startup and status messages. The preview is for your own confirmation only — it isn't what gets sent over OSC. See the in-app **Quick Start** guide (Help menu) for the full walkthrough, including a tour of the Settings window.

**Tracking mode** decides what gets tracked and sent:

| Mode | Tracks |
|---|---|
| `pose` | Body pose only |
| `hand` | Both hands only |
| `all` | Pose and both hands together (the default) |

## Updating

MP-OSC checks GitHub for a newer release a few seconds after it opens, and stays silent if you're already current. When a newer version is available, it offers to download it, verify its checksum and code signature, and swap itself in before relaunching automatically — no manual download required, and no partial or broken state if any step fails along the way. See the in-app **Updates** guide for the full flow and what to do if MP-OSC can't self-update on your machine (e.g. it's still sitting in Downloads).

## What It Sends

Every OSC message is a single string argument containing compact JSON, not separate float/int arguments — for example:

```
/pose/raw   "{\"timestamp\":1720000000.123,\"landmarks\":[...]}"
```

Pose, left-hand, and right-hand landmarks each get their own address, alongside a few other channel types:

- **Raw and world landmarks** — normalized image-space coordinates, and separately, real-world-scale coordinates.
- **Bounds** — the min/max landmark extremes for each detection, one message per axis pair.
- **Status** — how many poses/hands the most recently finished detection found, sent on every processed frame.

See the in-app **OSC Address Reference** for every address and exact payload shape, and **TouchDesigner, Max, Unity** for patterns specific to those three receivers.

## Configuration

Most settings live in the app itself: OSC host/port, tracking mode, pose model and FPS cap in the main window, and everything else in **mp-osc → Settings…**, split across four tabs:

- **General** — the update checker, and shortcuts to `config.json`.
- **Tracking** — pose and hand detection thresholds, smoothing, and how many of each to track.
- **Preview** — whether the preview window shows, mirroring, and landmark/connection colors and sizes.
- **Advanced** — camera capture settings, performance and garbage-collection tuning, the OSC send queue size, and launch-time backend toggles (Force CPU/GPU, legacy API (deprecated), holistic on/off).

Whatever remains reachable only through `config.json`, and the full list of every key the app understands, is documented in the in-app **Appendix: CLI & config.json**, which also covers running MP-OSC from the command line with flags and environment variables.

## Troubleshooting

The in-app **Troubleshooting** guide (Help menu) is keyed to the exact messages MP-OSC prints to its log pane, so it's usually the fastest way to figure out what a given warning or error actually means.

## License

This project is based on MediaPipe and is licensed under the Apache License 2.0.

---
#### Author:
Noah Hardy
