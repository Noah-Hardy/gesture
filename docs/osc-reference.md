# OSC Address Reference

Every address sent in each output format, with its payload. See **OSC Output** for format selection, bundling, and the meaning of status, tracking and heartbeat.

## Address map

| Purpose | legacy (default) | json | float |
|---|---|---|---|
| Pose landmarks | `/pose/raw` JSON, `type` and `id` per landmark | `/pose/raw` JSON with `person`; no `type` or `id` | `/pose/lm/<n>` `ffff` (x y z visibility); additional people: `/pose/<p>/lm/<n>` |
| Pose world landmarks | `/pose/world` JSON | `/pose/world` JSON, no visibility | `/pose/world/lm/<n>` `fff` |
| Pose bounds | `/pose/raw_bounds`, `/pose/world_bounds` JSON | Same addresses, with `person` | `/pose/bounds` `ffffff`, `/pose/world_bounds` `ffffff` |
| Hand landmarks | `/left_hand/raw`, `/right_hand/raw` JSON | Same addresses, v2 payload | `/left_hand/lm/<n>` `fff`, `/right_hand/lm/<n>` `fff` |
| Hand world landmarks | `/left_hand/world`, `/right_hand/world` JSON | Same addresses, no visibility | `/left_hand/world/lm/<n>` `fff` (and right) |
| Hand bounds | `/left_hand/bounds`, `/left_hand/world_bounds` (and right) JSON | Same addresses | Same addresses, `ffffff` |
| Status (per frame) | `/mp/status`, `/hand/status` JSON `{"status": N}` | `/gesture/pose/status` `i`, `/gesture/hand/status` `i` | As json |
| Tracking (debounced) | `/mp/tracking` `i`, `/hand/tracking` `i` | `/gesture/pose/tracking` `i`, `/gesture/hand/tracking` `i` | As json |
| Heartbeat (1 Hz) | `/mp/heartbeat` `fiii` | `/gesture/heartbeat` `fiii` | As json |
| Transport | One datagram per message | Bundles of at most 1400 bytes | Bundles of at most 1400 bytes |

`f` is a 32-bit float, `i` a 32-bit integer, `<n>` the landmark index (see **Landmark indices**) and `<p>` the person index.

## Common channels

These channels carry numeric arguments in every format. `legacy` uses the `/mp/` and `/hand/` prefixes; `json` and `float` use `/gesture/`.

| legacy | json and float | Arguments |
|---|---|---|
| `/mp/tracking` | `/gesture/pose/tracking` | `i`: debounced pose count (highest status in the last 0.3 s) |
| `/hand/tracking` | `/gesture/hand/tracking` | `i`: debounced hand count |
| `/mp/heartbeat` | `/gesture/heartbeat` | `f i i i`: processing FPS, send queue depth, dropped packets, sent packets |
| `/mp/status` (JSON string) | `/gesture/pose/status` | `i`: poses in the current frame's result |
| `/hand/status` (JSON string) | `/gesture/hand/status` | `i`: hands in the current frame's result |

Status and tracking are sent every processed frame; the heartbeat once per second. The dropped and sent counters count packets (a bundle counts as one) and wrap at 2^31.

## legacy format

The 0.2.x wire format. Each data message is one OSC string argument containing compact JSON, sent as a separate UDP datagram.

**Landmark list** (`/pose/raw`, `/pose/world`, hand raw and world channels): an array with one object per landmark.

```json
{"type":"pose_0","id":0,"x":0.512,"y":0.231,"z":-0.413,"visibility":0.998}
```

`x`, `y`, `z` and `visibility` are rounded to 3 decimal places. Pose landmarks, normalized and world, include a visibility score. Hand landmarks report `0.0`, as MediaPipe does not score hand visibility. `id` is the landmark index.

**Bounds object** (`*_bounds` channels): the six extreme landmarks of a detection.

```json
{"max_x":{"id":2,"x":0.53,"y":0.21,"z":-0.392,"visibility":0.996},"min_x":{"id":0,"x":0.512,"y":0.231,"z":-0.413,"visibility":0.998},"max_y":{...},"min_y":{...},"max_z":{...},"min_z":{...}}
```

Each value is the complete landmark at that extreme, including `id` and excluding `type`.

**Pose channels** (`pose` and `all` modes):

| Address | Payload |
|---|---|
| `/pose/raw` | `{"timestamp": <epoch seconds>, "landmarks": [33 normalized landmarks]}` |
| `/pose/world` | Same structure; world coordinates in metres, origin at the hips |
| `/pose/raw_bounds` | Bounds object over normalized landmarks |
| `/pose/world_bounds` | Bounds object over world landmarks |
| `/mp/status` | `{"status": <N>}` |

When several poses are detected (separate pose model only), each is sent as a complete set of messages on the same addresses, distinguished by the landmark `type`: `pose_0`, `pose_1` and so on.

**Hand channels** (`hand` and `all` modes):

| Address | Payload |
|---|---|
| `/left_hand/raw`, `/right_hand/raw` | `{"timestamp": <epoch>, "handedness": "Left", "landmarks": [21 landmarks]}`; `handedness` is `Left`, `Right` or `Unknown` |
| `/left_hand/world`, `/right_hand/world` | Same structure; world coordinates |
| `/left_hand/bounds`, `/right_hand/bounds` | Bounds object over normalized landmarks |
| `/left_hand/world_bounds`, `/right_hand/world_bounds` | Bounds object over world landmarks |
| `/hand/status` | `{"status": <N>}` |

