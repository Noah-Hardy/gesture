# OSC Output

## Transport

Every message MP-OSC sends is a single OSC argument: one string, containing compact JSON. There are no plain float or int OSC arguments anywhere in the protocol — a receiver needs to parse the string argument as JSON to get at the numbers inside.

```
/pose/raw   "{\"timestamp\":1720000000.123,\"landmarks\":[...]}"
```

The complete address list and exact payload shape for every message are in the **OSC Address Reference**. This page covers the concepts that apply across all of them.

## Tracking modes decide what's sent

The **Tracking mode** dropdown controls which channels exist at all:

| Mode | Sends |
|---|---|
| `pose` | Body pose landmarks and pose status only |
| `hand` | Left/right hand landmarks and hand status only |
| `all` | Both — pose and hand landmarks and status together |

`all` mode normally uses a single combined model pass (MediaPipe's "Holistic" landmarker) rather than running pose and hand detection separately. This is faster, but changes two things about the wire format compared to running pose and hand separately: hand landmarks are labeled by handedness (`hand_left`/`hand_right`) instead of by detection order (`hand_0`/`hand_1`), and only one person's hands are ever reported. **No Holistic**, in Settings → Advanced, is a launch-time toggle that switches `all` mode back to two separate models if you need positional (rather than handedness-based) hand labeling, or need to run `mediapipe.num_poses` above 1. See the **OSC Address Reference** for the exact field-naming difference this causes.

## Status channels are a heartbeat, not a presence flag

`/mp/status` and `/hand/status` send `{"status": N}` on every processed frame, where `N` is the number of poses or hands detected **in that specific frame's result**. It's tempting to treat `status: 0` as "nobody is here," but MediaPipe's detector runs asynchronously and doesn't produce a fresh result every single frame — on the frames where a fresh result hasn't arrived yet, MP-OSC still sends `status: 0`, even while continuously tracking someone. If your receiving patch treats a single `0` as "gone," it will flicker. Debounce a few consecutive zero readings before treating someone as no longer present.

## Losing tracking clears the last position — once

When a pose or hand that was previously detected disappears, MP-OSC sends one empty-landmarks message on the affected channels (empty list, and empty bounds) so your receiver can clear stale data instead of freezing on the last known position. This fires exactly once on the transition frame, not repeatedly while nobody is in frame — so if you need "no one is here" as an ongoing state, drive it from the status channel (with debouncing, see above) rather than expecting repeated empty messages.

## OSC send queue and dropped messages

Outgoing messages are queued and sent by a background thread so that a slow network target can't stall tracking. If messages are produced faster than the sender thread can push them out, the **newest** message is dropped once the queue is full, and a running count of drops appears in the FPS/stats line (see **Models & Performance**) when **Show FPS** is enabled.

OSC is UDP, which is fire-and-forget: sending to a host/port nobody is listening on doesn't fail, block, or come back as an error — it just silently goes nowhere. An absent or wrong OSC target does **not** produce Dropped counts by itself. A climbing "Dropped" count instead means MP-OSC's own outgoing queue is filling up faster than it can be drained — i.e. landmark data is being produced faster than it can be sent, regardless of whether anything is actually listening on the other end.
