# OSC Output

## Output formats

Select the format under **Settings → Advanced → OSC → Output format** (`osc.protocol`), or with `--osc-protocol legacy|json|float`. Changes apply at the next **Start**.

| Format | Landmark encoding | Transport | Intended use |
|---|---|---|---|
| `legacy` (default) | One JSON string per channel; each landmark has `type` and `id` fields. Identical to 0.2.x. | One datagram per message; no bundles | Existing patches built for 0.2.x |
| `json` | One JSON string per channel, with a top-level `person` index and no per-landmark `type` or `id` | OSC bundles | Receivers that parse JSON |
| `float` | One message per landmark with float arguments, for example `/pose/lm/0 x y z visibility` | OSC bundles | Receivers that bind addresses to numeric values (Isadora, TouchDesigner OSC In CHOP, Resolume); lossy networks |

`legacy` is the default throughout 0.3.x. The default changes in 0.4.0; new patches should use `json` or `float`. The **OSC Address Reference** lists every address in each format.

Pose and hand data use the same address families in all formats: `/pose/...`, `/left_hand/...` and `/right_hand/...`. Status, tracking and heartbeat channels carry numeric arguments. In `json` and `float` they are under `/gesture/...`. In `legacy`, status uses the 0.2.x addresses (`/mp/status` and `/hand/status`, as JSON), and the tracking and heartbeat channels are added alongside.

## OSC bundles

In `json` and `float`, the messages for each frame are sent as OSC bundles of at most 1400 bytes, which fits in a standard 1500-byte Ethernet frame without IP fragmentation. Frames that exceed 1400 bytes are split across several bundles, in order. Bundles use the "immediately" timetag.

The receiver must support OSC bundles. TouchDesigner, Max `[udpreceive]`, Isadora, python-osc and the common Unity OSC packages do. For receivers that accept only bare messages, use `legacy`.

## Payload size and fragmentation

In `legacy`, `/pose/raw` is about 2.5 KB and `/pose/world` about 2.7 KB. Both exceed one Ethernet frame and are IP-fragmented. On a lossy network, such as Wi-Fi or a busy venue switch, loss of any fragment discards the whole message, and some receivers do not reassemble fragments. The result is intermittent or incomplete landmark data (see **Troubleshooting**).

In `json`, `/pose/world` (about 1.2 KB) and all hand channels fit within one bundle. `/pose/raw` (about 1.76 KB) does not; it is sent in its own bundle and is still fragmented. This has no practical effect on `127.0.0.1` or a lightly loaded wired network.

In `float`, each landmark message is 36–56 bytes and no bundle exceeds 1400 bytes. Use `float` on lossy networks and with Isadora.

## Tracking mode

**Tracking mode** determines which channels are sent:

| Mode | Channels |
|---|---|
| `pose` | Pose landmarks, pose status, pose tracking |
| `hand` | Hand landmarks, hand status, hand tracking |
| `all` | All of the above |

The heartbeat is sent in every mode.

By default, `all` uses MediaPipe's combined Holistic landmarker, which is faster but tracks one person only. In `legacy`, it also labels hands `hand_left` / `hand_right` instead of `hand_0` / `hand_1`. **No holistic** (**Settings → Advanced**) uses separate pose and hand models instead; this is required for `mediapipe.num_poses` above 1.

## Status, tracking and heartbeat

- **Status** (`/gesture/pose/status`, `/gesture/hand/status`; `legacy`: `/mp/status`, `/hand/status` as `{"status": N}`) is the number of poses or hands in the current frame's result. Detection runs asynchronously and does not produce a result on every frame, so status is `0` on intermediate frames even while tracking is continuous.
- **Tracking** (`/gesture/pose/tracking`, `/gesture/hand/tracking`; `legacy`: `/mp/tracking`, `/hand/tracking`) is the highest status value over the last 0.3 seconds. It rises immediately on detection and falls only after detection stops. Use tracking, not status, for presence detection. The hold time is set by the optional `osc.tracking_hold` key, in seconds.
- **Heartbeat** (`/gesture/heartbeat`; `legacy`: `/mp/heartbeat`) is sent once per second with four arguments: processing rate in FPS (float), send queue depth, total dropped packets and total sent packets (integers). It is sent regardless of whether anyone is in frame or the source is reconnecting. A missing heartbeat indicates that the engine has stopped.

## Clear messages

When a tracked pose or hand is lost, one clear message is sent on each affected channel:

- `legacy` and `json`: an empty `landmarks` list on raw and world channels, and `{}` on bounds channels.
- `float`: six `0.0` values on each bounds address (`/pose/bounds`, `/pose/world_bounds` and the hand equivalents). Per-landmark addresses retain their last values; use tracking to determine whether they are current.

The clear is sent once, on the frame where tracking is lost. Use tracking to detect a continuing absence.

On **Stop** or quit, the engine sends clears for every channel used in the session, and status and tracking values of `0`, before exiting.

## Send queue

Packets are queued and sent on a background thread so that network delays do not stall tracking. When the queue is full, the oldest packet is dropped. The drop count appears in the stats line when **Show FPS** is enabled, and in the heartbeat.

OSC uses UDP, so sending to a host or port with no listener does not produce an error or increase the drop count. A rising drop count means packets are produced faster than the send thread can transmit them.

The host can be a broadcast address, such as `192.168.1.255`, to send to all machines on the subnet.
