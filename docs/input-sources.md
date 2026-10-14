# Camera & NDI

## Camera

In the **Input** pane, select **Camera** and choose the device. `0` is normally the built-in camera or the first camera detected; additional cameras use `1`, `2` and so on. Changing the source needs a restart.

macOS requests camera permission on first use. If permission was denied, the **Camera access needed** card appears; enable it under **System Settings → Privacy & Security → Camera**, then click **Recheck**. See **Troubleshooting**.

## NDI

Select **NDI** to receive video from an NDI sender, such as a video switcher, another computer or OBS with the NDI plugin. The **NDI source** list fills in as sources are found (this takes a few seconds); select one.

Source matching:

- Names are case-insensitive. An exact match takes precedence.
- A partial name is accepted only if it matches exactly one source. For example, `switcher` selects `Switcher-1 (Program)` only if no other source name contains "switcher".
- If the name matches no source, or more than one, the engine stops and the **NDI source not found** card appears. It does not fall back to a different source or to the camera.
- If no name is set, the first source found is used.

**NDI bandwidth** (**Input** pane, `camera.ndi_bandwidth`) selects the received stream. `lowest` (the default) requests the sender's proxy stream, about 640×360, which is sufficient for tracking and uses less network bandwidth. `highest` requests full resolution.

## Source loss and reconnection

If frames stop arriving, the engine retries with a delay that increases from 0.1 to 2 seconds, and reopens the source after every 5 failed reads. The **Reconnecting** card shows over the preview, and the console logs `Capture stopped delivering frames - retrying`, followed by `Capture recovered` when frames resume. The OSC heartbeat continues during this time.

The engine exits after **Reconnect timeout** (**Input** pane, `camera.reconnect_timeout`, default 30 seconds). A value of `0` retries indefinitely, which is suitable for unattended installations.

## The preview

The preview is shown inside the **Live** pane, with the detected landmarks drawn over the processed frame, letterboxed to fit. There is no separate preview window. It is for visual confirmation only and is never transmitted. How it is drawn is set in the **Preview** pane. Turning the preview off or on never affects tracking or OSC output.

When running the engine from the command line without the app, nothing is shown by default. `--preview-window` opens a local OpenCV window (press `q` in that window to close it). `--preview` is the same flag, and `--no-preview` is accepted and does nothing.

## Mirror preview

**Mirror preview** (the **Mirror** chip on the stage, or the **Preview** pane) flips the preview horizontally. It affects the display only; landmark coordinates are computed from the unflipped frame.

## Processing resolution

Each frame is resized to the processing resolution before tracking. The preview shows this resized frame. Set it in the **Input** pane (`camera.processing_width` and `camera.processing_height`, default 640×480). Smaller sizes run faster; larger sizes detect smaller or more distant people.

The aspect ratio is preserved. A source with a different aspect ratio, such as 16:9 into 4:3, is scaled and padded rather than stretched, and all normalized coordinates are mapped back to the source frame. Normalized x and y therefore always span the full source image, from 0 to 1.
