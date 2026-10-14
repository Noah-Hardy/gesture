# Welcome

Gesture tracks body pose and hand landmarks from a camera or NDI video feed using MediaPipe, and sends the results as [OSC](https://en.wikipedia.org/wiki/Open_Sound_Control) messages over the local network. Any OSC-capable application, such as TouchDesigner, Max/MSP, Unity, Unreal, Resolume or Ableton, can receive the stream.

This guide covers the packaged app. Command-line use and building from source are covered in the **Appendix**.

## Contents

- **Quick Start**: the window, first launch, starting and stopping tracking, and confirming that data arrives.
- **Settings**: the Input, OSC Output, Tracking, Preview and Advanced panes, and Apply / Revert.
- **Updates**: how the built-in updater checks for and installs new versions.
- **Camera & NDI**: selecting an input source.
- **OSC Output**: what Gesture sends, and the three output formats.
- **TouchDesigner, Max, Unity, Isadora**: receiver setup.
- **Models & Performance**: accuracy and speed trade-offs, and the FPS readout.
- **Troubleshooting**: the fault cards shown over the preview, and explanations of log messages.

The **OSC Address Reference** and **Appendix** are reference material.
