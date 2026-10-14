# Settings

**Gesture → Settings…** (⌘,) opens the settings panes in the main window. There are five, in the rail below **Live**: **Input**, **OSC Output**, **Tracking**, **Preview** and **Advanced**. Each field maps to a `config.json` key; the **Appendix** lists them all.

## Pending changes, Apply and Revert

Editing a field does not change the running engine or the configuration file. The change is pending: the field is marked, and a bar at the bottom of the window shows the count with two buttons.

- **Apply** (⌘S, **Engine → Apply Pending Changes**) writes the changes to `config.json` and sends them to the engine. If a pending change needs a restart and the engine is running, the button reads **Apply & restart**.
- **Revert** (⌘Z, **Engine → Revert Changes**) discards every pending change and restores the fields.

Values are validated as you type. An invalid field shows the reason, and **Apply** stays disabled until it is corrected.

## Live and restart settings

Each pane's heading says which kind it contains, and restart-class fields carry a restart marker.

- **Live** settings take effect in the running engine when applied, without interrupting the stream: the OSC host, port, send queue size and tracking hold, the confidence thresholds, the FPS cap, the FPS/stats line, and the Preview pane's drawing options. Changing a confidence threshold rebuilds the landmarkers, which pauses tracking for a few hundred milliseconds.
- **Restart** settings need the engine to restart: the video source and capture options, the OSC output format, the tracking mode, the pose model, the number of poses and hands, and the backend options. When the engine is stopped they apply at the next **Start**.

The engine does not have to be running to apply anything.

## Input

The video source (**Camera** or **NDI**), the device or NDI source, the camera permission state, and the capture width, height, FPS and buffer size. **Processing width** and **Processing height** set the frame size passed to MediaPipe, the main quality and speed trade-off. **Reconnect timeout** sets how long a lost source may take to recover before the engine exits (0 means never). **NDI bandwidth** selects `lowest` (proxy stream) or `highest`. See **Camera & NDI**.

## OSC Output

**Host**, **Port** and **Send queue size** (the number of packets held before the oldest is dropped); the **Output format**: `float` (default), `json` or `legacy` (the 0.2.x format; deprecated, removed in 1.0); and the **Tracking mode** (`pose`, `hand` or `all`). See **OSC Output**.

## Tracking

Detection settings for pose and hands.

- **Pose**: **Model** (lite, full or heavy), **Number of poses**, the **detection**, **tracking** and **pose presence** confidence thresholds.
- **Hands**: **Number of hands**, and the **detection**, **presence** and **tracking** confidence thresholds.

To reduce jitter, raise the tracking confidence. To reduce lost detections in poor lighting, lower the detection confidence.

## Preview

How the preview inside the **Live** pane is drawn: landmark drawing, mirroring, rendering while Gesture is in the background, and landmark and connection colours, thickness and radius. These settings never affect OSC output.

## Advanced

- **Performance**: FPS cap, the FPS/stats line, **Enable garbage collection**, and **Max pending frames** (1 gives the lowest latency). Disabling garbage collection gives the most consistent frame timing, but memory use can grow over long sessions. See **Models & Performance**.
- **Backend**: **Force CPU delegate**, **Force GPU delegate**, and **No holistic** (in `all` mode, use separate pose and hand models instead of the combined holistic model). The GPU delegate leaks memory on Apple Silicon and is not recommended for long sessions.
- **Updates**: **Check for updates on launch** and **Include pre-release builds**. See **Updates**.
- **Configuration file**: reveal or open `config.json`, for keys the panes do not expose.