In all formats, any hand whose handedness is not `"Left"`, including `Unknown`, is sent on the right-hand channels.

**`type` values** depend on the detection path:

| Detection path | `type` values |
|---|---|
| `all` mode with the Holistic model (default) | Pose: `pose_0` / `pose_world_0` (one person). Hands: `hand_left`, `hand_right`, `hand_world_left`, `hand_world_right`, by handedness. |
| `all` mode with **No holistic**, or `pose` / `hand` mode | Pose: `pose_0`, `pose_1`, … by detection order. Hands: `hand_0`, `hand_1`, `hand_world_0`, `hand_world_1`, by detection order; use the `handedness` field for side. |

**Clears:** on pose loss, `/pose/raw` and `/pose/world` receive `{"timestamp": ..., "landmarks": []}`, both bounds channels receive `{}`, and `/mp/status` receives `{"status":0}`. On hand loss, the hand's four channels receive the same, with `handedness` retained.

## json format

The `legacy` addresses with reduced payloads, sent in OSC bundles.

```
/pose/raw   {"timestamp":1727200000.123,"person":0,"landmarks":[{"x":0.512,"y":0.231,"z":-0.413,"visibility":0.998}, ...]}
/pose/world {"timestamp":1727200000.123,"person":0,"landmarks":[{"x":-0.012,"y":-0.601,"z":-0.287}, ...]}
```

Differences from `legacy`:

- **No per-landmark `type` or `id`.** The array position is the landmark index: `landmarks[0]` is the nose, `landmarks[15]` the left wrist.
- **Top-level `person`** on `/pose/raw`, `/pose/world`, `/pose/raw_bounds` and `/pose/world_bounds`, starting at `0`.
- **No `visibility` on world landmarks or world bounds.** Normalized pose landmarks retain it; hand landmarks report `0.0`.
- **Bounds retain `id`**, identifying the extreme landmark.
- **Hand payloads** retain `handedness` and have no `person` field.
- **Clears** are an empty `landmarks` list and `{}` bounds. A pose clear has no `person` field and applies to all people. Status is not included; `/gesture/pose/status` reports `0` separately.
- **Status** is an integer on `/gesture/pose/status` and `/gesture/hand/status`.

Typical sizes: `/pose/raw` about 1760 bytes, `/pose/world` about 1170, hand `raw` about 1160, hand `world` about 790. `/pose/raw` exceeds the 1400-byte bundle limit and is IP-fragmented (see **OSC Output**).

## float format

One message per landmark with float arguments, sent in bundles of at most 1400 bytes.

**Pose**, first person:

| Address | Arguments |
|---|---|
| `/pose/lm/<n>` (n = 0–32) | `x y z visibility`, normalized |
| `/pose/world/lm/<n>` | `x y z`, metres, origin at the hips |
| `/pose/bounds` | `min_x max_x min_y max_y min_z max_z` over normalized landmarks |
| `/pose/world_bounds` | `min_x max_x min_y max_y min_z max_z` over world landmarks |

**Additional people.** Person 0 always uses the addresses above. Subsequent people insert their index after `/pose`: `/pose/1/lm/<n>`, `/pose/1/world/lm/<n>`, `/pose/1/bounds`, `/pose/1/world_bounds`, then `/pose/2/...`. There is no `/pose/0/...`. Multiple people require the separate pose model (**No holistic**) and `mediapipe.num_poses` above 1.

**Hands** (`left_hand` or `right_hand`):

| Address | Arguments |
|---|---|
| `/left_hand/lm/<n>` (n = 0–20) | `x y z`, normalized |
| `/left_hand/world/lm/<n>` | `x y z`, metres |
| `/left_hand/bounds` | 6 floats, same order as pose bounds |
| `/left_hand/world_bounds` | 6 floats, same order as pose bounds |

The right hand uses the same structure under `/right_hand/`.

Values are unrounded 32-bit floats. Bounds do not identify the extreme landmarks; use `json` if that is required.

**Clears:** on pose loss, `/pose/bounds` and `/pose/world_bounds`, and those of every other person seen since the previous clear, receive six `0.0` values. On hand loss, that hand's `bounds` and `world_bounds` receive six `0.0` values. Per-landmark addresses retain their last values; gate them on `/gesture/pose/tracking` or `/gesture/hand/tracking` being greater than 0.

## Status semantics

Status reports the number of poses or hands in the most recent completed detection. Detection is asynchronous and does not complete on every frame, so status is `0` on intermediate frames during continuous tracking. Use the tracking channels for presence detection.

A clear is sent once, on the frame where a pose or hand is lost.

## Landmark indices

Landmark indices follow MediaPipe's 33-point pose model and 21-point hand model. The index is the `id` field in `legacy`, the array position in `json`, and `<n>` in `float` (`/pose/lm/15` is the left wrist). These tables are generated from MediaPipe's landmark enums by `scripts/make_landmark_tables.py`.

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

Both hands use the same layout.
