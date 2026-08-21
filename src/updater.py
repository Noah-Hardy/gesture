#!/usr/bin/env python3
"""
Self-Updater Module

Checks GitHub Releases for a newer MP-OSC build, and - if the user chooses to
install it - downloads, verifies and swaps the running .app bundle for the
new one before relaunching.

This module imports no tkinter. src.update_dialog owns the window; this
module owns the network, filesystem and subprocess work, run on a worker
thread and reported back to the GUI through the same queue.Queue -> 100ms
tk after() poll pattern src.gui already uses for the engine log and NDI
discovery (see UpdateController at the bottom).

A process cannot delete the .app bundle it is running out of and survive, so
the actual swap is done by a small detached shell script that waits for this
process to exit, then moves the new bundle into place. See
_write_install_script for the exact sequence and its rollback.
"""

# ============================================================================
# IMPORTS
# ============================================================================
import glob
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, NamedTuple, Optional

from src import docs
from src.net import ssl_context

# ============================================================================
# CONSTANTS
# ============================================================================
DEFAULT_REPO = "Noah-Hardy/mp-osc"
GITHUB_API = "https://api.github.com"
USER_AGENT = "MP-OSC-Updater"
BUNDLE_ID = "net.hardymail.mp-osc"

# The optional 4th component is for hotfix releases (e.g. 0.1.5.1).
_ASSET_RE = re.compile(r'^MP-OSC-(\d+\.\d+\.\d+(?:\.\d+)?)-macos-arm64\.zip$')

# spctl lives in /usr/sbin, not /usr/bin like codesign and ditto.
_SPCTL = '/usr/sbin/spctl'
_VERSION_RE = re.compile(r'^[vV]?(\d+(?:\.\d+){0,3})(?:[-+.](.+))?$')


class UpdateError(Exception):
    """A user-facing update failure - str(e) is safe to show in a dialog"""


# ============================================================================
# DATA TYPES
# ============================================================================
class Release(NamedTuple):
    tag: str
    version: str
    name: str
    notes: str
    html_url: str
    zip_url: str
    zip_name: str
    zip_size: int
    sha_url: str
    prerelease: bool


class CheckResult(NamedTuple):
    kind: str                        # 'available' | 'none' | 'error'
    release: Optional[Release] = None
    message: str = ''
    persist: Optional[dict] = None   # config['updates'] keys to write on the main thread


class Preflight(NamedTuple):
    ok: bool
    code: str
    detail: str
    app_path: Optional[str]


class StagedUpdate(NamedTuple):
    script_path: str
    install_log: str
    target_app: str


# ============================================================================
# VERSION
# ============================================================================
def current_version() -> str:
    """The running app's version. Empty string if it can't be determined."""
    override = os.environ.get('MPOSC_UPDATE_FAKE_VERSION')
    if override:
        return override
    return docs.app_version()


def parse_version(raw: str):
    """Parse a version/tag string into a comparable key, or None if unparseable"""
    if not raw:
        return None
    m = _VERSION_RE.match(raw.strip())
    if not m:
        return None
    core = tuple(int(part) for part in m.group(1).split('.'))
    core = (core + (0, 0, 0, 0))[:4]
    pre = m.group(2) or ''
    # A pre-release suffix sorts below the bare version: 0.2.0-rc.1 < 0.2.0
    rank = (0, pre) if pre else (1, '')
    return (core, rank)


def compare_versions(a: str, b: str) -> Optional[int]:
    """-1 / 0 / 1 if a is older/equal/newer than b, or None if either is unparseable"""
    ka, kb = parse_version(a), parse_version(b)
    if ka is None or kb is None:
        return None
    if ka < kb:
        return -1
    if ka > kb:
        return 1
    return 0


# ============================================================================
# BUNDLE / FILESYSTEM PATHS
# ============================================================================
def bundle_path() -> Optional[str]:
    """The running MP-OSC.app directory, or None when not running frozen"""
    if not getattr(sys, 'frozen', False):
        return None
    exe = os.path.realpath(sys.executable)
    contents = os.path.dirname(os.path.dirname(exe))     # .../MP-OSC.app/Contents
    app = os.path.dirname(contents)                       # .../MP-OSC.app
    if app.endswith('.app') and os.path.isfile(os.path.join(contents, 'Info.plist')):
        return app
    return None


