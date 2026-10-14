# Gesture 0.4.0 release notes

0.4.0 replaces the four Tk windows and the separate preview window with one native window, and changes the default OSC format. The sections on the OSC format and network packet size come last, because they are the ones that can affect existing receivers.

## One window

- **One window.** The launcher, Settings, Help and update dialogs, and the OpenCV preview window are now a single Qt window. A rail switches between **Live**, **Input**, **OSC Output**, **Tracking**, **Preview**, **Advanced** and **Help**.
- **Live pane.** The preview is drawn inside the window, letterboxed, with chips for the source and mirroring. Four meters show frame rate, landmarks sent, detections and send latency, and a column lists the OSC messages as they are sent.
- **Fault cards.** When the engine is starting, reconnecting, cannot find the camera or NDI source, has been denied camera access, or is stopped, a card over the preview says so and links to the matching **Troubleshooting** section. See **Troubleshooting**.
- **Startup phases.** Starting shows `Opening camera…`, `Loading …`, `Warming up…` and `Ready` in order, both on the card and in the console. The models are warmed up before `Ready`. The old `Engine ready` line is gone.
- **Settings panes with Apply and Revert.** Edits are pending until you apply them (⌘S) or revert them (⌘Z). Settings that a running engine can change (OSC host, port and queue size, confidence thresholds, FPS cap, preview drawing) apply live; the rest restart the engine. See **Settings**.
- **First-run setup.** A short setup asks for camera access and the OSC destination, with presets for Isadora, TouchDesigner and Max/MSP. **Help → Set Up…** reopens it. Upgrades from earlier versions skip it.
- **Menus and keyboard.** A native menu bar, shortcuts for the main actions (see **Quick Start**), and a status-bar item that shows whether Gesture is streaming and lets you start, stop and reopen the window. Closing the window leaves the engine running; **Quit Gesture** stops it.
- **Console drawer.** The engine output overlays the panes (⇧⌘C) instead of squeezing them.
- **Update overlay.** The update dialog is now an overlay in the window. **Help → Check for Updates…** is unchanged.
- **Launch animation and new icon.** A short animation plays every time Gesture opens and expands into the window; click or press a key to skip it, or turn it off under Advanced > Interface. It is skipped when macOS Reduce Motion is on. The app also has a new icon.
- **Preview window on the command line.** `--preview-window` opens the OpenCV preview when running the engine without the app. `--preview` is the same flag, and `--no-preview` is accepted and does nothing. The `q` key applies only to that window.
- **Engine and app connection.** The engine reports status and preview frames over a private socket and exits within about one second if the app goes away.

## Removed

- **The legacy MediaPipe API.** Gesture uses the MediaPipe Tasks API only. `--force-legacy` and the setting behind it are gone, and the unused keys are dropped from `config.json` the next time it is saved.
- **The Tk launcher**, with its collapsible sections, **Save Config** and **Clear Log** buttons. The same settings are in the panes.

## The default OSC format is now `float`

Since 0.4.0 the default OSC output format is `float` (one numeric message per landmark, in OSC bundles). In 0.3.x it was `legacy` (the 0.2.x JSON strings).

**Existing installs are migrated once.** 0.3.x saved `"protocol": "legacy"` into almost every `config.json`, whether or not the user chose it, so changing the default alone would only reach fresh installs. On first launch, 0.4.0 switches a config that has `legacy` saved to `float`, records `osc.protocol_migrated: "0.4.0"` so it never repeats, and logs the change. The app then offers **Keep Legacy (0.2.x) format** once. After that, choosing `legacy` again is respected and is never migrated. Configs set to `json` or `float`, and fresh installs, are not touched. `--osc-protocol` still overrides the config for a single run.

If a receiver was built for 0.2.x (addresses such as `/mp/status`), either update it to the `float` or `json` addresses (see the OSC Address Reference), or switch back in the **OSC Output** pane or with `--osc-protocol legacy`.

## Deprecation: `legacy` is removed in 1.0

The `legacy` format is deprecated. It stays available, byte-identical to 0.2.x, throughout the 0.x series (0.4 and later) and is removed in 1.0.

## `json` and the network packet size

The `json` wire format is unchanged from 0.3.x, so existing `json` receivers keep working. As in 0.3.x, `/pose/raw` (about 1.76 KB for a full 33-landmark pose) is the one `json` address larger than a single packet and is IP-fragmented; every other address fits in one 1400-byte bundle. On Wi-Fi or other lossy networks, use `float`.
