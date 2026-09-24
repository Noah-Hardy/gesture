# Troubleshooting

The tracking engine's console output streams directly into the launcher's log pane, so the messages below are exactly what you'll see there. Find the message, read what it means.

## Camera and NDI

| Message | Meaning |
|---|---|
| `❌ Video capture is not open - nothing to process` | The chosen camera device ID doesn't exist, or NDI never connected. Check the device ID, or on macOS confirm camera access under **System Settings → Privacy & Security → Camera**. |
| `⚠️ Camera may be slow to start - continuing anyway` | The camera didn't produce a frame within about 3 seconds of opening. Often harmless (some cameras are just slow to wake up); if tracking never starts, the camera may be in use by another app. |
| `⚠️ Capture stopped delivering frames - retrying` | The camera or NDI source stopped delivering frames mid-session, often a cable, USB or network dropout. Gesture keeps retrying and reopens the source every few attempts; `✅ Capture recovered` follows once it's back. See **Camera & NDI**. |
| `❌ Capture lost for ...s (... failed reads) - giving up` | The source didn't come back within **Reconnect timeout** (Settings → Advanced, default 30 s). Tracking stops; fix the source and click Start again, or set the timeout to 0 to retry forever. |
| `❌ NDI requested but ndi-python not installed` | This build doesn't have NDI support available. NDI is optional; camera input still works. |
| `❌ NDI source unavailable - not falling back to webcam` | NDI didn't connect. The line above it says why: `no NDI sources found`, `no source named '...'`, or `'...' matches more than one source` (use the full name). Gesture never switches to a different source or the webcam on its own. See **Camera & NDI**. |
| `❌ NDI setup failed` | The NDI library itself failed to start (see the rest of the line for the error). Check that the NDI runtime is installed. |
| `🛑 Launcher is gone - stopping` | The launcher that started the engine quit or crashed, so the engine stopped itself rather than keep running (and holding the camera) in the background. |
| `⚠️ Resolution differs from config` | Your camera's actual resolution doesn't match what's requested. Frames are resized to the configured processing resolution regardless — see **Processing resolution** in **Camera & NDI** for why that can distort the image. |

## Startup and model loading

| Message | Meaning |
|---|---|
| `📥 Downloading pose model...` / `📥 Downloading hand model...` / `📥 Downloading holistic model...` | First-time setup: Gesture fetches the MediaPipe model files it needs. Requires an internet connection once; models are cached afterward. The packaged macOS app ships all models already downloaded, so this should only appear when running from source. |
| `❌ Failed to download model` | The one-time model download failed — check your internet connection and try again. |
| `❌ Model file not available` / `❌ Hand model file not available` / `❌ Holistic model file not available` | The model file Gesture needs isn't present and couldn't be fetched. Tracking can't start without it. |
| `🛑 Cannot initialize pose processing backend` / `hand processing backend` / `any processing backend` | Every available detection backend failed to initialize (both the modern and legacy MediaPipe APIs). Tracking cannot start; check the lines above this one in the log for the underlying reason. |

## Delegate (CPU/GPU) selection

| Message | Meaning |
|---|---|
| `🍎 Apple Silicon detected: Using CPU delegate` | Expected and correct — GPU acceleration is intentionally disabled on Apple Silicon due to a known MediaPipe memory leak. Not an error. |
| `⚠️ GPU delegate failed during initialization` | GPU setup failed and Gesture is falling back to CPU automatically. Tracking continues, typically just slower. |
| `❌ CPU delegate also failed` | Both GPU and CPU setup failed for this component. That component (pose or hand) won't be available this run. |

## During tracking (recoverable — tracking continues)

| Message | Meaning |
|---|---|
| `⚠️ Tasks frame processing error` / `⚠️ Legacy frame processing error` / `⚠️ Hand frame processing error` / `⚠️ Holistic frame processing error` | A single frame failed to process. Gesture logs it and continues with the next frame. Occasional occurrences are usually harmless; if this repeats every frame, tracking has effectively stalled and needs a restart. |
| `OSC send error` | A network send failed (destination unreachable, etc). The message is counted as dropped and tracking continues — check that your OSC host/port are correct and reachable. |

## Config

| Message | Meaning |
|---|---|
| `⚠️ Failed to load config file` | `config.json` exists but couldn't be parsed (often a JSON syntax error from manual editing) — Gesture falls back to defaults for this run. Fix the file or use **💾 Save Config** to overwrite it with a valid one. |
| `❌ Invalid OSC port` / `❌ Invalid camera device ID` | **💾 Save Config** refused to save because one of those fields isn't a valid number. Fix the field and save again. |

## Updating

If a download or install fails partway — a lost connection, a checksum mismatch, a signature that doesn't verify — Gesture shows the error, and the version you already have keeps running untouched; nothing is lost, and there's nothing to clean up by hand. See the **Updates** guide for the exact steps an install goes through.

If you're offline, or Gesture can't self-update on this machine (it's still in `~/Downloads`, or installed somewhere your account can't write to), use **Help → Check for Updates…** once you're back online and in a writable location, or download the new version manually from the project's GitHub Releases page (**Help → Project on GitHub**).

## Data arrives but is incomplete

Some OSC channels come through fine — usually `/mp/status`, `/hand/status`, and the small `*_bounds` channels — while the larger landmark channels (`/pose/raw`, `/left_hand/raw`, `/right_hand/raw`, and the `*/world` variants) arrive rarely or not at all. This has no single console message; it shows up as a gap between what your receiver logs and what Gesture's own `Sent` count (Show FPS) says it transmitted. Three causes, in the order to check them:

- **Outgoing send queue too small.** `all` mode sends 14 messages per frame; older configs could have a queue depth (`osc.queue_size`) too small to hold even one frame's worth, so any brief stall in the sender thread dropped landmark channels every frame. Fixed as of 0.1.8 — the default and the enforced floor are both 32, and older saved configs are migrated automatically the first time you launch a 0.1.8 build. Confirm with **Show FPS**: `Dropped` should stay flat under normal load; if it's still climbing, check your `osc.queue_size` in **Settings → Advanced**.
- **MTU fragmentation.** In the default `legacy` format, a full pose's landmark JSON (about 2.5 KB) is bigger than one network packet, so it gets split into IP fragments, and losing any one fragment loses the whole message. Not every receiver reassembles fragments either. The `json` format shrinks most channels to fit, but `/pose/raw` is still about 1.76 KB. **Switch to the `float` format** (Settings → Advanced → OSC → Output format), where every bundle fits in one packet. Your receiver needs to accept OSC bundles, which all the common ones do. See **OSC Output**.
- **JSON-vs-numeric payload format.** `legacy` and `json` send one JSON string argument per message. A receiver that expects separate float/int OSC arguments (Isadora, TouchDesigner's OSC In CHOP and Resolume in particular) can silently fail to parse it as anything at all. Use the `float` format for these; see **TouchDesigner, Max, Unity, Isadora**.

If you need to stay on a JSON format, switching **Tracking mode** to `pose` or `hand` instead of `all` cuts the per-frame message count roughly in half. That reduces exposure to both problems without fixing them.

## If none of this matches what you're seeing

Copy the exact message from the log pane and check the **OSC Address Reference** and **Appendix** for anything more specific to the feature involved, or consult the project's GitHub page from the **Help** menu.
