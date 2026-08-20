# OSC Address Reference

Every message is a single OSC string argument containing compact JSON (see **OSC Output** for why). This page lists every address MP-OSC actually sends, with the exact payload shape for each.

## Payload building blocks

Two small JSON shapes appear throughout the tables below.

**A landmark list** (used in `/pose/raw`, `/pose/world`, hand raw/world channels) is an array of objects, one per landmark:

```json
{"type": "pose_0", "id": 23, "x": 0.52, "y": 0.48, "z": -0.12, "visibility": 0.98}
```

`x`/`y`/`z` and `visibility` are rounded to 3 decimal places. `visibility` is `null` for hand landmarks and for world landmarks (MediaPipe doesn't provide a visibility score for those) — it's only a real number for normalized pose landmarks. `id` is the landmark's index — see **Landmark indices**, below, for what each index means.

**A bounds object** (used in every `*_bounds` channel) reports the six extreme landmarks of a detection, not a numeric box:

```json
{
  "max_x": {"id": 23, "x": 0.9, "y": 0.5, "z": -0.1, "visibility": 0.99},
  "min_x": {"id": 11, "x": 0.1, "y": 0.6, "z": -0.2, "visibility": 0.98},
  "max_y": {"id": 27, "x": 0.5, "y": 0.95, "z": -0.3, "visibility": 0.97},
  "min_y": {"id": 0,  "x": 0.4, "y": 0.05, "z": -0.4, "visibility": 0.96},
  "max_z": {"id": 12, "x": 0.6, "y": 0.4, "z": 0.2, "visibility": 0.97},
  "min_z": {"id": 5,  "x": 0.3, "y": 0.7, "z": -0.5, "visibility": 0.95}
}
```

Each of the six values is the full landmark object at that extreme — note there is **no `type` field** here (unlike the landmark-list shape above), and `visibility` is **omitted entirely** rather than sent as `null` when it doesn't apply.

## Pose channels

Sent in `pose` mode and `all` mode.

| Address | Payload |
|---|---|
| `/pose/raw` | `{"timestamp": <epoch seconds>, "landmarks": [...normalized pose landmarks]}` |
| `/pose/world` | Same shape, world-space (real-world-scale, body-centered) coordinates |
| `/pose/raw_bounds` | Bounds object over the normalized landmarks |
| `/pose/world_bounds` | Bounds object over the world landmarks |
| `/mp/status` | `{"status": <N>}` — see **Status semantics**, below |

**Multiple people, same address.** If more than one pose is detected in a single frame (only possible with the separate, non-holistic pose model — see **OSC Output**), each pose is sent as its own complete `/pose/raw` (and `/pose/world`, `/pose/raw_bounds`, `/pose/world_bounds`) message, one after another, on the same address. They're distinguished only by the `type` field inside each landmark (`pose_0`, `pose_1`, and so on) — there is no separate per-person address.

## Hand channels

Sent in `hand` mode and `all` mode. Left and right hands are routed to entirely separate addresses.

| Address | Payload |
|---|---|
| `/left_hand/raw`, `/right_hand/raw` | `{"timestamp": <epoch>, "handedness": "Left"\|"Right"\|"Unknown", "landmarks": [...]}` |
| `/left_hand/world`, `/right_hand/world` | Same shape, world-space coordinates |
| `/left_hand/bounds`, `/right_hand/bounds` | Bounds object over normalized landmarks |
| `/left_hand/world_bounds`, `/right_hand/world_bounds` | Bounds object over world landmarks |
| `/hand/status` | `{"status": <N>}` — see **Status semantics**, below |

A hand whose handedness can't be determined confidently is routed to `/right_hand/*` along with genuine right hands — anything that isn't literally `"Left"` goes to the right-hand channel.

## The `type` field has three different conventions

The `type` string inside each landmark object identifies what the point belongs to, but its exact form depends on which detection path produced it:

| Situation | `type` values |
|---|---|
| `all` mode, default (combined holistic model) | Pose: always `pose_0` / `pose_world_0` (single person). Hands: `hand_left` / `hand_right` and `hand_world_left` / `hand_world_right` — labeled by handedness. |
| `all` mode with **No Holistic**, or `pose`/`hand` mode | Pose: `pose_0`, `pose_1`, ... (by detection order). Hands: `hand_0` / `hand_1` and `hand_world_0` / `hand_world_1` — labeled by **detection order, not handedness** (use the separate `"handedness"` field for that). |

If your receiving patch parses `type` to tell hands apart, it needs to handle both the `hand_left`/`hand_right` form and the positional `hand_0`/`hand_1` form, depending on whether holistic mode is active.

## Status semantics

`/mp/status` and `/hand/status` report the number of poses/hands detected **in the most recent frame the detector actually finished processing** — not a stable "is anyone here" flag. Because detection runs asynchronously and doesn't produce a fresh result on every single video frame, `status: 0` is sent on ordinary in-between frames too, even while someone is being continuously tracked. See **OSC Output** for the practical implication (debounce it) before wiring a receiver's "person present" logic directly to this value.

When a previously-tracked pose or hand disappears, one empty-landmarks message (empty `landmarks` array, empty bounds `{}`) is sent on the affected channels exactly once, on the transition frame — not repeated on every subsequent empty frame.

## Landmark indices

Every landmark's `id` field is an index into MediaPipe's standard 33-point pose model or 21-point hand model. These tables are generated directly from MediaPipe's own landmark enums (`scripts/make_landmark_tables.py`), so they can't drift out of sync with the library.

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