def _updates_dir() -> str:
    """Writable staging area for downloaded archives, logs and helper scripts"""
    return os.path.join(os.path.expanduser('~/Library/Application Support'), 'mp-osc', 'updates')


def _install_log_path() -> str:
    return os.path.join(_updates_dir(), 'install.log')


# ============================================================================
# PREFLIGHT
# ============================================================================
def preflight(min_free_bytes: int = 0) -> Preflight:
    """
    Cheap, synchronous checks that decide whether an install can even be
    attempted, run both before offering the Install button and again right
    before the download starts.
    """
    app = bundle_path()
    if app is None:
        return Preflight(False, 'not_frozen', "Updates apply to the packaged app only.", None)

    real = os.path.realpath(app)
    if '/AppTranslocation/' in real:
        return Preflight(
            False, 'translocated',
            "MP-OSC is running from a temporary, read-only copy. Move MP-OSC.app to your "
            "Applications folder and reopen it, then try again.",
            app,
        )

    parent = os.path.dirname(app)

    try:
        st = os.statvfs(parent)
        if st.f_flag & os.ST_RDONLY:
            return Preflight(False, 'read_only_volume',
                             "MP-OSC is running from a read-only disk image.", app)
    except OSError:
        pass

    if not os.access(parent, os.W_OK):
        return Preflight(
            False, 'not_writable',
            f"{parent} is not writable by your account. An administrator can move MP-OSC.app "
            "to a writable location, or you can update it manually.",
            app,
        )

    if min_free_bytes:
        try:
            free = shutil.disk_usage(parent).free
            if free < min_free_bytes:
                mb = min_free_bytes // (1 << 20)
                return Preflight(False, 'low_disk',
                                 f"Not enough free disk space (need about {mb} MB).", app)
        except OSError:
            pass

    return Preflight(True, 'ok', '', app)


def cleanup_stale() -> None:
    """
    Sweep leftovers from interrupted or crashed installs. Safe to call on
    every launch - only touches files this module itself would have created.
    """
    app = bundle_path()
    if app:
        parent = os.path.dirname(app)
        _sweep_glob(os.path.join(parent, '.MP-OSC-update-*'), max_age_seconds=86400, is_dir=True)
        _sweep_glob(app + '.old-*', max_age_seconds=86400, is_dir=True)

    updates_dir = _updates_dir()
    if os.path.isdir(updates_dir):
        _sweep_glob(os.path.join(updates_dir, '*.part'), max_age_seconds=0, is_dir=False)
        _sweep_glob(os.path.join(updates_dir, 'install-*.sh'), max_age_seconds=86400, is_dir=False)
        _sweep_glob(os.path.join(updates_dir, 'MP-OSC-*.zip'), max_age_seconds=86400, is_dir=False)


def _sweep_glob(pattern: str, max_age_seconds: float, is_dir: bool) -> None:
    now = time.time()
    for path in glob.glob(pattern):
        try:
            if now - os.path.getmtime(path) < max_age_seconds:
                continue
        except OSError:
            continue
        try:
            if is_dir and os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            elif not is_dir and os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


def last_install_failed() -> Optional[bool]:
    """
    Best-effort read of the most recent install attempt's outcome from
    install.log. None if there is no log (nothing has been attempted).
    """
    log_path = _install_log_path()
    if not os.path.exists(log_path):
        return None
    try:
        with open(log_path, 'r', errors='replace') as f:
            f.seek(max(0, os.path.getsize(log_path) - 4096))
            tail = f.read()
    except OSError:
        return None
    failure_markers = ('aborting', 'failed to move', 'failed to install', 'restoring the old one')
    return any(marker in tail for marker in failure_markers)


# ============================================================================
# RELEASE DISCOVERY
# ============================================================================
def _repo() -> str:
    return os.environ.get('MPOSC_UPDATE_REPO', DEFAULT_REPO)


