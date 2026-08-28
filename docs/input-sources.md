# Camera & NDI

## Webcam

Pick **📷 Camera** and set **Device ID** — `0` is almost always your Mac's built-in camera or the first camera macOS finds; try `1`, `2`, etc. if you have more than one connected.

The first time MP-OSC accesses the camera, macOS will prompt for camera permission. If you accidentally denied it, re-enable it under **System Settings → Privacy & Security → Camera**, then restart MP-OSC.

## NDI

Pick **🎥 NDI** to receive video over the network from an NDI sender (a switcher, another Mac running an NDI source app, OBS with the NDI plugin, etc.) instead of a local camera. Click **Refresh** to search the network for available sources — this takes a few seconds — then choose one from the dropdown.

A few behaviors worth knowing:

- Matching a source by name is a **case-insensitive substring match**, not an exact match. Typing `switcher` will match a source literally named `Switcher-1 (Program)`.
- If the name you've saved doesn't match anything currently on the network, MP-OSC connects to the first available source instead, rather than failing outright.
- If no NDI sources are found at all after searching, MP-OSC falls back to the webcam automatically.

NDI and the OSC coordinates it produces don't have a fixed relationship to real-world size — see **Processing resolution**, below, for why that matters.

## Show preview window

**🖼️ Show preview window**, under **Input**, controls whether the separate confirmation window opens at all — titled **"MP-OSC Preview — not the OSC output"** so it's never mistaken for the data feed itself. It's on by default; uncheck it to run without the window (headless use, or if it's distracting). This mirrors **Mirror preview**, below, in also being a launcher checkbox that always overrides the saved `config.json` value for the run about to start (also available in **Settings → Preview**, and as `--preview`/`--no-preview` on the command line).

## Mirror preview

**🪞 Mirror preview window** flips the preview horizontally, so a webcam feed looks like a mirror (your right hand appears on the right side of the screen) rather than a video call (your right hand appears on the left). This is a **display-only** setting — it does not change any OSC data. Landmark coordinates and every value MP-OSC sends over the network are computed before the mirror flip and are completely unaffected by this checkbox.

## Processing resolution

Internally, every incoming frame — from a camera or from NDI — is resized to a fixed **processing resolution** before MediaPipe looks at it, and that resized frame is also what appears in the preview window. This is a `config.json` setting (`camera.processing_width` / `camera.processing_height`, default 640×480) rather than something in the launcher form.

**This resize does not preserve aspect ratio.** If your camera or NDI source has a different aspect ratio than the configured processing resolution (for example, a 16:9 source resized into a 4:3 processing size), the image — and the body it's tracking — will be stretched or squashed. If you're seeing landmark positions that seem subtly off, or people that look unnaturally wide or narrow in the preview, check that the processing resolution's aspect ratio matches your actual source. See the **Appendix** for how to change it.
