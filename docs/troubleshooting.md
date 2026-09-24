# Troubleshooting

The engine's output appears in the launcher's log pane. Messages are listed below by category.

## Camera and NDI

| Message | Meaning |
|---|---|
| `Video capture is not open - nothing to process` | The camera device ID does not exist, or NDI did not connect. Check the device ID and camera permission (**System Settings → Privacy & Security → Camera**). |
| `Camera may be slow to start - continuing anyway` | No frame arrived within about 3 seconds of opening the camera. Usually harmless. If tracking does not start, another application may be using the camera. |
| `Capture stopped delivering frames - retrying` | The source stopped delivering frames. The engine retries and periodically reopens the source; `Capture recovered` follows on success. See **Camera & NDI**. |
| `Capture lost for ...s (... failed reads) - giving up` | The source did not recover within **Reconnect timeout** (default 30 s). Restore the source and click **Start**, or set the timeout to `0` to retry indefinitely. |
| `NDI requested but ndi-python not installed` | This build has no NDI support. Camera input is unaffected. |
| `NDI source unavailable - not falling back to webcam` | NDI did not connect. The preceding line gives the reason: `no NDI sources found`, `no source named '...'`, or `'...' matches more than one source` (use the full name). See **Camera & NDI**. |
| `NDI setup failed` | The NDI library failed to initialize; the error follows. Check that the NDI runtime is installed. |
| `Launcher is gone - stopping` | The launcher exited or crashed, and the engine stopped to release the camera. |
| `Resolution differs from config` | The camera's actual resolution differs from the requested resolution. Frames are still scaled to the processing resolution with the aspect ratio preserved; see **Processing resolution** in **Camera & NDI**. |

## Startup and model loading

| Message | Meaning |
|---|---|
| `Downloading pose model...` / `hand model...` / `holistic model...` | A model file is being downloaded. This requires an internet connection once; models are cached afterwards. The packaged app includes all models, so this appears only when running from source. |
| `Failed to download model` | The model download failed. Check the internet connection and retry. |
| `Model file not available` / `Hand model file not available` / `Holistic model file not available` | A required model file is missing and could not be downloaded. Tracking cannot start. |
| `Cannot initialize pose processing backend` / `hand processing backend` / `any processing backend` | Both the Tasks and legacy MediaPipe APIs failed to initialize. The preceding log lines give the cause. |

## Delegate selection

| Message | Meaning |
|---|---|
| `Apple Silicon detected: Using CPU delegate` | Expected. The GPU delegate is disabled on Apple Silicon because of a MediaPipe memory leak. |
| `GPU delegate failed during initialization` | GPU initialization failed; Gesture uses the CPU instead. |
| `CPU delegate also failed` | Both delegates failed for this component (pose or hand), which is unavailable for this run. |

## During tracking

These errors are recoverable; tracking continues.

| Message | Meaning |
|---|---|
| `Tasks frame processing error` / `Legacy frame processing error` / `Hand frame processing error` / `Holistic frame processing error` | One frame failed to process and was skipped. Occasional occurrences are harmless. If it repeats on every frame, restart the engine. |
| `OSC send error` | A send failed, for example because the destination is unreachable. The packet is counted as dropped. Check the OSC host and port. |

## Configuration

| Message | Meaning |
|---|---|
| `Failed to load config file` | `config.json` could not be parsed, typically because of a JSON syntax error. Defaults are used for this run. Correct the file, or click **Save Config** to overwrite it. |
| `Invalid OSC port` / `Invalid camera device ID` | **Save Config** rejected a non-numeric value. Correct the field and save again. |

## Updates

If a download or installation fails, Gesture reports the error and the installed version is unchanged. See **Updates**.

If Gesture cannot update itself (for example, it is running from `~/Downloads` or from a location the user cannot write to), move it to **Applications** and use **Help → Check for Updates…**, or download the release manually from **Help → Project on GitHub**.

## Incomplete data

Status and bounds channels arrive, but landmark channels (`/pose/raw`, `/left_hand/raw`, `/right_hand/raw` and the world variants) arrive intermittently or not at all. No log message indicates this; compare the receiver's log with the `Sent` count in the stats line (**Show FPS**). Check the following in order:

- **Send queue drops.** With **Show FPS** enabled, `Dropped` should remain constant under normal load. If it rises, increase `osc.queue_size` in **Settings → Advanced** (minimum 32).
- **IP fragmentation.** In `legacy`, a full pose's landmark JSON (about 2.5 KB) exceeds one network packet and is fragmented; the loss of any fragment discards the message, and some receivers do not reassemble fragments. In `json`, `/pose/raw` (about 1.76 KB) is still fragmented. Use `float` (**Settings → Advanced → OSC → Output format**), in which every bundle fits in one packet. The receiver must accept OSC bundles. See **OSC Output**.
- **Payload type.** `legacy` and `json` send a JSON string argument. Receivers that expect numeric arguments, such as Isadora, TouchDesigner's OSC In CHOP and Resolume, cannot use it. Use `float`; see **TouchDesigner, Max, Unity, Isadora**.

When a JSON format is required, setting **Tracking mode** to `pose` or `hand` instead of `all` approximately halves the messages per frame. This reduces, but does not eliminate, both problems.

## Other issues

Note the exact log message and consult the **OSC Address Reference** and **Appendix**, or the project page (**Help → Project on GitHub**).
