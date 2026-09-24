# TouchDesigner, Max, Unity, Isadora

Configure the receiver to listen for OSC over UDP on the port set in Gesture's **OSC Output** section (default 1234).

The approach depends on the output format (**Settings → Advanced → OSC → Output format**; see **OSC Output**):

- **`float`**: each landmark has its own address with float arguments. Suitable for receivers that bind addresses to numeric values. Recommended for Isadora and lossy networks.
- **`legacy`** (default) and **`json`**: one JSON string per channel, parsed in the receiver.

`json` and `float` are sent as OSC bundles. All receivers below unpack bundles automatically.

## TouchDesigner

**float:** add an **OSC In CHOP** on Gesture's port. Each address produces one channel per argument: x, y, z and, for pose, visibility. Use a **Select CHOP** with a pattern such as `pose/lm/*` or `left_hand/*` to select landmarks.

**legacy / json:** use an **OSC In DAT**, which lists each message's address and string argument per row. Filter by address (`/pose/raw`, `/left_hand/raw` and so on) and parse the argument with `json.loads()` in a Script DAT or DAT Execute callback. Field names for each format are in the **OSC Address Reference**.

## Max/MSP and Max for Live

`[udpreceive 1234]` receives OSC messages and unpacks bundles.

**float:** route by address and unpack the arguments, for example `[route /pose/lm/0]` → `[unpack f f f f]` for the nose's x, y, z and visibility. CNMAT `[OSC-route]` also supports wildcards (`/pose/lm/*`).

**legacy / json:** `[route /pose/raw]` → `[prepend parse]` → `[dict]` converts the JSON string to a dictionary. Extract landmarks with `[dict.unpack landmarks:]`, or with `[js]` and `JSON.parse`.

## Unity

OSC packages such as extOSC provide a per-address `Bind` call that invokes a callback with the incoming `OSCMessage`.

**float:** bind each required address, for example `/pose/lm/15` for the left wrist, and read x, y and z from `message.Values[0].FloatValue`, `[1]` and `[2]`. Alternatively, bind `/pose/lm/*` and parse the index from the address.

**legacy / json:** read the string argument (`message.Values[0].StringValue`) and deserialize it with `JsonUtility.FromJson<T>()` into a `[Serializable]` class matching the payload in the **OSC Address Reference**, or with a general-purpose JSON library.

## Isadora

Isadora maps OSC addresses to numeric values and cannot parse JSON strings directly. Use `float`.

1. In Gesture, set **Output format** to `float` and set **Port** to Isadora's OSC input port (default 1234). Click **Start**.
2. In Isadora, open **Communications → Stream Setup**, enable OSC on that port, and click **Auto-Detect Input**. Addresses such as `/pose/lm/0`, `/pose/lm/15` and `/gesture/pose/tracking` appear while a person is in frame. Assign channel numbers to the required addresses.
3. Add an **OSC Listener** actor for each address, set to its assigned channel. The output carries the address's values: x, y, z and visibility for pose landmarks; x, y and z for hand landmarks. Depending on the Isadora version, multiple arguments appear either as a list or as one stream entry per argument. Use a **Limit-Scale Value** actor to map the 0–1 range to stage coordinates.
4. Use `/gesture/pose/tracking` (greater than 0 when a person is present) to control effect visibility. Landmark values retain their last position when tracking is lost.

Common indices: 0 nose, 15 and 16 wrists, 23 and 24 hips (see **Landmark indices** in the **OSC Address Reference**). Normalized x and y range from 0 at the left and top of the frame to 1 at the right and bottom. `/pose/bounds` gives the body's extent as six floats (min x, max x, min y, max y, min z, max z).

### JSON formats in Isadora

If `legacy` or `json` is required, for example because another receiver on the same stream expects JSON, use Isadora's **JavaScript** actor:

1. Bind an OSC Listener to `/pose/raw` and connect its output to a JavaScript actor with one text input and three outputs.
2. Use the following script, which outputs x, y and z of landmark 15 (left wrist). Change `n` to select another landmark.

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

The script works with both `legacy` and `json`, as both list landmarks in index order. Because `/pose/raw` exceeds one network packet in both formats, frames can be lost on Wi-Fi or busy networks. `float` is not affected.

## Coordinates and presence

Normalized coordinates range from approximately 0 to 1 across the frame; scale by the target's width and height. World landmarks are in metres with the origin at the hips. For presence detection, use the tracking channel (`/gesture/pose/tracking`, or `/mp/tracking` in `legacy`) rather than status, which reads `0` on intermediate frames. See **OSC Output**.
