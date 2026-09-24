# OSC Output

## Three output formats

Gesture can send its data in three formats. Choose one under **Settings → Advanced → OSC → Output format** (config key `osc.protocol`), or with `--osc-protocol legacy|json|float` on the command line. It applies the next time you click Start.

| Format | Landmarks arrive as | Packets | Use it when |
|---|---|---|---|
| `legacy` (default) | One JSON string per channel, with a `type` and `id` on every landmark, exactly as 0.2.x sent it | One UDP datagram per message, never bundled | You have an existing patch and don't want to touch it |
| `json` | One leaner JSON string per channel: a top-level `person` index, no per-landmark `type`/`id` | Per-frame OSC bundles | Your receiver parses JSON and you want smaller payloads |
| `float` | One message per landmark with plain float arguments, e.g. `/pose/lm/0 x y z visibility` | Per-frame OSC bundles | Isadora, TouchDesigner's OSC In CHOP, Resolume, or any receiver that binds an address to numbers; lossy or busy networks |

**`legacy` stays the default for all of 0.3.x**, so an existing patch keeps working with no changes. 0.4.0 will switch the default to the new format, so if you're starting a new patch, build it on `json` or `float`. The **OSC Address Reference** lists every address in all three formats side by side.

Whatever the format, pose and hand data stay on the same address families (`/pose/...`, `/left_hand/...`, `/right_hand/...`). The status, tracking and heartbeat channels always carry plain numbers. In `json` and `float` they live under `/gesture/...`. In `legacy`, status stays on the 0.2.x addresses (`/mp/status`, `/hand/status`, as JSON), and the new channels sit alongside them.

## OSC bundles (json and float)

In `json` and `float` mode, each processed frame's messages are packed into **OSC bundles** of at most 1400 bytes. 1400 bytes fits inside a standard 1500-byte network packet with room to spare, so a bundle never has to be split into IP fragments along the way. A frame that doesn't fit in one bundle is split across several, in order. Every bundle uses the "immediately" timetag.

Your receiver has to accept OSC bundles. TouchDesigner, Max's `[udpreceive]`, Isadora, python-osc and the common Unity OSC packages unpack bundles on their own, so each message inside arrives exactly as if it had been sent alone. If a receiver only understands bare messages, stay on `legacy`, which never bundles.

## Large JSON payloads and fragmentation

A full 33-landmark pose is a lot of JSON. In `legacy` mode, `/pose/raw` is about 2.5 KB and `/pose/world` about 2.7 KB. Both are bigger than one network packet, so the operating system splits each into IP fragments. On a busy or lossy network (Wi-Fi, a venue switch), losing any single fragment loses the whole message, and some receivers never reassemble fragments at all. That's the "data arrives but is incomplete" symptom in **Troubleshooting**.

`json` mode shrinks this: `/pose/world` drops to about 1.2 KB and fits in a bundle, and the hand channels all fit. **`/pose/raw` is still about 1.76 KB in `json` mode**, though, which is over the 1400-byte budget. It goes out alone in its own bundle and still gets fragmented. On a single machine (`127.0.0.1`) or a quiet wired network that's harmless.

**`float` mode is the fix for lossy networks and Isadora**: each landmark is a 36–44 byte message, and every bundle stays within 1400 bytes, so nothing is ever fragmented.

## Tracking modes decide what's sent

The **Tracking mode** dropdown controls which channels exist at all:

| Mode | Sends |
|---|---|
| `pose` | Body pose landmarks, pose status and pose tracking |
| `hand` | Left/right hand landmarks, hand status and hand tracking |
| `all` | Both, pose and hands together |

The heartbeat is sent in every mode.

`all` mode normally uses a single combined model pass (MediaPipe's "Holistic" landmarker) rather than running pose and hand detection separately. This is faster, but it only ever reports one person, and in `legacy` mode it changes how hand landmarks are labeled (`hand_left`/`hand_right` rather than `hand_0`/`hand_1`). **No Holistic**, in Settings → Advanced, is a launch-time toggle that switches `all` mode back to two separate models if you need to run `mediapipe.num_poses` above 1. See the **OSC Address Reference** for the details.

## Status, tracking and heartbeat

Three kinds of health channel tell your patch what's going on:

- **Status** (`/gesture/pose/status`, `/gesture/hand/status`; in `legacy`, `/mp/status` and `/hand/status` as `{"status": N}`) is the raw count of poses or hands **in this frame's result**. MediaPipe's detector runs asynchronously and doesn't finish a fresh result every frame. On the frames in between, status is `0` even while someone is being tracked continuously. If your patch treats a single `0` as "gone", it will flicker.
- **Tracking** (`/gesture/pose/tracking`, `/gesture/hand/tracking`; in `legacy`, `/mp/tracking` and `/hand/tracking`) is the debounced count. It's the highest status count from the last 0.3 seconds, so it rises the moment someone is detected and only falls once detection has really stopped reporting them. **Drive "person present" logic from tracking, not status.** The hold time is the optional `osc.tracking_hold` config key, in seconds.
- **Heartbeat** (`/gesture/heartbeat`; in `legacy`, `/mp/heartbeat`) arrives once a second with four numbers: the processing loop's frames per second (float), then the OSC send queue's current depth, total dropped packets and total sent packets (ints). It keeps coming even when nobody is in frame or the camera has hiccupped, so a missing heartbeat means the engine has stopped, not that the room is empty.

## Losing tracking clears the last position, once

When a pose or hand that was being tracked disappears, Gesture sends one "cleared" signal on the affected channels so your receiver doesn't freeze on the last known position:

- `legacy` and `json`: an empty `landmarks` list on the raw and world channels, and an empty `{}` on the bounds channels.
- `float`: six `0.0`s on each bounds address (`/pose/bounds`, `/pose/world_bounds`, and the hand equivalents). The per-landmark addresses keep their last values; use tracking (or all-zero bounds) to know whether they're live.

This fires exactly once, on the frame tracking is lost, not repeatedly while nobody is in frame. For "no one is here" as an ongoing state, use the tracking channel.

Clicking **Stop** (or quitting) sends the same clears for every channel the session used, plus status `0` and tracking `0`, before the engine exits, so receivers don't keep showing the last pose after the engine has stopped.

## OSC send queue and dropped messages

Outgoing packets are queued and sent by a background thread so that a slow network target can't stall tracking. If packets are produced faster than the sender thread can push them out, the **oldest** queued packet is dropped to make room for the newest one. Under sustained congestion, a stale pose isn't worth keeping over a fresh one. A running count of drops appears in the FPS/stats line (see **Models & Performance**) when **Show FPS** is enabled, and in every heartbeat.

OSC is UDP, which is fire-and-forget: sending to a host/port nobody is listening on doesn't fail, block, or come back as an error. It just silently goes nowhere. An absent or wrong OSC target does **not** produce Dropped counts by itself. A climbing "Dropped" count instead means Gesture's own outgoing queue is filling up faster than it can be drained, whether or not anything is listening on the other end.

The host can be a broadcast address (for example `192.168.1.255`) to reach every machine on the subnet at once.
