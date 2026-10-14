# Troubleshooting

The engine's output appears in the console drawer (**View → Toggle Console**, ⇧⌘C). When something is wrong with the engine, a card appears over the Live preview; its **Learn more** link opens the matching section below. The sections that follow list log messages by category.

## Starting

The card **Starting** shows while the engine opens the source and loads models. It steps through the same phases that the console logs:

| Console line | Meaning |
|---|---|
| `Opening camera…` (or `Connecting to NDI source…`) | The engine is opening the camera, or connecting to the NDI sender. |
| `Loading …` (for example `Loading pose model (lite)…`) | The model files named on the card are being loaded. The first start after an install can take a few seconds longer. |
| `Warming up…` | The models are loaded and the first frames are being processed. |
| `Ready` | Tracking has started and the card closes. |

If a step takes more than about 30 seconds, click **Stop** in the toolbar and start again. If it stalls at `Opening camera…`, another application may be holding the camera; see **Camera not found**. Models are bundled with the packaged app; when running from source a missing model is downloaded first (see **Startup and model loading**).

## Reconnecting

The card **Reconnecting** shows when the source stops delivering frames after tracking had started. The engine keeps retrying, reopens the source periodically, and the OSC heartbeat continues. Tracking resumes on its own when frames return and the console logs `Capture recovered`. Click **Stop** to give up.

The engine exits on its own after **Reconnect timeout** (**Input** pane, default 30 seconds; `0` retries indefinitely). Common causes are a camera cable or hub, a sleeping laptop, and an NDI sender that was stopped or moved to another network. See **Camera & NDI**.

## Camera not found

The card **Camera not found** (or **NDI source not found** when the input is NDI) appears when the engine could not open the selected source. The engine has stopped.

- **Camera.** Check that the device is connected and that no other application is using it. Device ID `0` is the first camera; external and Continuity cameras use `1`, `2` and so on. The card offers **Rescan devices** and **Use NDI instead**.
- **NDI.** Check that the sender is running on the same network and that the **NDI source** name matches exactly one source. The card offers **Rescan sources** and **Use a camera instead**. Matching rules are in **Camera & NDI**.

Both actions open the **Input** pane where the source can be changed. Apply the change, then start the engine.

## Camera access needed

The card **Camera access needed** appears when macOS has denied Gesture access to the camera. Click **Open System Settings**, turn on Gesture under **Privacy & Security → Camera**, and click **Recheck**. macOS may ask you to quit and reopen Gesture before the change takes effect. The permission is checked on every start.

## Stopped

The card **Stopped** shows while the engine is not running: before the first start, after **Stop**, and after the engine exits. Nothing is tracked or sent, and the OSC heartbeat is silent. Changes in the settings panes apply when you start. Click **Start engine**, or use the toolbar or ⌘↩.

If the engine stopped on its own, the console shows why (for example `Capture lost for …s - giving up`, or a model initialisation error). A camera problem keeps its own card (**Camera not found** or **Camera access needed**) instead of **Stopped**.

OSC output is a UDP send with no connection, so a busy or closed destination port cannot be detected: nothing in Gesture reports it, and a receiver that is not listening produces no error. If data does not arrive, check the host and port in the **OSC Output** pane and the receiver's own log.

## Camera and NDI

| Message | Meaning |
|---|---|
| `Camera <n> could not be opened` / `Camera access is denied` | The source could not be opened or macOS denied access. The matching card appears; see **Camera not found** and **Camera access needed**. |
| `Video capture is not open - nothing to process` | The camera device ID does not exist, or NDI did not connect. Check the device ID and camera permission (**System Settings → Privacy & Security → Camera**). |
| `Camera may be slow to start - continuing anyway` | No frame arrived within about 3 seconds of opening the camera. Usually harmless. If tracking does not start, another application may be using the camera. |
| `Capture stopped delivering frames - retrying` | The source stopped delivering frames. The engine retries and periodically reopens the source; `Capture recovered` follows on success. See **Camera & NDI**. |
| `Capture lost for ...s (... failed reads) - giving up` | The source did not recover within **Reconnect timeout** (default 30 s). Restore the source and start the engine, or set the timeout to `0` to retry indefinitely. |
| `NDI requested but ndi-python not installed` | This build has no NDI support. Camera input is unaffected. |
| `NDI source unavailable - not falling back to webcam` | NDI did not connect. The preceding line gives the reason: `no NDI sources found`, `no source named '...'`, or `'...' matches more than one source` (use the full name). See **Camera & NDI**. |
| `NDI setup failed` | The NDI library failed to initialize; the error follows. Check that the NDI runtime is installed. |
| `Launcher is gone - stopping` | The app exited or crashed, and the engine stopped to release the camera. The engine watches its connection to the app; when that closes it exits within about one second. |
| `Resolution differs from config` | The camera's actual resolution differs from the requested resolution. Frames are still scaled to the processing resolution with the aspect ratio preserved; see **Processing resolution** in **Camera & NDI**. |

