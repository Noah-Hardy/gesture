# Models & Performance

## Pose model

The **Pose model** dropdown trades accuracy for speed:

| Model | Character |
|---|---|
| `lite` | Fastest, least accurate |
| `full` | Balanced — a reasonable default |
| `heavy` | Slowest, most accurate |

This only affects **body pose** detection. Hand tracking always uses MediaPipe's single hand-landmarker model, and in `all` mode's default holistic path, one combined model handles both pose and hands regardless of this setting.

## FPS cap

Leave **FPS cap** blank or `0` to run uncapped (as fast as the camera and models allow). Setting a number (e.g. `30`) throttles how often frames are pulled from the camera/NDI source, which can produce steadier, more predictable timing for downstream animation — at the cost of a hard ceiling on responsiveness. This caps capture rate, not model inference time directly; a slow model can still fall behind an uncapped or generously capped rate (see **Dropped frames**, below).

## Show FPS

**Show FPS**, in **Settings → Advanced**, prints a stats line to the log pane roughly every 30 frames:

```
CPU (MediaPipe Tasks) FPS: 28.41 | Memory: 412.3MB | OSC Sent: 8420 Dropped: 0 Queued: 2 | MP Pending: 0 Skipped: 3
```

- **FPS** — frames actually processed per second.
- **Memory** — the tracking process's resident memory.
- **OSC Sent / Dropped / Queued** — how many OSC messages have gone out, how many were dropped because the send queue was full (see **OSC Output**), and how many are queued right now.
- **MP Pending / Skipped** — see **Dropped frames**, below.

## Dropped frames

MediaPipe's detector runs asynchronously from frame capture, and only one frame is ever allowed to be "in flight" being processed at a time. If a new frame arrives before the previous one has finished, that new frame is **skipped entirely** — not queued, not sent — and the `Skipped` counter in the FPS line increments. Critically, **no OSC message of any kind is sent for a skipped frame** (not even a status message), so a `Skipped` count that's climbing relative to your total frame count means the model is the bottleneck, not the network or the OSC queue. Choosing a lighter pose model, or forcing CPU vs. GPU (see below), is the usual fix.

## Force CPU / Force GPU delegate

By default, MP-OSC picks a delegate automatically: **Apple Silicon Macs always use CPU**, because MediaPipe's GPU delegate has a known memory leak on Apple Silicon. **Force CPU**, **Force GPU** and **Force Legacy** live in **Settings → Advanced**, labeled "applies on next Start" since they're launch-time only — they take effect the next time you click Start, not while the engine is already running. Force CPU and Force Legacy are saved to `config.json`; Force GPU is not (it's mutually exclusive with Force CPU and carries its own memory-leak warning right on the checkbox — use it deliberately, not as a default).

## Force Legacy

**Deprecated:** `--force-legacy` will be removed in 0.2.0 once the legacy MediaPipe Solutions API is deleted.

**Force Legacy** switches from MediaPipe's modern "Tasks" API to its older synchronous API. This disables GPU acceleration entirely, limits pose detection to a single person, and disables the combined holistic model in `all` mode (falling back to two separate legacy models). It exists as a compatibility fallback — MP-OSC already falls back to it automatically if the modern API fails to initialize — and normally shouldn't need to be checked by hand.

The `mediapipe.model_complexity`, `mediapipe.enable_segmentation`, `mediapipe.smooth_landmarks`, and `hand.model_complexity` config keys are read only by the legacy processors — they have no effect on the default Tasks path and will become dead weight once the legacy path is removed in 0.2.0.

## Garbage collection and memory

**Enable garbage collection** and **GC interval (frames)**, in **Settings → Advanced**, control periodic Python garbage collection during tracking (`config.json`'s `performance.gc_enabled` and `performance.gc_interval`). Disabling it can produce smoother, more consistent frame timing at the cost of higher memory use over a long-running session.