def check_for_update(config, manual: bool = False, timeout: int = 10) -> CheckResult:
    """
    Blocking. Runs on a worker thread. Reads config for throttling/etag state
    but never writes it - callers apply CheckResult.persist on the main
    thread, since Config.save() rewrites the whole file.
    """
    current = current_version()
    if not current:
        return CheckResult('error', message="Couldn't determine the running app version.")

    updates_cfg = config.get('updates') or {}
    now = time.time()

    if not manual:
        if not updates_cfg.get('check_on_launch', True):
            return CheckResult('none')
        rl_until = updates_cfg.get('rate_limited_until', 0) or 0
        if now < rl_until:
            return CheckResult('none')
        # No time-based throttle: every launch checks. The ETag below makes
        # the no-change case a 304, which GitHub does not count against the
        # rate limit, so a release published right after the previous check
        # still surfaces on the very next launch.
    else:
        rl_until = updates_cfg.get('rate_limited_until', 0) or 0
        if now < rl_until:
            when = time.strftime('%H:%M', time.localtime(rl_until))
            return CheckResult('error', message=f"GitHub rate limit reached. Try again after {when}.")

    include_pre = bool(updates_cfg.get('include_prereleases', True))
    headers = {
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': f'{USER_AGENT}/{current}',
    }
    # Manual checks always fetch fresh - a 304 has no body, so it cannot
    # carry the asset URLs a manual "install this" click needs. Launch
    # checks also fetch fresh while the last check saw a version newer
    # than this build: a 304 would silently swallow the update dialog the
    # user postponed with "Later".
    last_seen = updates_cfg.get('last_seen_version', '') or ''
    seen_newer = (compare_versions(last_seen, current) or 0) > 0
    etag = '' if (manual or seen_newer) else (updates_cfg.get('last_etag', '') or '')
    if etag:
        headers['If-None-Match'] = etag

    url = f"{GITHUB_API}/repos/{_repo()}/releases?per_page=10"
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
            new_etag = resp.headers.get('ETag', '') or ''
            data = json.loads(resp.read(1_000_000).decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return CheckResult('none', persist={'last_check': int(now)})
        if e.code in (403, 429):
            reset_header = e.headers.get('x-ratelimit-reset') if e.headers else None
            reset_ts = int(reset_header) if reset_header and reset_header.isdigit() else int(now + 3600)
            persist = {'rate_limited_until': reset_ts}
            if manual:
                when = time.strftime('%H:%M', time.localtime(reset_ts))
                return CheckResult('error', message=f"GitHub rate limit reached. Try again after {when}.",
                                   persist=persist)
            return CheckResult('none', persist=persist)
        message = f"GitHub returned an error ({e.code})."
        return CheckResult('error' if manual else 'none', message=message)
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError):
        message = "Couldn't reach GitHub to check for updates."
        return CheckResult('error' if manual else 'none', message=message)
    except (json.JSONDecodeError, ValueError):
        message = "GitHub returned an unexpected response."
        return CheckResult('error' if manual else 'none', message=message)

    if not isinstance(data, list):
        return CheckResult('error' if manual else 'none', message="GitHub returned an unexpected response.")

    release = _pick_release(data, include_pre)
    persist = {'last_check': int(now)}
    if new_etag:
        persist['last_etag'] = new_etag
    if release is not None:
        persist['last_seen_version'] = release.version

    if release is None:
        return CheckResult('none', message='No published release found.', persist=persist)

    cmp = compare_versions(release.version, current)
    if cmp is None or cmp <= 0:
        return CheckResult('none', persist=persist)

    if not manual:
        skipped = updates_cfg.get('skipped_version', '')
        if skipped and release.tag == skipped:
            return CheckResult('none', persist=persist)

    return CheckResult('available', release=release, persist=persist)


