# OSC Address Reference

This page lists every address Gesture sends in each of its three output formats (`legacy`, `json` and `float`), with the exact payload for each. **OSC Output** explains how to choose a format, how bundles work, and what status, tracking and the heartbeat mean.

## Address map

| Purpose | legacy (default) | json | float |
|---|---|---|---|
| Pose landmarks | `/pose/raw` JSON, `type`/`id` per landmark | `/pose/raw` JSON with `person`, no `type`/`id` | `/pose/lm/<n>` `ffff` (x y z visibility); other people: `/pose/<p>/lm/<n>` |
| Pose world landmarks | `/pose/world` JSON | `/pose/world` JSON, no visibility | `/pose/world/lm/<n>` `fff` |
| Pose bounds | `/pose/raw_bounds`, `/pose/world_bounds` JSON | same addresses, with `person` | `/pose/bounds` `ffffff`, `/pose/world_bounds` `ffffff` |
| Hand landmarks | `/left_hand/raw`, `/right_hand/raw` JSON | same addresses, new shape | `/left_hand/lm/<n>` `fff`, `/right_hand/lm/<n>` `fff` |
| Hand world landmarks | `/left_hand/world`, `/right_hand/world` JSON | same addresses, no visibility | `/left_hand/world/lm/<n>` `fff` (and right) |
| Hand bounds | `/left_hand/bounds`, `/left_hand/world_bounds` (and right) JSON | same addresses | same addresses, `ffffff` |
| Pose / hand status (raw, per frame) | `/mp/status`, `/hand/status` JSON `{"status": N}` | `/gesture/pose/status` `i`, `/gesture/hand/status` `i` | same as json |
| Pose / hand tracking (debounced) | `/mp/tracking` `i`, `/hand/tracking` `i` | `/gesture/pose/tracking` `i`, `/gesture/hand/tracking` `i` | same as json |
| Heartbeat (1 Hz) | `/mp/heartbeat` `fiii` | `/gesture/heartbeat` `fiii` | same as json |
| Packets | one datagram per message | per-frame bundles of 1400 bytes or less | per-frame bundles of 1400 bytes or less |

`f` is a 32-bit float argument, `i` a 32-bit int, `<n>` the landmark number (see **Landmark indices**, below) and `<p>` the person index. `legacy` is the default until 0.4.0.

## Channels shared by every format

These carry plain numeric arguments in every format. Only the addresses differ: `legacy` keeps the `/mp/` and `/hand/` prefixes it has always used, while `json` and `float` use `/gesture/`.

| legacy | json and float | Arguments |
|---|---|---|
| `/mp/tracking` | `/gesture/pose/tracking` | `i`: debounced pose count, the highest status in the last 0.3 s |
| `/hand/tracking` | `/gesture/hand/tracking` | `i`: debounced hand count |
| `/mp/heartbeat` | `/gesture/heartbeat` | `f i i i`: loop FPS, send-queue depth, dropped packets, sent packets |
| `/mp/status` (JSON string) | `/gesture/pose/status` | `i`: poses in this frame's result |
| `/hand/status` (JSON string) | `/gesture/hand/status` | `i`: hands in this frame's result |

Status and tracking are sent on every processed frame. The heartbeat is sent once a second, even while nobody is in frame. The dropped and sent counters count packets (a bundle counts as one) and wrap at 2^31.

## legacy format

The frozen 0.2.x wire format. Every data message is a single OSC string argument containing compact JSON, sent as its own UDP datagram.

**A landmark list** (used in `/pose/raw`, `/pose/world`, and the hand raw/world channels) is an array of objects, one per landmark:

```json
{"type":"pose_0","id":0,"x":0.512,"y":0.231,"z":-0.413,"visibility":0.998}
```

`x`/`y`/`z` and `visibility` are rounded to 3 decimal places. Pose landmarks, normalized and world, carry a real visibility score. Hand landmarks always report `0.0`, because MediaPipe doesn't score hand visibility. `id` is the landmark number.

**A bounds object** (used in every `*_bounds` channel) reports the six extreme landmarks of a detection, not a numeric box:

```json
{"max_x":{"id":2,"x":0.53,"y":0.21,"z":-0.392,"visibility":0.996},"min_x":{"id":0,"x":0.512,"y":0.231,"z":-0.413,"visibility":0.998},"max_y":{...},"min_y":{...},"max_z":{...},"min_z":{...}}
```

Each of the six values is the full landmark at that extreme, including its `id`, with no `type` field.

**Pose channels** (in `pose` and `all` mode):

| Address | Payload |
|---|---|
| `/pose/raw` | `{"timestamp": <epoch seconds>, "landmarks": [...33 normalized landmarks]}` |
| `/pose/world` | Same shape, world coordinates in metres, centred on the hips |
| `/pose/raw_bounds` | Bounds object over the normalized landmarks |
| `/pose/world_bounds` | Bounds object over the world landmarks |
| `/mp/status` | `{"status": <N>}` |

