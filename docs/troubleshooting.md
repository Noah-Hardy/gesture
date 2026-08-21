# Troubleshooting

The tracking engine's console output streams directly into the launcher's log pane, so the messages below are exactly what you'll see there. Find the message, read what it means.

## Camera and NDI

| Message | Meaning |
|---|---|
| `❌ Video capture is not open - nothing to process` | The chosen camera device ID doesn't exist, or NDI never connected and there was no camera to fall back to. Check the device ID, or on macOS confirm camera access under **System Settings → Privacy & Security → Camera**. |
| `⚠️ Camera may be slow to start - continuing anyway` | The camera didn't produce a frame within about 3 seconds of opening. Often harmless (some cameras are just slow to wake up); if tracking never starts, the camera may be in use by another app. |
| `❌ Too many consecutive frame failures` | The camera or NDI source stopped delivering frames mid-session — often a cable, USB, or network dropout. Tracking stops; restart it once the source is back. |
| `❌ NDI requested but ndi-python not installed` | This build doesn't have NDI support available. NDI is optional; camera input still works. |
| `❌ NDI capture failed to open, falling back to camera...` | NDI setup failed for some reason (see the next line in the log for the specific error) and MP-OSC switched to the webcam instead. |
| `⚠️ Source '...' not found, using first available` | The saved NDI source name didn't match anything currently on the network (matching is a substring search — see **Camera & NDI**), so MP-OSC connected to whatever NDI source it found first instead. |
| `⚠️ Resolution differs from config` | Your camera's actual resolution doesn't match what's requested. Frames are resized to the configured processing resolution regardless — see **Processing resolution** in **Camera & NDI** for why that can distort the image. |

## Startup and model loading

| Message | Meaning |
|---|---|
| `📥 Downloading pose model...` / `📥 Downloading hand model...` / `📥 Downloading holistic model...` | First-time setup: MP-OSC fetches the MediaPipe model files it needs. Requires an internet connection once; models are cached afterward. The packaged macOS app ships all models already downloaded, so this should only appear when running from source. |
| `❌ Failed to download model` | The one-time model download failed — check your internet connection and try again. |
| `❌ Model file not available` / `❌ Hand model file not available` / `❌ Holistic model file not available` | The model file MP-OSC needs isn't present and couldn't be fetched. Tracking can't start without it. |
| `🛑 Cannot initialize pose processing backend` / `hand processing backend` / `any processing backend` | Every available detection backend failed to initialize (both the modern and legacy MediaPipe APIs). Tracking cannot start; check the lines above this one in the log for the underlying reason. |

## Delegate (CPU/GPU) selection

| Message | Meaning |
|---|---|
| `🍎 Apple Silicon detected: Using CPU delegate` | Expected and correct — GPU acceleration is intentionally disabled on Apple Silicon due to a known MediaPipe memory leak. Not an error. |
| `⚠️ GPU delegate failed during initialization` | GPU setup failed and MP-OSC is falling back to CPU automatically. Tracking continues, typically just slower. |
| `❌ CPU delegate also failed` | Both GPU and CPU setup failed for this component. That component (pose or hand) won't be available this run. |

## During tracking (recoverable — tracking continues)

| Message | Meaning |
|---|---|
| `⚠️ Tasks frame processing error` / `⚠️ Legacy frame processing error` / `⚠️ Hand frame processing error` / `⚠️ Holistic frame processing error` | A single frame failed to process. MP-OSC logs it and continues with the next frame. Occasional occurrences are usually harmless; if this repeats every frame, tracking has effectively stalled and needs a restart. |
| `OSC send error` | A network send failed (destination unreachable, etc). The message is counted as dropped and tracking continues — check that your OSC host/port are correct and reachable. |

## Config

| Message | Meaning |
|---|---|
| `⚠️ Failed to load config file` | `config.json` exists but couldn't be parsed (often a JSON syntax error from manual editing) — MP-OSC falls back to defaults for this run. Fix the file or use **💾 Save Config** to overwrite it with a valid one. |
| `❌ Invalid OSC port` / `❌ Invalid camera device ID` | **💾 Save Config** refused to save because one of those fields isn't a valid number. Fix the field and save again. |

## Updating

If a download or install fails partway — a lost connection, a checksum mismatch, a signature that doesn't verify — MP-OSC shows the error, and the version you already have keeps running untouched; nothing is lost, and there's nothing to clean up by hand. See the **Updates** guide for the exact steps an install goes through.

If you're offline, or MP-OSC can't self-update on this machine (it's still in `~/Downloads`, or installed somewhere your account can't write to), use **Help → Check for Updates…** once you're back online and in a writable location, or download the new version manually from the project's GitHub Releases page (**Help → Project on GitHub**).

## If none of this matches what you're seeing

Copy the exact message from the log pane and check the **OSC Address Reference** and **Appendix** for anything more specific to the feature involved, or consult the project's GitHub page from the **Help** menu.
