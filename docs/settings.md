# Settings

**Gesture → Settings…** (⌘,) opens a window with four tabs covering options not shown in the launcher. Each field maps to a `config.json` key (listed in the **Appendix**). Changes are written only when **Save** is clicked; closing the window discards them. **Restore Defaults** resets every field on every tab, and also takes effect only on **Save**.

## General

- **Check for updates on launch** and **Include pre-release builds**: control the launch update check. See **Updates**.
- **Last checked** and **Check Now**: the time of the last check, and an immediate check.
- **Open config.json** and **Reveal config.json in Finder**: access to the configuration file, for keys this window does not expose.

## Tracking

Detection settings for pose and hands.

- **Pose**: **Model** (lite, full or heavy), **Number of poses**, the **detection**, **tracking** and **pose presence** confidence thresholds, and **Smooth landmarks**.
- **Hands**: **Number of hands**, and the **detection**, **presence** and **tracking** confidence thresholds.

To reduce jitter, raise the tracking confidence or enable landmark smoothing. To reduce lost detections in poor lighting, lower the detection confidence.

## Preview

Preview window visibility, mirroring, title, and landmark drawing: landmark and connection colours, thickness and radius. These settings do not affect OSC output.

## Advanced

- **Camera**: capture width, height, FPS and buffer size. **Processing width** and **Processing height** set the frame size passed to MediaPipe, the main quality and speed trade-off. **Reconnect timeout** sets how long a lost source may take to recover before the engine exits (0 means never). **NDI bandwidth** selects `lowest` (proxy stream) or `highest`. See **Camera & NDI**.
- **Performance**: FPS cap, the FPS/stats log line, **Enable garbage collection**, and **Max pending frames** (1 gives the lowest latency). Disabling garbage collection gives the most consistent frame timing, but memory use can grow over long sessions. See **Models & Performance**.
- **OSC**: send queue size (the number of packets held before the oldest is dropped), and **Output format**: `legacy` (default), `json` or `float`. See **OSC Output**.
- **Backend**: **Force CPU delegate**, **Force GPU delegate**, **Force legacy MediaPipe API** (deprecated), and **No holistic** (in `all` mode, use separate pose and hand models instead of the combined holistic model). The GPU delegate leaks memory on Apple Silicon and is not recommended for long sessions.

Output format and Backend changes apply at the next **Start**.