If more than one pose is detected in a frame (only possible with the separate, non-holistic pose model), each pose is sent as its own complete set of messages, one after another, on the same addresses. They're told apart only by the `type` inside each landmark: `pose_0`, `pose_1`, and so on.

**Hand channels** (in `hand` and `all` mode):

| Address | Payload |
|---|---|
| `/left_hand/raw`, `/right_hand/raw` | `{"timestamp": <epoch>, "handedness": "Left", "landmarks": [...21 landmarks]}` (`handedness` is `Left`, `Right` or `Unknown`) |
| `/left_hand/world`, `/right_hand/world` | Same shape, world coordinates |
| `/left_hand/bounds`, `/right_hand/bounds` | Bounds object over normalized landmarks |
| `/left_hand/world_bounds`, `/right_hand/world_bounds` | Bounds object over world landmarks |
| `/hand/status` | `{"status": <N>}` |

A hand whose handedness can't be determined confidently goes to `/right_hand/*` along with genuine right hands: anything that isn't literally `"Left"` goes to the right-hand channels. This rule is the same in every format.

**The `type` field has two conventions**, depending on which detection path produced it:

| Situation | `type` values |
|---|---|
| `all` mode, default (combined holistic model) | Pose: always `pose_0` / `pose_world_0` (single person). Hands: `hand_left` / `hand_right` and `hand_world_left` / `hand_world_right`, labeled by handedness. |
| `all` mode with **No Holistic**, or `pose`/`hand` mode | Pose: `pose_0`, `pose_1`, ... (by detection order). Hands: `hand_0` / `hand_1` and `hand_world_0` / `hand_world_1`, labeled by **detection order, not handedness** (use the separate `"handedness"` field for that). |

**Clears:** when a tracked pose disappears, `/pose/raw` and `/pose/world` get `{"timestamp": ..., "landmarks": []}`, both bounds channels get `{}`, and `/mp/status` gets `{"status":0}`. A lost hand gets the same on its four channels, with its `handedness` kept.

## json format

JSON v2: the same addresses as `legacy`, with smaller payloads, sent in per-frame OSC bundles.

```
/pose/raw   {"timestamp":1727200000.123,"person":0,"landmarks":[{"x":0.512,"y":0.231,"z":-0.413,"visibility":0.998}, ...]}
/pose/world {"timestamp":1727200000.123,"person":0,"landmarks":[{"x":-0.012,"y":-0.601,"z":-0.287}, ...]}
```

What changed from `legacy`:

- **No per-landmark `type` or `id`.** A landmark's position in the `landmarks` array is its number: `landmarks[0]` is the nose, `landmarks[15]` the left wrist.
- **A top-level `"person"`** on `/pose/raw`, `/pose/world`, `/pose/raw_bounds` and `/pose/world_bounds` replaces the `pose_0`/`pose_1` type strings. It's `0` for the first (or only) person.
- **World landmarks and world bounds carry no `visibility`.** It only repeated the normalized landmark's value. Normalized pose landmarks keep it; hand landmarks carry `0.0`.
- **Bounds keep their `id`**, because there it tells you which landmark is the extreme, which you can't work out from anything else.
- **Hand payloads** keep `handedness` and have no `person` field.
- **Clears** are the empty `landmarks` list and `{}` bounds, as in `legacy`. A pose clear has no `person` field: it means every person. Unlike `legacy`, it doesn't include a status message; `/gesture/pose/status` reports `0` separately.
- **Status** is the plain int `/gesture/pose/status` and `/gesture/hand/status`, not JSON.

Typical sizes for a full detection: `/pose/raw` about 1760 bytes, `/pose/world` about 1170, a hand's `raw` about 1160 and its `world` about 790. `/pose/raw` is the one message still over the 1400-byte bundle budget; it goes out alone and gets IP-fragmented (see **OSC Output**).

## float format

One OSC message per landmark, with plain float arguments and no JSON, sent in per-frame bundles of 1400 bytes or less.

**Pose**, for the first (or only) person:

| Address | Arguments |
|---|---|
| `/pose/lm/<n>` (n = 0–32) | `x y z visibility`: normalized, 4 floats |
| `/pose/world/lm/<n>` | `x y z`: metres, centred on the hips, 3 floats |
| `/pose/bounds` | `min_x max_x min_y max_y min_z max_z`: 6 floats over the normalized landmarks |
| `/pose/world_bounds` | `min_x max_x min_y max_y min_z max_z`: 6 floats over the world landmarks |

**More than one person.** Person 0 always uses the addresses above, so a single-person patch keeps working when a second person walks in. People after the first get their index inserted after `/pose`: `/pose/1/lm/<n>`, `/pose/1/world/lm/<n>`, `/pose/1/bounds`, `/pose/1/world_bounds`, then `/pose/2/...`. There is no `/pose/0/...`. Multiple people need the separate, non-holistic pose model with `mediapipe.num_poses` above 1.

