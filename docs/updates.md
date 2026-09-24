# Updates

Gesture checks GitHub Releases for new versions and can install them in place.

## Launch check

Shortly after launch, Gesture checks for a newer release, at most once every 24 hours. It shows nothing if the installed version is current, or if GitHub cannot be reached.

**Settings → General** controls this:

- **Check for updates on launch**: disables the launch check. Manual checks remain available.
- **Include pre-release builds** (off by default): treats pre-releases as available updates.

## Manual check

Both of the following check immediately, regardless of the 24-hour limit:

- **Help → Check for Updates…**
- **Settings → General → Check Now**, next to the time of the last check.

A manual check reports when no update is available.

## Update dialog

When an update is found, a dialog shows its release notes and three options:

- **Install and Relaunch**: installs the update (see below).
- **Skip This Version**: suppresses launch-check prompts for this release. A manual check still offers it.
- **Later**: closes the dialog until the next check.

If the engine is running, Gesture asks to stop it before installing.

## Installation steps

1. **Download**: fetches the release's zip archive. The updater always uses the zip; the `.dmg` is for manual installation.
2. **Checksum**: compares the archive's SHA-256 against the published `.sha256` file.
3. **Signature**: verifies that the new `Gesture.app` is signed with the same Developer ID as the running app and passes Gatekeeper assessment.
4. **Swap**: Gesture quits. A helper script moves the current app aside as a backup, moves the new app into place, and reopens it.
5. **Relaunch**: the new version opens. `config.json` is not modified.

If any step fails, the existing version is kept or restored from the backup, and the dialog shows the failure along with a link to the release page.

## When self-update is unavailable

The updater requires the packaged app, installed in a location the current user can write to. Otherwise it explains why and links to the Releases page. This applies when:

- **Running from source** (`uv run python app.py`).
- **Running from the disk image or a translocated copy**: macOS runs apps opened from a mounted `.dmg`, or from Downloads, from a temporary read-only location. Move `Gesture.app` to **Applications** and reopen it.
- **Installed in a location the user cannot write to**, such as `/Applications` for a non-administrator account. Move the app to a writable location, or install updates manually.

A manual installation, replacing the old `.app` with the one from the Releases page, is equivalent to a self-update.
