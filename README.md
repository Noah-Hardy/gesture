# Gesture

Gesture (formerly MP-OSC) watches a camera or an [NDI](https://ndi.video/) video feed, detects a person's body pose and hand positions in real time with [MediaPipe](https://developers.google.com/mediapipe), and streams the result over [OSC (Open Sound Control)](https://opensoundcontrol.stanford.edu/) to any address on your network. Anything that can receive OSC — TouchDesigner, Max/MSP, Unity, Unreal, Resolume, Ableton — can subscribe to that stream and react to where a person's body and hands are, live.

## Overview

- **Real-time pose and hand tracking**, using MediaPipe's Tasks API with an automatic fallback to its legacy Solutions API if the modern one fails to initialize.
- **Camera or NDI input** — capture from a local webcam, or receive video over the network from an NDI source (a switcher, another Mac, OBS with the NDI plugin, etc).
- **Three OSC output formats**: the original JSON strings (`legacy`, the default through 0.3.x), a leaner bundled JSON (`json`), and one native-float message per landmark (`float`) for receivers like Isadora that bind to numeric OSC arguments. Plus bounds, status, debounced tracking and a 1 Hz heartbeat.
- **Landmark visualization** in a live preview window, purely for your own confirmation — it's not part of what gets sent over the network.
- **A native macOS app**: a dark-themed launcher, a full tabbed Settings window covering everything from tracking thresholds to preview styling, and a self-updater — no Python, no terminal, no dependencies to install.
- **A command-line interface** underneath it all — the app builds a command line from its own form and runs the exact same engine, so nothing behaves differently between the GUI and the CLI.

## Download

Grab the latest release from the [Releases page](https://github.com/Noah-Hardy/gesture/releases).

Gesture requires an **Apple Silicon Mac running macOS 13 or later**. Intel Macs aren't supported — the NDI library Gesture depends on (`ndi-python`) doesn't publish x86_64 wheels for macOS, so there's no way to build an Intel-compatible bundle.

## Install

Open the downloaded `.dmg`, then drag `Gesture.app` into the **Applications** shortcut inside it. Open Gesture from your Applications folder, not from inside the disk image — it works fine there, but the copy on the disk image is read-only, so the in-app updater can't install into it.

Gesture is signed with a Developer ID and notarized by Apple, so it opens with no Gatekeeper warning and no Terminal step.

**Upgrading from 0.2.x?** Let the in-app updater do it. Your settings move from `~/Library/Application Support/mp-osc` to `~/Library/Application Support/Gesture` automatically on first launch, and OSC output doesn't change unless you choose a new format. An install updated from 0.2.1 keeps its old app name for one update and becomes `Gesture.app` on the update after that.

## Quick Start

1. Open Gesture.
2. Under **OSC Output**, set the **Host** (defaults to `127.0.0.1` — leave it alone if the receiver runs on the same Mac) and **Port** your receiving software is listening on.
3. Under **Input**, choose **Camera** or **NDI** and pick a source.
4. Click **Start**.

A preview window, titled "Gesture Preview — not the OSC output", opens showing the camera feed with detected landmarks drawn over it, and the launcher's log pane fills with startup and status messages. The preview is for your own confirmation only — it isn't what gets sent over OSC. See the in-app **Quick Start** guide (Help menu) for the full walkthrough, including a tour of the Settings window.

**Tracking mode** decides what gets tracked and sent:

| Mode | Tracks |
|---|---|
| `pose` | Body pose only |
| `hand` | Both hands only |
| `all` | Pose and both hands together (the default) |

## Updating

Gesture checks GitHub for a newer release a few seconds after it opens, and stays silent if you're already current. When a newer version is available, it offers to download it, verify its checksum and code signature, and swap itself in before relaunching automatically — no manual download required, and no partial or broken state if any step fails along the way. See the in-app **Updates** guide for the full flow and what to do if Gesture can't self-update on your machine (e.g. it's still sitting in Downloads).

## What It Sends

Pose, left-hand and right-hand landmarks each get their own OSC addresses, in one of three formats chosen under **Settings → Advanced → OSC → Output format** (or `--osc-protocol` on the command line):

| Format | What a pose looks like on the wire |
|---|---|
| `legacy` (default) | `/pose/raw` with one JSON string argument, exactly as 0.2.x sent it |
| `json` | `/pose/raw` with a leaner JSON string (a `person` index, no per-landmark `type`/`id`), sent in OSC bundles |
| `float` | `/pose/lm/0` … `/pose/lm/32`, each with four float arguments `x y z visibility`, sent in OSC bundles |

`legacy` stays the default for all of 0.3.x, so existing patches keep working untouched; 0.4.0 moves the default to the new format. Every format also sends:

- **World landmarks**: real-world-scale coordinates in metres, alongside the normalized image-space ones.
- **Bounds**: the extremes of each detection on every axis.
- **Status and tracking**: how many poses/hands the latest detection found, raw per frame and debounced.
- **Heartbeat**: once a second, the engine's FPS and send-queue counters, even when nobody is in frame.

See the in-app **OSC Output** guide for how the formats compare, **OSC Address Reference** for every address and payload, and **TouchDesigner, Max, Unity, Isadora** for receiver setups.

## Configuration

Most settings live in the app itself: OSC host/port, tracking mode, pose model and FPS cap in the main window, and everything else in **Gesture → Settings…**, split across four tabs:

- **General** — the update checker, and shortcuts to `config.json`.
- **Tracking** — pose and hand detection thresholds, smoothing, and how many of each to track.
- **Preview** — whether the preview window shows, mirroring, and landmark/connection colors and sizes.
- **Advanced** — camera capture settings, performance and garbage-collection tuning, the OSC send queue size and output format, and launch-time backend toggles (Force CPU/GPU, legacy API (deprecated), holistic on/off).

Whatever remains reachable only through `config.json`, and the full list of every key the app understands, is documented in the in-app **Appendix: CLI & config.json**, which also covers running Gesture from the command line with flags and environment variables.

## Troubleshooting

The in-app **Troubleshooting** guide (Help menu) is keyed to the exact messages Gesture prints to its log pane, so it's usually the fastest way to figure out what a given warning or error actually means.

## License

This project is based on MediaPipe and is licensed under the Apache License 2.0.

---
#### Author:
Noah Hardy
