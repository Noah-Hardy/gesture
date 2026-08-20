# Welcome

MP-OSC watches a camera or an NDI video feed, detects a person's body pose and hand positions with MediaPipe, and streams the result out as [OSC](https://en.wikipedia.org/wiki/Open_Sound_Control) messages on your local network. Anything that can receive OSC — TouchDesigner, Max/MSP, Unity, Unreal, Resolume, Ableton — can subscribe to that stream and react to where a person's body and hands are, live.

This guide is written for the app you're looking at: the launcher window, its checkboxes, and the data it sends. If you're comfortable with the command line and want to run the engine directly or build the app from source, see the **Appendix** at the end.

## What's in this guide

- **Quick Start** — first launch, the Start/Stop lifecycle, confirming data is arriving.
- **Camera & NDI** — choosing an input source, and the mirror-preview option.
- **OSC Output** — what MP-OSC sends and to where.
- **TouchDesigner, Max, Unity** — patterns for the three most common receivers.
- **Models & Performance** — trading accuracy for speed, and reading the FPS line.
- **Troubleshooting** — keyed to the exact messages the app prints.

The **OSC Address Reference** and **Appendix** at the bottom are reference material, not a read-through — jump to them when you need a specific address or flag.

## A note on accuracy

Every claim in this documentation was checked against the app's actual source code, not assumed from how the feature "should" work. Where something behaves in a way that might surprise you — a status channel that isn't quite what it sounds like, a config option that has no effect — it's called out explicitly rather than glossed over.
