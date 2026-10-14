# Quick Start

## Installation

Open the downloaded `.dmg` and drag `Gesture.app` into **Applications**. Run Gesture from the Applications folder. A copy run from the disk image is read-only and cannot be updated in place; Gesture shows a warning at launch in that case.

## The window

Gesture is a single window. A rail on the left switches between panes; a toolbar above them holds the engine controls.

- **Live**: the preview, four meters (frame rate, landmarks sent, detections, send latency) and a column showing the OSC messages being sent. This pane opens at launch.
- **Input**, **OSC Output**, **Tracking**, **Preview**, **Advanced**: settings. See **Settings**.
- **Help**: this documentation.

The toolbar has the **Start** / **Stop** button, the engine state, and read-outs for the tracking mode, the source and the OSC destination. Changes made in a settings pane are not applied until you apply them; while changes are pending, a bar at the bottom of the window shows **Apply** (⌘S) and **Revert** (⌘Z).

The console drawer (⇧⌘C, **View → Toggle Console**) shows the engine's output. It overlays the panes rather than resizing them.

Gesture does not run MediaPipe in the app itself. **Start** launches a separate engine process that captures video and performs tracking; the window shows that process's output and its preview.

## First run

The first time Gesture opens, a short setup asks for camera access, then for the destination (a receiver preset such as Isadora, TouchDesigner or Max/MSP, or a host and port), and offers to start tracking. It can be skipped, and reopened later from **Help → Set Up…**.

Without the setup:

1. Open **OSC Output** and set **Host** to the IP address of the receiving machine. The default, `127.0.0.1`, is correct when the receiver runs on the same Mac. Set **Port** to the port the receiver listens on.
2. Open **Input** and select **Camera** and `Camera 0` (the built-in or first external camera). See **Camera & NDI** for other sources.
3. Leave **Tracking mode** set to `all` to track pose and both hands.
4. Apply the changes (⌘S), then click **Start** (⌘↩).

While the engine starts, a card over the preview steps through `Opening camera…`, `Loading …` and `Warming up…`, and closes at `Ready`. The preview then shows the camera image with the detected landmarks drawn over it. It is for visual confirmation only and is not transmitted. If something goes wrong, the card explains it and links to **Troubleshooting**.

The preview is inside the **Live** pane. There is no separate preview window and no key to press to close one: **Stop** (⌘↩ again) ends tracking.

## Confirming output

With the default `float` format, the OSC column in the **Live** pane lists the address families as they are sent. A receiver logging incoming OSC shows `/gesture/heartbeat` and `/gesture/pose/status` continuously while the engine runs, and `/pose/lm/<n>` while a person is in frame. See **OSC Output** and the **OSC Address Reference**.

If nothing arrives, check that the host and port match the receiver and that no firewall blocks UDP on that port. Gesture cannot detect a closed or busy destination port, because OSC is sent over UDP.

## Saving the configuration

Applied settings are written to `config.json` immediately. Window choices such as the console drawer are saved shortly after they change, and everything is saved on quit and before an update installs. Changes that are still pending are never saved. **File → Reveal config.json** (⇧⌘,) shows the file in Finder.

## Settings

**Gesture → Settings…** (⌘,) opens the settings panes, starting with **Input**. Each pane marks which settings apply live and which need the engine to restart. See **Settings**.

## Updates

Gesture checks for a newer release shortly after launch. If one is available, a card appears in the rail; opening it shows the release notes in an overlay and offers to install. See **Updates**.

## Keyboard

| Key | Action |
|---|---|
| ⌘↩ | Start or stop the engine |
| ⌘R | Restart the engine |
| ⌘S / ⌘Z | Apply / revert pending changes |
| ⌘, | Settings |
| ⌘] / ⌘[ | Next / previous pane |
| ⇧⌘C | Toggle the console |
| ⇧⌘O | Toggle the OSC activity column |
| ⇧⌘L | Copy the console output |
| ⇧⌘, | Reveal `config.json` |
| ⌘? | Documentation |
| ⌘Q | Quit |

## Stopping, the status item and quitting

**Stop** shuts down the engine, which releases the camera and closes the OSC socket. Gesture also adds an item to the macOS menu bar, the status item. It shows whether the engine is streaming, the destination and the frame rate, and offers **Start** / **Stop**, **Open Gesture** and **Quit Gesture**. Closing the window hides it and leaves the engine running; the status item or the Dock icon brings the window back. Quitting (⌘Q, or **Quit Gesture** in the status item) stops the engine first, then exits.
