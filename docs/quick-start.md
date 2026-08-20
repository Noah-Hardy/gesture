# Quick Start

## The launcher window

Opening MP-OSC.app shows one window with three sections and a log pane:

- **Input** — tracking mode, and camera or NDI source
- **OSC Output** — the host and port MP-OSC sends to
- **Model & Performance** — pose model, FPS cap, and a few toggles
- **▶ Start / 💾 Save Config / Clear Log** buttons, then a live log of the engine's output

The launcher itself never runs MediaPipe. Pressing **Start** launches a second, hidden process that does the actual camera capture and tracking — its console output is what you see streaming into the log pane. **Stop** shuts that process down cleanly.

## First run

1. Set **OSC Output → Host** to the IP address of the machine that will receive the data. If MP-OSC and the receiver are running on the same machine, use `127.0.0.1`.
2. Set **Port** to whatever your receiving software is listening on.
3. Under **Input**, pick **📷 Camera** and leave the device ID at `0` (your Mac's built-in or first external camera), or see **Camera & NDI** for other options.
4. Leave **Tracking mode** on `all` — it tracks pose and both hands together.
5. Click **▶ Start**.

The log pane will fill with startup messages: which delegate (CPU or GPU) was chosen, which model loaded, and a confirmation line once tracking begins. A preview window opens separately, showing the camera feed with the detected skeleton drawn over it — this is for your own confirmation, and is not what gets sent over OSC. Press `q` with that window focused to stop tracking early, or use the launcher's **⏹ Stop** button.

## Confirming data is arriving

If your receiving software can log incoming OSC messages, you should see something arrive on `/mp/status` continuously once the engine starts, and on `/pose/raw`, `/left_hand/raw`, and `/right_hand/raw` whenever a person is in frame. See **OSC Output** for what these actually contain, and the **OSC Address Reference** for the complete list.

If nothing arrives, double-check the host/port match your receiver, and that no firewall on either machine is blocking UDP traffic on that port.

## Save Config

**💾 Save Config** writes your Host, Port, Camera/NDI selection, pose model, FPS cap and Show FPS setting to `config.json`, so the next time you open MP-OSC these fields are already filled in. **Tracking mode and the Force CPU / Force Legacy / No Holistic toggles are launch-only** — they apply to the run you're about to start, but are not saved. The log pane confirms this every time you save.

## Stopping and quitting

**⏹ Stop** (or ⌘.) asks the tracking process to shut down cleanly — it releases the camera, closes the OSC connection, and closes the preview window. Quitting the app entirely (⌘Q, or the red close button) does the same thing first, then closes the launcher.
