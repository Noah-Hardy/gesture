# Quick Start

## Installing

Open the `.dmg` you downloaded, then drag `Gesture.app` into the **Applications** shortcut inside it, and open Gesture from your Applications folder — not from inside the disk image. The copy on the disk image is read-only, so it works for a quick look but the in-app updater can't install into it; Gesture warns about this once at launch if you do.

## The launcher window

Opening Gesture.app shows one window with three collapsible sections, then Start/Save Config/Clear Log buttons, a live log of the engine's output, and a status line. Click a section's ▾/▸ header to expand or collapse it — the app remembers each section's open/closed state between launches.

- **Input** (open by default) — tracking mode, and camera or NDI source
- **OSC Output** (open by default) — the host and port Gesture sends to
- **Model & Performance** (collapsed by default) — pose model and FPS cap, plus a pointer to **Gesture → Settings…** for everything else

Most of what used to be scattered checkboxes now lives in the **Settings** window (see below) — the main window stays short, with only the handful of fields you're likely to touch every session.

The launcher itself never runs MediaPipe. Pressing **Start** launches a second, hidden process that does the actual camera capture and tracking — its console output is what you see streaming into the log pane. **Stop** shuts that process down cleanly.

## First run

1. Set **OSC Output → Host** to the IP address of the machine that will receive the data. It defaults to `127.0.0.1` (localhost) — leave it as-is if Gesture and the receiver run on the same machine.
2. Set **Port** to whatever your receiving software is listening on.
3. Under **Input**, pick **📷 Camera** and leave the device ID at `0` (your Mac's built-in or first external camera), or see **Camera & NDI** for other options.
4. Leave **Tracking mode** on `all` — it tracks pose and both hands together.
5. Click **Start**.

The log pane will fill with startup messages: which delegate (CPU or GPU) was chosen, which model loaded, and a confirmation line once tracking begins. A preview window opens separately, titled **"Gesture Preview — not the OSC output"** — a reminder that this window is for your own confirmation only, showing the camera feed with the detected skeleton drawn over it, and is never itself what gets sent over OSC. Press `q` with that window focused to stop tracking early, or use the launcher's **Stop** button (the Start button turns into Stop, in red, while the engine is running).

Uncheck **🖼️ Show preview window** under **Input** if you don't want the window to open at all — for example, running headless, or if you find it distracting. With the preview hidden, `q` has nothing to be pressed in, so **Stop** is the only way to end tracking.

## Confirming data is arriving

If your receiving software can log incoming OSC messages, you should see something arrive on `/mp/status` continuously once the engine starts, and on `/pose/raw`, `/left_hand/raw`, and `/right_hand/raw` whenever a person is in frame. See **OSC Output** for what these actually contain, and the **OSC Address Reference** for the complete list.

If nothing arrives, double-check the host/port match your receiver, and that no firewall on either machine is blocking UDP traffic on that port.

## Save Config

Gesture reopens the way you last used it. Every time you click **Start**, quit, or let an update install, it saves the main window's fields to `config.json`, including the tracking mode. **Save Config** saves those fields straight away, and tells you if the port or camera ID isn't a valid number.

Everything set in the **Settings** window is saved there directly, as soon as you click its own Save button — you don't need to also click the main window's Save Config for those fields.

## Settings

**Gesture → Settings…** (⌘,) opens a separate window with four tabs — General, Tracking, Preview, and Advanced — covering update behavior, detection thresholds, preview styling, camera/performance tuning, and the launch-time backend toggles (Force CPU, Force GPU, Force Legacy (deprecated), No Holistic) that used to live as checkboxes in this window. See the **Settings** guide for a tour of each tab.

## Updates

Gesture checks GitHub for a newer release a few seconds after it opens, silently — if you're already current, nothing happens. If a newer version is available, a dialog shows the release notes with the option to install and relaunch automatically. See the **Updates** guide for the full flow, and how to check manually.

## Stopping and quitting

**Stop** (or ⌘.) asks the tracking process to shut down cleanly — it releases the camera, closes the OSC connection, and closes the preview window. Quitting the app entirely (⌘Q, or the red close button) does the same thing first, then closes the launcher.