def _pick_release(releases: list, include_prereleases: bool) -> Optional[Release]:
    """Pick the newest release with a usable zip+sha256 asset pair, never trusting list order"""
    best = None
    best_key = None

    for item in releases:
        if not isinstance(item, dict) or item.get('draft'):
            continue
        if item.get('prerelease') and not include_prereleases:
            continue

        tag = item.get('tag_name') or ''
        key = parse_version(tag)
        if key is None:
            continue

        assets = item.get('assets') or []
        version = tag.lstrip('vV')
        zip_asset = next(
            (a for a in assets
             if _ASSET_RE.match(a.get('name', '') or '') and _ASSET_RE.match(a['name']).group(1) == version),
            None,
        )
        if zip_asset is None:
            continue
        sha_name = zip_asset['name'] + '.sha256'
        sha_asset = next((a for a in assets if a.get('name') == sha_name), None)
        if sha_asset is None:
            continue

        if best_key is None or key > best_key:
            best_key = key
            best = Release(
                tag=tag,
                version=version,
                name=item.get('name') or tag,
                notes=item.get('body') or '',
                html_url=item.get('html_url') or '',
                zip_url=zip_asset.get('browser_download_url', ''),
                zip_name=zip_asset.get('name', ''),
                zip_size=int(zip_asset.get('size') or 0),
                sha_url=sha_asset.get('browser_download_url', ''),
                prerelease=bool(item.get('prerelease')),
            )

    return best


