# TouchDesigner, Max, Unity, Isadora

Patterns for wiring up the most common receivers. In every case you listen for UDP/OSC on the **Port** set in Gesture's OSC Output section (1234 by default), on the same machine (`127.0.0.1`) or over the network (the receiving machine's actual IP).

Which approach fits depends on the output format (**Settings → Advanced → OSC → Output format**; see **OSC Output**):

- **`float`**: every landmark is its own address with plain float arguments. This suits any receiver that maps an address straight to numbers, and it's the one to pick for Isadora or a lossy network.
- **`legacy`** (the default) and **`json`**: one JSON string per channel. Parse it in the receiver.

The `json` and `float` formats arrive as OSC bundles. Every receiver below unpacks bundles on its own, so you bind to addresses exactly as you would for single messages.

## TouchDesigner

**float:** add an **OSC In CHOP** on Gesture's port. Each address becomes channels, one per argument (x, y, z and, for pose, visibility), which you can wire straight into anything that takes CHOP values. Use a **Select CHOP** with a pattern like `pose/lm/*` or `left_hand/*` to pick out the landmarks you need.

**legacy / json:** use an **OSC In DAT** instead, which gives you each message's address and string argument per row. Filter on the address (`/pose/raw`, `/left_hand/raw`, ...) and parse the argument with `json.loads()` in a Script DAT or a DAT Execute callback to get at the `landmarks` array. The **OSC Address Reference** has the exact field names per format.

## Max/MSP (and Max for Live)

`[udpreceive 1234]` gets you the OSC messages, with bundles already unpacked.

**float:** route by address and unpack the floats, e.g. `[route /pose/lm/0]` → `[unpack f f f f]` for the nose's x, y, z and visibility. The CNMAT `[OSC-route]` object does the same with wildcard support (`/pose/lm/*`).

**legacy / json:** `[route /pose/raw]` → `[prepend parse]` → `[dict]` turns the JSON string into a dictionary. From there, `[dict.unpack landmarks:]` or `[js]` with `JSON.parse` pulls out individual landmarks.

## Unity

`extOSC` and similar OSC packages give you a `Bind` call per address that fires a callback with the incoming `OSCMessage`.

**float:** bind each landmark address you need (e.g. `/pose/lm/15` for the left wrist) and read `message.Values[0].FloatValue`, `[1]`, `[2]` for x, y, z. Binding `/pose/lm/*` and parsing the number off the end of the address saves writing 33 bindings.

**legacy / json:** read the single string argument (`message.Values[0].StringValue`) and deserialize it with `JsonUtility.FromJson<T>()` against a small `[Serializable]` class matching the payload shape in the **OSC Address Reference**, or with a general-purpose JSON library if you'd rather not write a class per channel.

## Isadora

Isadora's OSC input maps each address to a numeric value, so it can't do much with a JSON string. **Use `float` mode.**

1. In Gesture, set **Settings → Advanced → OSC → Output format** to `float`, and set the OSC Output **Port** to Isadora's OSC input port (1234 by default, which is also Gesture's default). Click **Start**.
2. In Isadora, open **Communications → Stream Setup**, make sure OSC is enabled on that port, and click **Auto-Detect Input**. Addresses like `/pose/lm/0`, `/pose/lm/15` and `/gesture/pose/tracking` appear as someone moves in front of the camera. Give the ones you want a channel number and click OK.
3. Add an **OSC Listener** actor for each address and set its channel to the one you assigned. Its output carries that address's values: x, y, z and visibility for a pose landmark, x, y, z for a hand landmark. Depending on your Isadora version, a multi-value address shows up either as one list output or as one stream entry per argument; either way, route the values into a **Limit-Scale Value** actor to map 0–1 onto your stage or projector coordinates.
4. Drive visibility of your effect from `/gesture/pose/tracking` (above 0 means someone is there), not from the landmark values, which hold their last position when tracking is lost.

The landmark numbers are listed under **Landmark indices** in the **OSC Address Reference**: 0 is the nose, 15 and 16 are the wrists, 23 and 24 the hips. Normalized x and y run from 0 at the left/top of the camera frame to 1 at the right/bottom. `/pose/bounds` gives the extremes of the whole body as six floats (min x, max x, min y, max y, min z, max z), which is handy for a single "where is the person" value.

### Workaround: staying on a JSON format

If you have to stay on `legacy` or `json` (for example, another patch listening to the same stream expects JSON), Isadora can still get at the numbers through its **JavaScript** actor:

1. Bind an OSC Listener to `/pose/raw` and feed its output into a JavaScript actor with one text input and three outputs.
2. Use a script like this, which returns x, y and z of landmark 15 (the left wrist). Change `n` for a different landmark:

```
function main() {
    var n = 15;
    var payload = JSON.parse(arguments[0]);
    if (!payload.landmarks || payload.landmarks.length <= n) {
        return [0, 0, 0];
    }
    var lm = payload.landmarks[n];
    return [lm.x, lm.y, lm.z];
}
```

This works for both `legacy` and `json`, since both list landmarks in index order. It still depends on the JSON arriving intact, though: a full `/pose/raw` is larger than one network packet in both formats, so on Wi-Fi or a busy network some frames never reach Isadora at all. `float` mode avoids that entirely.

## A practical note for every receiver

Normalized coordinates run roughly 0–1 across the camera frame, not pixels. Multiply by your target's actual width and height before mapping to a screen position. World landmarks are real-world metres centred on the body's hips, not screen space. And whatever the receiver, use the tracking channel (`/gesture/pose/tracking`, or `/mp/tracking` in `legacy`) rather than status to drive a "person present" toggle: status reads `0` on ordinary in-between frames, not only when someone leaves. See **OSC Output** for why.
