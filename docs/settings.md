# Settings

**mp-osc → Settings…** (⌘,) opens a separate window with four tabs, covering everything the main launcher's three collapsible sections don't. Every field is bound to a real `config.json` key (the exact key names are in the **Appendix**) and is only written to disk when you click **Save**, at the bottom of the window — closing the window or switching tabs without saving discards your changes. **Restore Defaults**, next to Save, resets every field on every tab back to its built-in default; like everything else, that's only written to `config.json` once you then click Save.

## General

Controls for the self-updater (see the **Updates** guide for what these actually do):

- **Check for updates on launch** and **Include pre-release builds** — the two toggles that shape the silent launch check.
- A **Last checked** label and a **Check Now** button, for triggering an immediate check without waiting.

Below that, two shortcuts to the config file itself: **Open config.json** (in your default text editor) and **Reveal config.json in Finder** — useful if you want to edit a key this window doesn't expose, or just confirm what got saved.

## Tracking

Detection thresholds for both pose and hands — the numbers that trade false positives against missed detections. For pose: **Model** (lite/full/heavy), **Number of poses**, three confidence thresholds (**detection**, **tracking**, **pose presence**), and **Smooth landmarks**. For hands: **Number of hands** and its own three confidence thresholds (**detection**, **presence**, **tracking**).

You'll typically only touch this tab if tracking feels jittery (try raising a tracking-confidence threshold, or enabling landmark smoothing) or too eager to lose a detection in less-than-ideal lighting (try lowering a detection-confidence threshold).

## Preview

Everything about the on-screen preview window: whether it shows at all, whether it's mirrored, its title, and how landmarks are drawn on top of the video — separate color pickers for landmark and connection color (click a swatch to open the system color picker), plus thickness and radius fields for each. None of this affects the OSC data sent over the network — it's purely how the confirmation window on your screen looks.

## Advanced

The tab for tuning that goes beyond a typical session:

- **Camera** — raw capture width, height, FPS and buffer size (distinct from the processing resolution frames get resized to before MediaPipe sees them — see **Camera & NDI**).
- **Performance** — target FPS cap, the Show FPS/stats line, and garbage-collection tuning (enable/interval) for trading smoother frame timing against memory use on long sessions.
- **OSC** — the outgoing send queue size, i.e. how many messages can back up before the oldest are dropped (see **OSC Output**).
- **Backend**, explicitly labeled "applies on next Start" since these are launch-time only and can't change while the engine is running: **Force CPU delegate**, **Force GPU delegate** (with a memory-leak warning right on the checkbox — Apple Silicon's GPU delegate is known to leak memory, so this isn't a default to leave on), **Force legacy MediaPipe API**, and **No holistic** (use separate pose and hand models in `all` mode instead of the combined holistic model). These four used to be checkboxes in the main launcher window; they moved here because most sessions never touch them.

See the **Appendix** for the exact `config.json` key each field maps to, and **Models & Performance** for what the Backend toggles actually change under the hood.
