# Updates

MP-OSC can check GitHub for a newer release and install it for you, without leaving the app.

## The silent launch check

A few seconds after MP-OSC opens, it quietly asks GitHub whether a newer release exists. If you're already on the latest version, nothing happens — no popup, no log spam. This check is throttled to at most once every 24 hours per launch and skips itself entirely if you're offline or GitHub is unreachable.

Turn this off, or include pre-release builds in what counts as "newer," under **Settings → General**:

- **Check for updates on launch** — uncheck to disable the silent check entirely. You can still check manually at any time (see below).
- **Include pre-release builds** (off by default) — pre-release tags on GitHub count as an available update when checked, ahead of a stable build that supersedes them.

## Checking manually

Two places trigger an immediate check, ignoring the usual throttling:

- **Help → Check for Updates…**
- **Settings → General → Check Now** (next to a "Last checked: …" label showing when MP-OSC last asked GitHub)

A manual check that finds nothing newer tells you so; a silent launch check that finds nothing stays quiet either way.

## When an update is found

A dialog opens showing the release notes for the new version, rendered the same way the in-app Help topics are. Three buttons:

- **Install and Relaunch** — starts the install flow below.
- **Skip This Version** — dismisses this dialog and won't prompt again for this specific release on future silent checks (a manual check will still offer it).
- **Later** — dismisses the dialog; MP-OSC asks again on its next scheduled check.

If the tracking engine is running when you click Install, MP-OSC asks to stop it first — the app has to be able to relaunch cleanly.

## What Install and Relaunch does

1. **Download** — fetches the release's zip archive from GitHub, with a progress bar showing megabytes and percent complete. (Releases also publish a `.dmg` for anyone installing by hand from the Releases page — the updater itself always uses the zip.)
2. **Verify checksum** — computes the SHA-256 of what was downloaded and compares it against the published `.sha256` file. A mismatch (a truncated or corrupted download) aborts here.
3. **Verify signature and notarization** — unpacks the archive and checks that the new `MP-OSC.app` is signed by the *same* Developer ID as the app you're currently running, and that it passes Gatekeeper's assessment. Anything else is rejected and discarded — this is what stops a tampered or mismatched build from ever being installed, regardless of what the checksum said.
4. **Swap** — once verified, MP-OSC quits itself; a small helper script waits for it to fully exit, moves the current `.app` aside as a backup, moves the new one into its place, and reopens it.
5. **Relaunch** — the new version opens automatically, on the same window position and settings as before (nothing in `config.json` is touched by an update).

## If something goes wrong

If the download, checksum, signature check, or swap fails at any point, the old version is left running (or restored from its backup) and nothing is lost — you keep using MP-OSC exactly as it was. The dialog shows what failed, with a button to open the release's GitHub page so you can download and install by hand if you'd rather not retry.

## When self-update isn't available

The updater only works on an installed, packaged `.app` sitting somewhere your account can write to. It explains why and offers to open the GitHub Releases page in your browser instead when:

- **You're running from source** (`uv run python app.py`), not the packaged app — updates only apply to `MP-OSC.app`.
- **MP-OSC is translocated, or still running from the `.dmg`** — opening `MP-OSC.app` straight out of the mounted disk image (instead of dragging it to Applications first), or from a Downloads folder, runs it from a temporary or read-only copy macOS controls, not the real location. MP-OSC warns about this once at launch; either way, drag `MP-OSC.app` to your **Applications** folder and reopen it from there.
- **MP-OSC is installed somewhere your account can't write to** — for example, `/Applications` on a Mac where you're not an administrator. An administrator can move it somewhere writable, or you can download and install the new version by hand.

In any of these cases, grabbing the new version manually from the project's GitHub Releases page and replacing the old `.app` works exactly the same as letting the updater do it.