## Startup and model loading

| Message | Meaning |
|---|---|
| `Downloading pose model...` / `hand model...` / `holistic model...` | A model file is being downloaded. This requires an internet connection once; models are cached afterwards. The packaged app includes all models, so this appears only when running from source. |
| `Failed to download model` | The model download failed. Check the internet connection and retry. |
| `Pose model file not available` / `Hand model file not available` / `Holistic model file not available` | A required model file is missing and could not be downloaded. Tracking cannot start. |
| `Cannot initialize pose processing backend` / `hand processing backend` / `any processing backend` | The MediaPipe Tasks landmarker failed to initialize. The preceding log lines give the cause. |

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
| `Pose frame processing error` / `Hand frame processing error` / `Holistic frame processing error` / `Frame processing error` | One frame failed to process and was skipped. Occasional occurrences are harmless. If it repeats on every frame, restart the engine. |
| `OSC send error` | A send failed, for example because the destination is unreachable. The packet is counted as dropped. Check the OSC host and port. |

## Configuration

| Message | Meaning |
|---|---|
| `Failed to load config file` | `config.json` could not be parsed, typically because of a JSON syntax error. Defaults are used for this run. Correct the file (**File → Reveal config.json**), or apply a change in any settings pane to overwrite it. |
| `Invalid integer value for GESTURE_...` | An environment variable override is not a number and was ignored. |

Values entered in a settings pane are checked as you type: an invalid field shows its reason and **Apply** stays disabled until it is fixed.

## Updates

If a download or installation fails, Gesture reports the error and the installed version is unchanged. See **Updates**.

If Gesture cannot update itself (for example, it is running from `~/Downloads` or from a location the user cannot write to), move it to **Applications** and use **Help → Check for Updates…**, or download the release manually from **Help → Project on GitHub**.

## Incomplete data

Status and bounds channels arrive, but landmark channels (`/pose/raw`, `/left_hand/raw`, `/right_hand/raw` and the world variants) arrive intermittently or not at all. No log message indicates this; compare the receiver's log with the `Sent` count in the stats line (**Advanced → Show FPS**). Check the following in order:

- **Send queue drops.** With **Show FPS** enabled, `Dropped` should remain constant under normal load. If it rises, increase **Send queue size** in the **OSC Output** pane (`osc.queue_size`) (minimum 32).
- **IP fragmentation.** In `legacy`, a full pose's landmark JSON (about 2.5 KB) exceeds one network packet and is fragmented; the loss of any fragment discards the message, and some receivers do not reassemble fragments. In `json`, `/pose/raw` (about 1.76 KB) is still fragmented; it is the only `json` address that is. Use `float` (**OSC Output → Output format**), in which every bundle fits in one packet. The receiver must accept OSC bundles. See **OSC Output**.
- **Payload type.** `legacy` and `json` send a JSON string argument. Receivers that expect numeric arguments, such as Isadora, TouchDesigner's OSC In CHOP and Resolume, cannot use it. Use `float`; see **TouchDesigner, Max, Unity, Isadora**.

When a JSON format is required, setting **Tracking mode** to `pose` or `hand` instead of `all` approximately halves the messages per frame. This reduces, but does not eliminate, both problems.

## Orphaned engine

The engine is a separate process connected to the app by a private socket. If the app quits or crashes, the socket closes and the engine exits within about one second, releasing the camera. If an engine process survives anyway (for example after a force-quit of a build from source), end it in Activity Monitor; the camera indicator turns off with it.

## Other issues

Note the exact log message and consult the **OSC Address Reference** and **Appendix**, or the project page (**Help → Project on GitHub**).
