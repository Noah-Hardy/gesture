# TouchDesigner, Max, Unity

General patterns for wiring up the three most common receivers. In every case you're listening for UDP/OSC on the **Port** configured in MP-OSC's OSC Output section, on the same machine (`127.0.0.1`) or over the network (the receiving machine's actual IP).

## TouchDesigner

Add an **OSC In DAT** or **OSC In CHOP**, set its network port to match MP-OSC's, and turn it on. Because every MP-OSC message is a single JSON string argument rather than separate float arguments, an **OSC In DAT** (which gives you the raw string per row) paired with a `json.loads()` call in a Script/Text DAT, or TouchDesigner's built-in `op('oscin1').JSONtoDicts()`-style helpers, is a more direct fit than an OSC In CHOP expecting numeric channels straight off the wire.

Route on the OSC address (e.g. `/pose/raw`, `/left_hand/raw`) with a Select or Filter DAT, then parse the JSON payload to pull out the `landmarks` array. See the **OSC Address Reference** for exact field names per channel, and **Landmark Reference** below for what each numeric index in a pose or hand landmark list corresponds to physically.

## Max/MSP (and Max for Live)

Max's `[udpreceive]` object listening on the configured port gets you the raw OSC packets; `[OSC-route]` (from the CNMAT OSC library, commonly bundled with Max) or a plain `[route]` on the address pattern splits them by channel. Since the payload is a JSON string rather than a list of floats, feed it to `[js]` (a small script calling `JSON.parse`) or the `[jit.string2json]`-style JSON-to-dict object available in recent Max versions, rather than expecting `[unpack]` to work directly on OSC arguments.

## Unity

`extOSC` and similar community OSC packages give you a `Bind` call per address (e.g. `/pose/raw`) that fires a callback with the raw `OscMessage`. Because the argument is one string, call `.StringValue` on it and deserialize with `JsonUtility.FromJson<T>()` against a small `[Serializable]` class matching the payload shape shown in the **OSC Address Reference** — or a general-purpose JSON library if you'd rather not hand-write a class per channel.

## A practical note for all three

Every payload's numeric coordinates are normalized (roughly 0–1 across the frame, see **Landmark Reference**), not pixels — so before mapping to a screen position or 3D scene, multiply by your target's actual width/height (and, for world landmarks, treat the values as real-world meters relative to the body's center rather than screen space). And regardless of receiver, remember that `status: 0` on `/mp/status` or `/hand/status` fires on ordinary stale-result frames, not only when someone leaves — debounce it if you're using it to drive a "person present" toggle. See **OSC Output** for why.