**Hands** (prefix `left_hand` or `right_hand`):

| Address | Arguments |
|---|---|
| `/left_hand/lm/<n>` (n = 0–20) | `x y z`: normalized, 3 floats (no visibility, since MediaPipe doesn't score hands) |
| `/left_hand/world/lm/<n>` | `x y z`: metres, 3 floats |
| `/left_hand/bounds` | 6 floats, same order as pose bounds |
| `/left_hand/world_bounds` | 6 floats, same order |

The right hand is identical under `/right_hand/`.

Values are full precision (not rounded to 3 decimal places), transmitted as 32-bit floats. Bounds are plain numbers in the order **min_x, max_x, min_y, max_y, min_z, max_z**. They don't say which landmark is the extreme; use `json` if you need that.

**Clears:** when a pose is lost, `/pose/bounds` and `/pose/world_bounds` (and those of every other person seen since the previous clear, like `/pose/1/bounds`) get six `0.0`s. A lost hand gets six `0.0`s on its `bounds` and `world_bounds`. The per-landmark addresses keep their last values, so gate them on `/gesture/pose/tracking` (or `/gesture/hand/tracking`) being above 0.

## Status semantics

The status channels report the number of poses or hands detected **in the most recent frame the detector actually finished**, not a stable "is anyone here" flag. Because detection runs asynchronously and doesn't produce a fresh result on every video frame, status is `0` on ordinary in-between frames too, even while someone is being continuously tracked. Use the tracking channels for "person present" logic; see **OSC Output**.

A clear is sent once, on the frame a pose or hand disappears, not on every empty frame after it.

## Landmark indices

The landmark number is an index into MediaPipe's standard 33-point pose model or 21-point hand model. In `legacy` it's each landmark's `id` field, in `json` its position in the `landmarks` array, and in `float` the `<n>` at the end of the address (`/pose/lm/15` is the left wrist). These tables are generated directly from MediaPipe's own landmark enums (`scripts/make_landmark_tables.py`), so they can't drift out of sync with the library.

### Pose landmark indices (33)

| Index | Name |
|---|---|
| 0 | `NOSE` |
| 1 | `LEFT_EYE_INNER` |
| 2 | `LEFT_EYE` |
| 3 | `LEFT_EYE_OUTER` |
| 4 | `RIGHT_EYE_INNER` |
| 5 | `RIGHT_EYE` |
| 6 | `RIGHT_EYE_OUTER` |
| 7 | `LEFT_EAR` |
| 8 | `RIGHT_EAR` |
| 9 | `MOUTH_LEFT` |
| 10 | `MOUTH_RIGHT` |
| 11 | `LEFT_SHOULDER` |
| 12 | `RIGHT_SHOULDER` |
| 13 | `LEFT_ELBOW` |
| 14 | `RIGHT_ELBOW` |
| 15 | `LEFT_WRIST` |
| 16 | `RIGHT_WRIST` |
| 17 | `LEFT_PINKY` |
| 18 | `RIGHT_PINKY` |
| 19 | `LEFT_INDEX` |
| 20 | `RIGHT_INDEX` |
| 21 | `LEFT_THUMB` |
| 22 | `RIGHT_THUMB` |
| 23 | `LEFT_HIP` |
| 24 | `RIGHT_HIP` |
| 25 | `LEFT_KNEE` |
| 26 | `RIGHT_KNEE` |
| 27 | `LEFT_ANKLE` |
| 28 | `RIGHT_ANKLE` |
| 29 | `LEFT_HEEL` |
| 30 | `RIGHT_HEEL` |
| 31 | `LEFT_FOOT_INDEX` |
| 32 | `RIGHT_FOOT_INDEX` |

### Hand landmark indices (21, per hand)

| Index | Name |
|---|---|
| 0 | `WRIST` |
| 1 | `THUMB_CMC` |
| 2 | `THUMB_MCP` |
| 3 | `THUMB_IP` |
| 4 | `THUMB_TIP` |
| 5 | `INDEX_FINGER_MCP` |
| 6 | `INDEX_FINGER_PIP` |
| 7 | `INDEX_FINGER_DIP` |
| 8 | `INDEX_FINGER_TIP` |
| 9 | `MIDDLE_FINGER_MCP` |
| 10 | `MIDDLE_FINGER_PIP` |
| 11 | `MIDDLE_FINGER_DIP` |
| 12 | `MIDDLE_FINGER_TIP` |
| 13 | `RING_FINGER_MCP` |
| 14 | `RING_FINGER_PIP` |
| 15 | `RING_FINGER_DIP` |
| 16 | `RING_FINGER_TIP` |
| 17 | `PINKY_MCP` |
| 18 | `PINKY_PIP` |
| 19 | `PINKY_DIP` |
| 20 | `PINKY_TIP` |

This same 21-point layout is used for both the left and right hand independently.
