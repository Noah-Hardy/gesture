# Quick Start

## Installation

Open the downloaded `.dmg` and drag `Gesture.app` into **Applications**. Run Gesture from the Applications folder. A copy run from the disk image is read-only and cannot be updated in place; Gesture shows a warning at launch in that case.

## The launcher window

The launcher has three collapsible sections, followed by the **Start**, **Save Config** and **Clear Log** buttons, a log pane and a status line. The open or collapsed state of each section is kept between launches.

- **Input**: tracking mode and video source.
- **OSC Output**: destination host and port.
- **Model & Performance**: pose model and FPS cap.

All other options are in **Gesture → Settings…**.

The launcher does not run MediaPipe itself. **Start** launches a separate engine process that captures video and performs tracking; the log pane shows that process's output. **Stop** shuts it down.

## First run

1. Set **OSC Output → Host** to the IP address of the receiving machine. The default, `127.0.0.1`, is correct when the receiver runs on the same Mac.
2. Set **Port** to the port the receiver listens on.
3. Under **Input**, select **Camera** with device ID `0` (the built-in or first external camera). See **Camera & NDI** for other sources.
4. Leave **Tracking mode** set to `all` to track pose and both hands.
5. Click **Start**.

The log reports the selected delegate (CPU or GPU), the loaded model, and a confirmation once tracking begins. A preview window titled **"Gesture Preview — not the OSC output"** shows the camera feed with detected landmarks overlaid. It is for visual confirmation only and is not transmitted.

To stop tracking, click **Stop**, or press `q` in the preview window. To run without a preview, clear **Show preview window** under **Input**; **Stop** is then the only way to end tracking.

## Confirming output

With the default `legacy` format, a receiver logging incoming OSC shows `/mp/status` continuously while the engine runs, and `/pose/raw`, `/left_hand/raw` and `/right_hand/raw` while a person is in frame. See **OSC Output** and the **OSC Address Reference** for details.

If nothing arrives, check that the host and port match the receiver and that no firewall blocks UDP on that port.

## Saving the configuration

The main window's fields, including tracking mode, are saved to `config.json` on **Start**, on quit, and before an update installs. **Save Config** saves them immediately and reports an invalid port or camera ID.

The **Settings** window saves its own fields with its **Save** button.

## Settings

**Gesture → Settings…** (⌘,) opens the General, Tracking, Preview and Advanced tabs. See **Settings**.

## Updates

Gesture checks for a newer release shortly after launch. If one is available, it shows the release notes and offers to install it. See **Updates**.

## Stopping and quitting

**Stop** (⌘.) shuts down the engine, which releases the camera, closes the OSC socket and closes the preview window. Quitting (⌘Q or the close button) stops the engine first, then exits.
