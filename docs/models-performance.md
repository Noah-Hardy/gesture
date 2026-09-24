# Models & Performance

## Pose model

| Model | Characteristics |
|---|---|
| `lite` | Fastest, least accurate |
| `full` | Balanced |
| `heavy` | Slowest, most accurate |

The pose model affects body pose detection only. Hand tracking uses MediaPipe's hand landmarker. In `all` mode with the default Holistic model, a single combined model handles pose and hands, and this setting has no effect.

## FPS cap

A blank or `0` **FPS cap** runs uncapped. A value such as `30` limits how often frames are read from the source, giving more consistent timing downstream at the cost of maximum responsiveness. The cap limits capture rate, not inference time; a slow model can still fall behind (see **Skipped frames**).

## Show FPS

**Show FPS** (**Settings → Advanced**) writes a stats line to the log approximately every 30 frames:

```
CPU (MediaPipe Tasks) FPS: 28.41 | Memory: 412.3MB | OSC Sent: 8420 Dropped: 0 Queued: 2 | MP Pending: 0 Skipped: 3
```

- **FPS**: frames processed per second.
- **Memory**: resident memory of the engine process.
- **OSC Sent / Dropped / Queued**: packets sent, packets dropped because the send queue was full (see **OSC Output**), and packets currently queued.
- **MP Pending / Skipped**: see **Skipped frames**.

## Skipped frames

Detection runs asynchronously from capture. When the number of frames in progress reaches **Max pending frames**, new frames are skipped and the `Skipped` counter increases. No landmark or status messages are sent for a skipped frame. A steadily rising `Skipped` count indicates that the model, not the network, is the bottleneck. Use a lighter pose model or change the delegate.

## Max pending frames

**Max pending frames** (**Settings → Advanced → Performance**, `performance.max_pending_frames`, default and minimum 1) is the number of frames MediaPipe may process concurrently. `1` gives the lowest latency. Higher values can increase throughput on fast machines, at the cost of added latency.

## Delegate selection

Gesture selects the delegate automatically. Apple Silicon Macs use the CPU, because MediaPipe's GPU delegate leaks memory on Apple Silicon. **Force CPU delegate** and **Force GPU delegate** (**Settings → Advanced**) override this at the next **Start**. Force CPU is saved to `config.json`; Force GPU is not.

## Force legacy MediaPipe API

**Deprecated.** `--force-legacy` will be removed in a future release.

This option switches from MediaPipe's Tasks API to the older synchronous Solutions API. It disables GPU acceleration, limits pose detection to one person, and replaces the Holistic model in `all` mode with separate models. Gesture falls back to this API automatically if the Tasks API fails to initialize, so manual selection is rarely needed.

The `mediapipe.model_complexity`, `mediapipe.enable_segmentation`, `mediapipe.smooth_landmarks` and `hand.model_complexity` keys apply only to the legacy API.

## Garbage collection

After the models load, Gesture performs one full garbage collection and freezes the loaded objects, so the collector does not traverse MediaPipe and OpenCV state during tracking. `performance.gc_interval` is no longer used.

**Enable garbage collection** (`performance.gc_enabled`, on by default) can be disabled to stop automatic collection for the session. This gives the most consistent frame timing, but memory use can grow over long sessions.
