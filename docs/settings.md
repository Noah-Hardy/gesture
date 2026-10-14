# Settings

**Gesture → Settings…** (⌘,) opens the settings panes in the main window. There are five, in the rail below **Live**: **Input**, **OSC Output**, **Tracking**, **Preview** and **Advanced**. Each field maps to a `config.json` key; the **Appendix** lists them all.

## Pending changes, Apply and Revert

Editing a field does not change the running engine or the configuration file. The change is pending, and a bar at the bottom of the window summarises it, for example `2 changes pending · 1 needs a restart` or `1 change pending · applies live`, and lists the first few fields by pane and label. The bar has two buttons.

- **Apply** (⌘S, **Engine → Apply Pending Changes**) writes the changes to `config.json` and sends the live ones to the engine. If a pending change needs a restart and the engine is running, the button reads **Apply & restart**, and the engine restarts.
- **Revert** (⌘Z, **Engine → Revert Changes**) discards every pending change and restores the fields.

Values are validated as you type. An invalid field shows the reason, the bar says how many fields are invalid, and **Apply** stays disabled until they are corrected.

## Live and restart settings

Each pane's heading says which kind it mostly contains, and restart settings carry a restart marker.

- **Live** settings take effect in the running engine when applied, without interrupting the stream: OSC **Host**, **Port** and **Send queue size**, the confidence thresholds, **Target FPS**, **Show FPS / stats line**, **Tracking hold**, and the Preview pane's drawing options. Changing a confidence threshold rebuilds the landmarkers, which pauses tracking for a few hundred milliseconds. The update checks in **Advanced** are read by the app and need no restart.
- **Restart** settings are read when the engine starts: everything in **Input**, the **Output format** and **Tracking mode**, the pose model, the number of poses and hands, garbage collection, **Max pending frames** and the backend options. When the engine is stopped they apply at the next **Start**.

The engine does not have to be running to apply anything. The **Appendix** has a table of every setting with its range and whether it is live or restart.

## Input

All of **Input** needs a restart.

- **Source**: **Camera** or **NDI**. The device list or the source list in the other row is dimmed while it is not in use.
- **Camera device**: listed as `Camera N — name`, for example `Camera 0 — FaceTime HD Camera`.
- **NDI source**: `Any (first found)` or a source by name. The list fills in as sources are found; `No sources found` shows until one appears.
- **NDI bandwidth**: `lowest` (a proxy stream of about 640×360, plenty for tracking) or `highest`.
- **Camera permission**: whether macOS has allowed camera access. It is checked on every start.
- **Capture width**, **height** and **FPS**, and **Buffer size** (minimum 1).
- **Processing width** and **Processing height**: the frame size passed to MediaPipe, the main quality and speed trade-off.
- **Reconnect timeout**: seconds a lost source may take to recover before the engine exits; `0` retries indefinitely.

See **Camera & NDI**.

## OSC Output

**Host**, **Port** (1 to 65535) and **Send queue size** (the number of packets held before the oldest is dropped) apply live. **Output format** (`Legacy (0.2.x)`, `JSON` or `Float`) and **Tracking mode** (`pose`, `hand` or `all`) need a restart.

Below them, an **Address map** and a **Payload sample** show what will be sent for the pending format and mode, so they change as you edit those two fields. See **OSC Output** and the **OSC Address Reference**.

## Tracking

Detection settings for pose and hands. The thresholds apply live; the models and counts need a restart.

- **Pose**: **Model** (`lite`, `full` or `heavy`), **Number of poses** (1 to 4; more than 1 disables the combined holistic model), and the **detection**, **tracking** and **pose presence** confidence thresholds.
- **Frame cost on this machine**: click **Measure** to time each model on a still image (**Measure again** repeats it). It is measured once per model and your camera feed will differ, but it shows how much slower `full` and `heavy` are on this Mac.
- **Hands**: **Number of hands** (0 to 2), and the **detection**, **presence** and **tracking** confidence thresholds.

To reduce jitter, raise the tracking confidence. To reduce lost detections in poor lighting, lower the detection confidence.

## Preview

How the preview inside the **Live** pane is drawn, all live: **Draw landmarks over the feed**, **Mirror preview**, **Render preview in background** (tracking continues either way), and the landmark and connection **colour**, **thickness** and **radius**. These settings never affect OSC output.

## Advanced

- **Performance**: **Target FPS** (`0` is uncapped, otherwise 1 to 240), **Show FPS / stats line**, **Enable garbage collection**, **Max pending frames** (1 is the lowest latency), and **Tracking hold**, the number of seconds a tracking count holds after detection stops reporting it (default 0.3, range 0 to 5). Disabling garbage collection gives the most consistent frame timing, but memory use can grow over long sessions. See **Models & Performance**.
- **Backend** (restart): **Force CPU delegate**, **Force GPU delegate**, and **Separate pose and hand models** (in `all` mode, use separate models instead of the combined holistic model). The GPU delegate leaks memory on Apple Silicon and is not recommended for long sessions.
- **Updates**: **Check for updates on launch** and **Include pre-release builds**. See **Updates**.
- **config.json**: the path of the configuration file, for keys the panes do not expose.