# ============================================================================
# DOWNLOAD, VERIFY, STAGE
# ============================================================================
def download_and_install(release: Release, progress_cb: Callable[[dict], None],
                         cancel_event: Optional[threading.Event]) -> StagedUpdate:
    """
    Blocking. Runs on a worker thread. Downloads the release zip, verifies
    its checksum and code signature, extracts it next to the running bundle,
    and writes (but does not run) the swap-and-relaunch helper script.

    Raises UpdateError with a user-facing message on any failure; the
    running bundle is never touched by this function.
    """
    pf = preflight(min_free_bytes=max(release.zip_size, 1) * 5)
    if not pf.ok:
        raise UpdateError(pf.detail)

    updates_dir = _updates_dir()
    os.makedirs(updates_dir, exist_ok=True)
    zip_path = os.path.join(updates_dir, release.zip_name)
    part_path = zip_path + '.part'

    progress_cb({'kind': 'progress', 'phase': 'download', 'done': 0, 'total': release.zip_size})
    expected_hash = _fetch_expected_sha256(release.sha_url)

    digest = hashlib.sha256()
    downloaded = 0
    req = urllib.request.Request(release.zip_url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as resp:
            with open(part_path, 'wb') as f:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise UpdateError('Cancelled.')
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    progress_cb({'kind': 'progress', 'phase': 'download',
                                'done': downloaded, 'total': release.zip_size})
    except UpdateError:
        _silent_remove(part_path)
        raise
    except Exception as e:
        _silent_remove(part_path)
        raise UpdateError(f"The download failed: {e}") from e

    if digest.hexdigest().lower() != expected_hash:
        _silent_remove(part_path)
        raise UpdateError('The download was incomplete or corrupted.')
    os.replace(part_path, zip_path)

    progress_cb({'kind': 'verifying', 'phase': 'extract'})
    extract_dir = os.path.join(os.path.dirname(pf.app_path), f'.MP-OSC-update-{os.getpid()}')
    shutil.rmtree(extract_dir, ignore_errors=True)
    os.makedirs(extract_dir, exist_ok=True)
    try:
        subprocess.run(['/usr/bin/ditto', '-x', '-k', zip_path, extract_dir],
                       check=True, capture_output=True, timeout=900)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise UpdateError("Couldn't unpack the update.") from e

    staged_app = _find_app(extract_dir)
    if staged_app is None:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise UpdateError("The downloaded update did not contain MP-OSC.app.")

    progress_cb({'kind': 'verifying', 'phase': 'signature'})
    try:
        _verify_staged_app(staged_app, release.version, pf.app_path)
    except Exception:
        # Any failure here, expected or not, must not leave the extracted
        # bundle sitting next to the real app in /Applications.
        shutil.rmtree(extract_dir, ignore_errors=True)
        _silent_remove(zip_path)
        raise

    install_log = _install_log_path()
    try:
        script_path = _write_install_script(updates_dir, staged_app, pf.app_path, extract_dir, zip_path)
    except OSError as e:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise UpdateError(f"Couldn't prepare the installer: {e}") from e

    return StagedUpdate(script_path=script_path, install_log=install_log, target_app=pf.app_path)


def _fetch_expected_sha256(sha_url: str) -> str:
    """
    Fetch the published .sha256 asset and return its checksum, tolerating
    both a bare filename and a path-prefixed one (scripts/release.sh runs
    shasum from the repo root, so the published file may read
    "<hash>  dist/MP-OSC-<version>-macos-arm64.zip").
    """
    req = urllib.request.Request(sha_url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=15, context=ssl_context()) as resp:
        text = resp.read(4096).decode('utf-8', errors='replace')
    field = text.strip().split()[0] if text.strip() else ''
    if not re.fullmatch(r'[0-9a-fA-F]{64}', field):
        raise UpdateError('The published checksum file was not in the expected format.')
    return field.lower()


def _find_app(extract_dir: str) -> Optional[str]:
    for name in os.listdir(extract_dir):
        if name.endswith('.app') and not name.startswith('__MACOSX'):
            return os.path.join(extract_dir, name)
    return None


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ----------------------------------------------------------------------------
# Signature verification - the security boundary. The .sha256 proves the
# download matches what GitHub served, not that it's genuine; this is what
# actually stops a compromised or mismatched build from being installed.
# ----------------------------------------------------------------------------
def _team_identifier(app_path: str) -> Optional[str]:
    try:
        result = subprocess.run(['/usr/bin/codesign', '-dv', '--verbose=4', app_path],
                                capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return None
    for line in (result.stderr or '').splitlines():
        if line.startswith('TeamIdentifier='):
            value = line.split('=', 1)[1].strip()
            return value if value and value != 'not set' else None
    return None


def _read_bundle_version(app_path: str) -> str:
    plist_path = os.path.join(app_path, 'Contents', 'Info.plist')
    try:
        with open(plist_path, 'rb') as f:
            data = plistlib.load(f)
        return str(data.get('CFBundleShortVersionString', ''))
    except (OSError, ValueError, plistlib.InvalidFileException):
        return ''


def _verify_staged_app(staged_app: str, expected_version: str, running_app_path: Optional[str]) -> None:
    requirement = f'anchor apple generic and identifier "{BUNDLE_ID}"'
    team_id = _team_identifier(running_app_path) if running_app_path else None
    if team_id:
        requirement += f' and certificate leaf[subject.OU]="{team_id}"'

    try:
        subprocess.run(
            ['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2',
             '-R=' + requirement, staged_app],
            check=True, capture_output=True, timeout=300,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        raise UpdateError('This update could not be verified and was discarded.') from e

    try:
        subprocess.run([_SPCTL, '--assess', '--type', 'exec', '-vv', staged_app],
                       check=True, capture_output=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        raise UpdateError('This update was not accepted by Gatekeeper and was discarded.') from e

    staged_version = _read_bundle_version(staged_app)
    if staged_version != expected_version:
        raise UpdateError('The downloaded update does not match the expected version.')


# ----------------------------------------------------------------------------
# Swap helper - a detached shell script, since this process cannot delete
# the bundle it is running out of and survive.
# ----------------------------------------------------------------------------
def _write_install_script(updates_dir: str, staged_app: str, target_app: str,
                          extract_dir: str, zip_path: str) -> str:
    script_path = os.path.join(updates_dir, f'install-{os.getpid()}.sh')
    script = f"""#!/bin/sh
# Generated by src/updater.py - swaps in a downloaded, verified MP-OSC.app
# once the running process (PID passed as $1) has exited.
set -u
PID="$1"
TARGET={shlex.quote(target_app)}
STAGED={shlex.quote(staged_app)}
EXTRACT_DIR={shlex.quote(extract_dir)}
ZIP={shlex.quote(zip_path)}
SELF="$0"

i=0
while kill -0 "$PID" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge 150 ]; then
        echo "install: parent did not exit within 30s, aborting" >&2
        exit 3
    fi
    sleep 0.2
done

BACKUP="$TARGET.old-$$"
if ! mv "$TARGET" "$BACKUP"; then
    echo "install: failed to move aside the old bundle" >&2
    exit 4
fi

if ! mv "$STAGED" "$TARGET" 2>/dev/null; then
    if ! /usr/bin/ditto "$STAGED" "$TARGET" 2>/dev/null; then
        echo "install: failed to install the new bundle, restoring the old one" >&2
        rm -rf "$TARGET"
        mv "$BACKUP" "$TARGET"
        exit 5
    fi
fi

/usr/bin/xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null
rm -rf "$BACKUP"
touch "$TARGET"
/usr/bin/open "$TARGET"

rm -rf "$EXTRACT_DIR" "$ZIP" "$SELF"
echo "install: done"
"""
    with open(script_path, 'w') as f:
        f.write(script)
    os.chmod(script_path, 0o700)
    return script_path


def spawn_installer(staged: StagedUpdate) -> None:
    """
    Launch the detached swap script and return immediately. Call this last,
    right before the GUI destroys its own window - our exit is what the
    script's wait loop is watching for.
    """
    os.makedirs(os.path.dirname(staged.install_log), exist_ok=True)
    log_fd = open(staged.install_log, 'ab')
    try:
        subprocess.Popen(
            ['/bin/sh', staged.script_path, str(os.getpid())],
            start_new_session=True, cwd='/',
            stdin=subprocess.DEVNULL, stdout=log_fd, stderr=subprocess.STDOUT,
            close_fds=True,
        )
    finally:
        log_fd.close()


# ============================================================================
# GUI-FACING CONTROLLER
# ============================================================================
class UpdateController:
    """
    Owns the worker thread(s) for checks and installs. Talks to the GUI only
    by putting (tag, payload) tuples on the same queue.Queue the rest of
    src.gui already drains from its 100ms after() poll - this class must
    never touch a tk widget.

    payload['kind'] is one of:
      'none' | 'available' | 'error'                  (from a check)
      'progress' | 'verifying' | 'ready' | 'failed'    (from an install)
    """

    def __init__(self, out_queue, config, tag: str = 'update'):
        self._queue = out_queue
        self._config = config
        self._tag = tag
        self._cancel_event = None
        self._busy = False
        self._last_progress_at = 0.0

    @property
    def busy(self) -> bool:
        return self._busy

    def check_async(self, manual: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._check_worker, args=(manual,), daemon=True).start()

    def _check_worker(self, manual: bool) -> None:
        try:
            result = check_for_update(self._config, manual=manual)
        except Exception as e:  # pragma: no cover - defensive, keeps the GUI usable
            result = CheckResult('error', message=f'Unexpected error: {e}')
        self._busy = False
        self._emit({'kind': result.kind, 'release': result.release,
                    'message': result.message, 'persist': result.persist, 'manual': manual})

    def install_async(self, release: Release) -> None:
        if self._busy:
            return
        self._busy = True
        self._cancel_event = threading.Event()
        threading.Thread(target=self._install_worker, args=(release, self._cancel_event),
                         daemon=True).start()

    def cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()

    def _install_worker(self, release: Release, cancel_event: threading.Event) -> None:
        def progress_cb(payload: dict) -> None:
            now = time.monotonic()
            terminal = payload.get('kind') != 'progress'
            if terminal or now - self._last_progress_at >= 0.2:  # throttle to ~5/sec
                self._last_progress_at = now
                self._emit(payload)

        try:
            staged = download_and_install(release, progress_cb, cancel_event)
        except UpdateError as e:
            self._busy = False
            self._emit({'kind': 'failed', 'message': str(e)})
            return
        except Exception as e:  # pragma: no cover - defensive
            self._busy = False
            self._emit({'kind': 'failed', 'message': f'Unexpected error: {e}'})
            return

        self._busy = False
        self._emit({'kind': 'ready', 'staged': staged})

    def _emit(self, payload: dict) -> None:
        self._queue.put((self._tag, payload))
