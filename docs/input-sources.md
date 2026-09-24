# Camera & NDI

## Webcam

Pick **📷 Camera** and set **Device ID** — `0` is almost always your Mac's built-in camera or the first camera macOS finds; try `1`, `2`, etc. if you have more than one connected.

The first time Gesture accesses the camera, macOS will prompt for camera permission. If you accidentally denied it, re-enable it under **System Settings → Privacy & Security → Camera**, then restart Gesture.

## NDI

Pick **🎥 NDI** to receive video over the network from an NDI sender (a switcher, another Mac running an NDI source app, OBS with the NDI plugin, etc.) instead of a local camera. Click **Refresh** to search the network for available sources — this takes a few seconds — then choose one from the dropdown.

A few behaviors worth knowing:

- Names are matched **case-insensitively**. An exact name match always wins. Otherwise a partial name is accepted only if it matches exactly one source: typing `switcher` connects to `Switcher-1 (Program)` if that's the only source containing "switcher", but not if `Switcher-2 (Preview)` is also on the network. Use the full name then.
- If the saved name doesn't match any source, or matches more than one, Gesture stops with a message saying so. It never silently connects to a different source, and it never switches to the webcam instead.
- With no source name saved, Gesture uses the first source it finds.
- **NDI bandwidth** (Settings → Advanced → Camera, `camera.ndi_bandwidth`) picks which stream to receive. `lowest` (the default) asks the sender for its small proxy stream, around 640×360, which is plenty for tracking and much lighter on the network. `highest` receives the full-resolution stream.

NDI and the OSC coordinates it produces don't have a fixed relationship to real-world size — see **Processing resolution**, below, for why that matters.

## If the camera or NDI source drops out

If frames stop arriving mid-session (a USB cable knocked loose, a network hiccup, a sender restarting), Gesture doesn't stop straight away. It keeps retrying, backing off from 0.1 up to 2 seconds between attempts, and reopens the camera or NDI connection after every 5 failed reads. The log shows `⚠️ Capture stopped delivering frames - retrying`, then `✅ Capture recovered` once frames return. OSC keeps flowing meanwhile: the heartbeat continues, so receivers can tell the engine is alive.

It gives up only after **Reconnect timeout** (Settings → Advanced → Camera, `camera.reconnect_timeout`, 30 seconds by default). Set it to `0` to keep retrying forever, which suits an unattended installation.

## Show preview window

**🖼️ Show preview window**, under **Input**, controls whether the separate confirmation window opens at all — titled **"Gesture Preview — not the OSC output"** so it's never mistaken for the data feed itself. It's on by default; uncheck it to run without the window (headless use, or if it's distracting). This mirrors **Mirror preview**, below, in also being a launcher checkbox that always overrides the saved `config.json` value for the run about to start (also available in **Settings → Preview**, and as `--preview`/`--no-preview` on the command line).

## Mirror preview

**🪞 Mirror preview window** flips the preview horizontally, so a webcam feed looks like a mirror (your right hand appears on the right side of the screen) rather than a video call (your right hand appears on the left). This is a **display-only** setting — it does not change any OSC data. Landmark coordinates and every value Gesture sends over the network are computed before the mirror flip and are completely unaffected by this checkbox.

## Processing resolution

Internally, every incoming frame — from a camera or from NDI — is resized to a fixed **processing resolution** before MediaPipe looks at it, and that resized frame is also what appears in the preview window. Set it under **Settings → Advanced → Camera** as **Processing width** and **Processing height** (`camera.processing_width` / `camera.processing_height`, default 640×480). It's the main quality/speed trade-off: a smaller size tracks faster, a larger one catches smaller or more distant people.

The resize **preserves the aspect ratio**. A source whose shape doesn't match the processing size (say, a 16:9 camera into the default 4:3) is scaled to fit and padded with black bars rather than stretched, and every normalized coordinate Gesture sends is mapped back to the original source frame. So 0–1 in x and y always spans your actual camera image, whatever the processing size.
