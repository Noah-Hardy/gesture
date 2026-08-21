#!/usr/bin/env python3
"""
Settings Window / Launcher GUI Module
Plain tkinter/ttk front-end for the MediaPipe OSC tracking engine

The GUI does NOT run MediaPipe in-process: on macOS both tkinter's mainloop
and cv2.imshow require the process main thread. Instead the form is turned
into a CLI argv and the engine is launched as a subprocess of the same
executable. Stopping sends SIGINT so the child hits its existing
KeyboardInterrupt/finally cleanup path.
"""

# ============================================================================
# IMPORTS
# ============================================================================
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Any, List, Optional

from src import docs, theme
from src.config import get_config, valid_port
from src.help_window import HelpWindow
from src.settings_window import SettingsWindow
from src.update_dialog import UpdateDialog
from src.updater import UpdateController, cleanup_stale, spawn_installer

# NDI discovery is optional - the library may not be installed
try:
    from src.ndi_capture import list_ndi_sources, NDI_AVAILABLE
except Exception:  # pragma: no cover - defensive, keeps the GUI usable
    NDI_AVAILABLE = False

    def list_ndi_sources() -> List[str]:
        """Fallback stub when the NDI module cannot be imported"""
        return []


# ============================================================================
# CONSTANTS
# ============================================================================
MAX_LOG_LINES = 3000          # Log pane ring-buffer cap
POLL_INTERVAL_MS = 100        # Queue drain / child watchdog interval
TERMINATE_AFTER = 5.0         # Seconds after SIGINT before SIGTERM
KILL_AFTER = 8.0              # Seconds after SIGINT before SIGKILL
CLOSE_GRACE = 3.0             # Seconds to wait for the child on window close

MODES = ('pose', 'hand', 'all')
POSE_MODELS = ('lite', 'full', 'heavy')

# Queue message tags (worker threads never touch tk widgets directly)
TAG_LOG = 'log'
TAG_NDI = 'ndi'
TAG_UPDATE = 'update'

UPDATE_CHECK_DELAY_MS = 1500  # deferred so a slow DNS lookup never delays first paint

# Startup spinner: shown from Start until the engine prints its ready line
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_INTERVAL_MS = 90
ENGINE_READY_PREFIX = "🟢 Engine ready"

# Engine exit codes (see EXIT_* in main.py) mapped to a clear status/log
# message, so _on_engine_exit can say *why* instead of a bare "code N" for
# every nonzero return.
ENGINE_EXIT_MESSAGES = {
    2: "⚠️  Engine stopped: camera/NDI source was lost",
    3: "❌ Engine crashed - see the log above for details",
}


# ============================================================================
# LAUNCHER GUI
# ============================================================================
class LauncherGui:
    """
    Settings window that builds a command line and supervises the engine
    subprocess, streaming its stdout into a read-only log pane
    """

    def __init__(self, root: tk.Tk):
        """Build the window and populate the form from the saved config"""
        self.root = root
        self.config = get_config()

        self.proc = None                      # type: Optional[subprocess.Popen]
        self.reader = None                    # type: Optional[threading.Thread]
        self.stop_requested_at = None         # type: Optional[float]
        self.terminated = False
        self.killed = False
        self._queue = queue.Queue()           # type: queue.Queue
        self._form_widgets = []               # type: List[Any]
        self._starting = False                # engine launched, ready line not yet seen
        self._spinner_job = None
        self._spinner_idx = 0
        self.help_window = None               # type: Optional[HelpWindow]
        self.settings_window = None           # type: Optional[SettingsWindow]
        self.update_dialog = None             # type: Optional[UpdateDialog]

        root.title("MediaPipe OSC Launcher")
        root.minsize(560, 520)

        theme.apply_theme(root)
        self.updater = UpdateController(self._queue, self.config, tag=TAG_UPDATE)

        self._init_variables()
        self._build_layout()
        self._build_menubar()
        self._poll()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

        if not NDI_AVAILABLE:
            self._set_status("⚠️  NDI library not available - camera input only")
        else:
            self._set_status("✅ Ready")

        # Cheap and local - safe to do before the queue/poll loop is relied on.
        try:
            cleanup_stale()
        except Exception:
            pass
        self.root.after(UPDATE_CHECK_DELAY_MS, lambda: self.updater.check_async(manual=False))

    # ------------------------------------------------------------------------
    # Form state
    # ------------------------------------------------------------------------
    def _init_variables(self) -> None:
        """Create tk variables seeded from the configuration file"""
        # Mode has no config key - GUI-local default
        self.var_mode = tk.StringVar(value='all')

        # Input
        self.var_source = tk.StringVar()
        self.var_camera = tk.StringVar()
        self.var_ndi_source = tk.StringVar()

        # OSC output - default host is localhost; port always has a value
        # since a blank one cannot start the engine.
        self.var_host = tk.StringVar()
        self.var_port = tk.StringVar()

        # Model & performance
        self.var_pose_model = tk.StringVar()
        self.var_fps_cap = tk.StringVar()
        self.var_show_fps = tk.BooleanVar()

        # Preview
        self.var_mirror = tk.BooleanVar()

        # Backend toggles - launch-only (never part of the argv used to
        # resume a saved config), but now persisted so Settings remembers
        # them. Owned here and shared with SettingsWindow, which is where
        # they're actually edited (see _build_advanced_tab).
        self.var_force_cpu = tk.BooleanVar()
        self.var_force_gpu = tk.BooleanVar()
        self.var_force_legacy = tk.BooleanVar()
        self.var_no_holistic = tk.BooleanVar()

        self._seed_vars_from_config()

        # Force CPU and Force GPU are mutually exclusive - main.py accepts
        # both flags at once with undefined results, so the UI enforces it.
        self.var_force_cpu.trace_add('write', lambda *a: self._enforce_delegate_choice('cpu'))
        self.var_force_gpu.trace_add('write', lambda *a: self._enforce_delegate_choice('gpu'))

        self.var_status = tk.StringVar(value="")

    def _seed_vars_from_config(self) -> None:
        """Push the current config values into the form's tk variables

        Used at construction and again after Settings saves, so the launcher
        form (and the argv/Save Config built from it) never holds stale
        values that would overwrite what Settings just wrote.
        """
        cfg = self.config

        use_ndi = bool(cfg.get('camera', 'use_ndi', False)) and NDI_AVAILABLE
        self.var_source.set('ndi' if use_ndi else 'camera')
        self.var_camera.set(str(cfg.get('camera', 'device_id', 0)))
        self.var_ndi_source.set(cfg.get('camera', 'ndi_source') or '')

        self.var_host.set(str(cfg.get('osc', 'host', '127.0.0.1')))
        self.var_port.set(str(cfg.get('osc', 'port', 1234)))

        model = cfg.get('mediapipe', 'pose_model_type', 'lite')
        if model not in POSE_MODELS:
            model = 'lite'
        self.var_pose_model.set(model)
        self.var_fps_cap.set(str(cfg.get('performance', 'target_fps', 0)))
        self.var_show_fps.set(bool(cfg.get('performance', 'show_fps', False)))

        self.var_mirror.set(bool(cfg.get('display', 'mirror_preview', False)))

        self.var_force_cpu.set(bool(cfg.get('performance', 'force_cpu', False)))
        self.var_force_gpu.set(bool(cfg.get('performance', 'force_gpu', False)))
        self.var_force_legacy.set(bool(cfg.get('performance', 'force_legacy', False)))
        self.var_no_holistic.set(bool(cfg.get('performance', 'no_holistic', False)))

    def _reload_form_from_config(self) -> None:
        """Settings saved - refresh the launcher form from the new config"""
        self._seed_vars_from_config()
        self._update_source_state()
        self._set_status("💾 Settings saved")

    # ------------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------------
    def _build_layout(self) -> None:
        """Assemble the labelled frames, buttons, status line and log pane"""
        outer = ttk.Frame(self.root, padding=10)
        outer.grid(row=0, column=0, sticky='nsew')
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        # Open log pane absorbs extra height; collapsed it gives the row up
        self._outer = outer
        outer.rowconfigure(4, weight=1 if self.config.get('ui', 'log_section_open', True) else 0)

        self._build_input_frame(outer, row=0)
        self._build_osc_frame(outer, row=1)
        self._build_model_frame(outer, row=2)
        self._build_buttons(outer, row=3)
        self._build_log(outer, row=4)
        self._build_status(outer, row=5)

        self._update_source_state()

    def _build_input_frame(self, parent: ttk.Frame, row: int) -> None:
        """Mode selection and camera/NDI input source"""
        section = theme.CollapsibleSection(
            parent, title="Input",
            open=bool(self.config.get('ui', 'input_section_open', True)),
            on_toggle=lambda is_open: self._on_section_toggle('input_section_open', is_open))
        section.grid(row=row, column=0, sticky='ew', pady=(0, 8))
        frame = section.body
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Tracking mode:").grid(row=0, column=0, sticky='w', pady=2)
        mode_box = ttk.Combobox(frame, textvariable=self.var_mode, values=list(MODES),
                                state='readonly', width=12)
        mode_box.grid(row=0, column=1, sticky='w', pady=2)
        self._register(mode_box, 'readonly')

        ttk.Separator(frame, orient='horizontal').grid(row=1, column=0, columnspan=3,
                                                       sticky='ew', pady=6)

        self.radio_camera = ttk.Radiobutton(frame, text="📷 Camera", value='camera',
                                            variable=self.var_source,
                                            command=self._update_source_state)
        self.radio_camera.grid(row=2, column=0, sticky='w', pady=2)
        self._register(self.radio_camera, 'normal')

        ttk.Label(frame, text="Device ID:").grid(row=2, column=1, sticky='e', padx=(0, 6), pady=2)
        self.spin_camera = ttk.Spinbox(frame, from_=0, to=99, width=6,
                                       textvariable=self.var_camera)
        self.spin_camera.grid(row=2, column=2, sticky='w', pady=2)
        self._register(self.spin_camera, 'normal')

        self.radio_ndi = ttk.Radiobutton(frame, text="🎥 NDI", value='ndi',
                                         variable=self.var_source,
                                         command=self._update_source_state)
        self.radio_ndi.grid(row=3, column=0, sticky='w', pady=2)
        self._register(self.radio_ndi, 'normal')

        self.combo_ndi = ttk.Combobox(frame, textvariable=self.var_ndi_source, values=[])
        self.combo_ndi.grid(row=3, column=1, sticky='ew', padx=(0, 6), pady=2)
        self._register(self.combo_ndi, 'normal')

        self.btn_refresh = ttk.Button(frame, text="Refresh", width=9,
                                      command=self._refresh_ndi_sources)
        self.btn_refresh.grid(row=3, column=2, sticky='w', pady=2)
        self._register(self.btn_refresh, 'normal')

        ttk.Separator(frame, orient='horizontal').grid(row=4, column=0, columnspan=3,
                                                       sticky='ew', pady=6)

        chk_mirror = ttk.Checkbutton(
            frame, text="🪞 Mirror preview window (display only - OSC data is unchanged)",
            variable=self.var_mirror)
        chk_mirror.grid(row=5, column=0, columnspan=3, sticky='w', pady=2)
        self._register(chk_mirror, 'normal')

        if not NDI_AVAILABLE:
            self.var_source.set('camera')
            self.radio_ndi.state(['disabled'])
            self.combo_ndi.state(['disabled'])
            self.btn_refresh.state(['disabled'])

    def _build_osc_frame(self, parent: ttk.Frame, row: int) -> None:
        """OSC destination host and port"""
        section = theme.CollapsibleSection(
            parent, title="OSC Output",
            open=bool(self.config.get('ui', 'osc_section_open', True)),
            on_toggle=lambda is_open: self._on_section_toggle('osc_section_open', is_open))
        section.grid(row=row, column=0, sticky='ew', pady=(0, 8))
        frame = section.body
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Host:").grid(row=0, column=0, sticky='w', pady=2)
        entry_host = ttk.Entry(frame, textvariable=self.var_host)
        entry_host.grid(row=0, column=1, sticky='ew', padx=(6, 0), pady=2)
        self._register(entry_host, 'normal')

        ttk.Label(frame, text="Port:").grid(row=1, column=0, sticky='w', pady=2)
        entry_port = ttk.Entry(frame, textvariable=self.var_port, width=10)
        entry_port.grid(row=1, column=1, sticky='w', padx=(6, 0), pady=2)
        self._register(entry_port, 'normal')

    def _build_model_frame(self, parent: ttk.Frame, row: int) -> None:
        """Pose model selection and frame rate cap

        Show FPS / Force CPU / Force legacy / No holistic used to live here
        as always-visible checkboxes. They're launch-time toggles most
        sessions never touch, so they moved to Settings -> Advanced
        (src.settings_window), leaving this section - and the window - short.
        """
        section = theme.CollapsibleSection(
            parent, title="Model & Performance",
            open=bool(self.config.get('ui', 'model_section_open', False)),
            on_toggle=lambda is_open: self._on_section_toggle('model_section_open', is_open))
        section.grid(row=row, column=0, sticky='ew', pady=(0, 8))
        frame = section.body
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Pose model:").grid(row=0, column=0, sticky='w', pady=2)
        model_box = ttk.Combobox(frame, textvariable=self.var_pose_model,
                                 values=list(POSE_MODELS), state='readonly', width=12)
        model_box.grid(row=0, column=1, sticky='w', padx=(6, 0), pady=2)
        self._register(model_box, 'readonly')

        ttk.Label(frame, text="FPS cap:").grid(row=1, column=0, sticky='w', pady=2)
        entry_fps = ttk.Entry(frame, textvariable=self.var_fps_cap, width=8)
        entry_fps.grid(row=1, column=1, sticky='w', padx=(6, 0), pady=2)
        self._register(entry_fps, 'normal')
        ttk.Label(frame, text="(0 or empty = uncapped)", style='Dim.TLabel').grid(
            row=1, column=2, sticky='w', padx=(6, 0), pady=2)

        ttk.Label(frame, text="More options in Settings (⌘,)",
                 style='Dim.TLabel').grid(row=2, column=0, columnspan=3, sticky='w', pady=(6, 0))

    def _on_section_toggle(self, ui_key: str, is_open: bool) -> None:
        """Persist a collapsible section's open/closed state immediately"""
        self.config.set('ui', ui_key, bool(is_open))
        self.config.save()

    def _enforce_delegate_choice(self, just_set: str) -> None:
        """Force CPU and Force GPU are mutually exclusive - clear the other one"""
        if just_set == 'cpu' and self.var_force_cpu.get():
            self.var_force_gpu.set(False)
        elif just_set == 'gpu' and self.var_force_gpu.get():
            self.var_force_cpu.set(False)

    def _build_buttons(self, parent: ttk.Frame, row: int) -> None:
        """Save Config and the Start/Stop toggle"""
        bar = ttk.Frame(parent)
        bar.grid(row=row, column=0, sticky='ew', pady=(0, 8))
        bar.columnconfigure(2, weight=1)

        self.btn_start = ttk.Button(bar, text="Start", width=12, style='Accent.TButton',
                                    command=self._toggle_engine)
        self.btn_start.grid(row=0, column=0, sticky='w')

        self.btn_save = ttk.Button(bar, text="Save Config", command=self._save_config)
        self.btn_save.grid(row=0, column=1, sticky='w', padx=(8, 0))

        self.btn_clear = ttk.Button(bar, text="Clear Log", command=self._clear_log)
        self.btn_clear.grid(row=0, column=3, sticky='e')

    def _build_log(self, parent: ttk.Frame, row: int) -> None:
        """Read-only stdout pane with a scrollbar, in a collapsible section"""
        section = theme.CollapsibleSection(
            parent, title="Engine Output", fill=True,
            open=bool(self.config.get('ui', 'log_section_open', True)),
            on_toggle=self._on_log_toggle)
        section.grid(row=row, column=0, sticky='nsew')
        frame = section.body
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.log = tk.Text(frame, height=14, wrap='none', state='disabled',
                           borderwidth=0, highlightthickness=0,
                           font=tkfont.nametofont('TkFixedFont'))
        theme.style_text_widget(self.log)
        self.log.grid(row=0, column=0, sticky='nsew')

        self.log.tag_configure('warn', foreground=theme.PALETTE['warn'])
        self.log.tag_configure('error', foreground=theme.PALETTE['error'])

        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.log.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.log.configure(yscrollcommand=scroll.set)

    def _on_log_toggle(self, is_open: bool) -> None:
        """The log row only absorbs extra height while the section is open"""
        self._outer.rowconfigure(4, weight=1 if is_open else 0)
        self._on_section_toggle('log_section_open', is_open)

    def _build_status(self, parent: ttk.Frame, row: int) -> None:
        """Single-line status label"""
        label = ttk.Label(parent, textvariable=self.var_status, anchor='w')
        label.grid(row=row, column=0, sticky='ew', pady=(6, 0))

    # ------------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------------
    def _build_menubar(self) -> None:
        """Build the macOS menu bar: App, File, Engine, Help

        root.config(menu=...) does not consume a grid row, so this needs no
        layout change. The App menu (name='apple') and Help menu (name='help')
        are macOS's special menu names - see _wire_app_menu for why the
        About/Settings items are never added here directly.
        """
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        self._wire_app_menu(menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Save Config", accelerator="Command-S",
                              command=self._save_config)
        file_menu.add_command(label="Open config.json", command=self._open_config_file)
        file_menu.add_command(label="Reveal Config in Finder", accelerator="Shift-Command-R",
                              command=self._reveal_config)
        menubar.add_cascade(label="File", menu=file_menu)

        engine_menu = tk.Menu(menubar, tearoff=0)
        engine_menu.add_command(label="Start", accelerator="Command-R", command=self._menu_start)
        self.MENU_START = engine_menu.index('end')
        engine_menu.add_command(label="Stop", accelerator="Command-.", command=self._menu_stop)
        self.MENU_STOP = engine_menu.index('end')
        engine_menu.add_separator()
        engine_menu.add_command(label="Clear Log", accelerator="Command-K", command=self._clear_log)
        menubar.add_cascade(label="Engine", menu=engine_menu)
        self.menu_engine = engine_menu

        help_menu = tk.Menu(menubar, name='help')
        menubar.add_cascade(label="Help", menu=help_menu)
        # Check for Updates lives here rather than the App menu -
        # _wire_app_menu deliberately adds no items to name='apple'.
        # manual=True: a menu click must bypass the 24h throttle and always
        # report a result (tk would otherwise call this with no args and get
        # the silent launch-check behavior).
        help_menu.add_command(label="Check for Updates…",
                              command=lambda: self._check_for_updates(manual=True))
        help_menu.add_separator()
        # No "MP-OSC Help" add_command here: registering tk::mac::ShowHelp in
        # _wire_app_menu already makes Aqua Tk auto-insert that exact item
        # (with the standard Command-? accelerator) into this name='help'
        # menu. Adding one by hand duplicated it - two "MP-OSC Help" entries
        # doing the same thing.
        help_menu.add_command(label="Open Full Documentation in Browser",
                              command=self._open_full_docs)
        help_menu.add_command(label="Project on GitHub", command=self._open_github)

        self._bind_menu_accelerators()
        self._sync_menu_state()

    def _wire_app_menu(self, menubar: tk.Menu) -> None:
        """Attach the special name='apple' menu, with no custom items on it

        macOS builds the App menu's content itself. Defining these Tcl
        commands makes macOS ADD the corresponding items in the correct
        position (About, Settings, Quit) - adding custom items directly to
        this menu would duplicate what macOS already puts there. The cascade
        still has to be created and attached, though: once root.config(menu=)
        replaces the default menu, nothing shows here without it.
        """
        app_menu = tk.Menu(menubar, name='apple')
        menubar.add_cascade(menu=app_menu)

        self.root.createcommand('tkAboutDialog', self._show_about)
        self.root.createcommand('tk::mac::ShowPreferences', self._show_preferences)
        self.root.createcommand('tk::mac::ShowHelp', lambda: self._show_help())
        self.root.createcommand('tk::mac::Quit', self._on_close)

    def _bind_menu_accelerators(self) -> None:
        """tkinter does not auto-bind accelerator= labels - wire each by hand"""
        bindings = (
            ('<Command-s>', lambda e=None: self._save_config()),
            ('<Command-r>', lambda e=None: self._menu_start()),
            ('<Command-period>', lambda e=None: self._menu_stop()),
            ('<Command-k>', lambda e=None: self._clear_log()),
            ('<Command-Shift-R>', lambda e=None: self._reveal_config()),
        )
        for keysym, handler in bindings:
            self.root.bind_all(keysym, handler)

    def _sync_menu_state(self) -> None:
        """Match the Engine menu to the engine's state

        Menu items aren't tracked by _register/_set_form_enabled (that
        machinery only understands widgets), so this is the parallel path -
        called from the same handful of places _set_form_enabled already is.
        """
        running = self.is_running()
        stopping = self.stop_requested_at is not None
        try:
            self.menu_engine.entryconfigure(
                self.MENU_START, state='disabled' if (running or stopping) else 'normal')
            self.menu_engine.entryconfigure(
                self.MENU_STOP, state='normal' if (running and not stopping) else 'disabled')
        except tk.TclError:
            pass

    def _menu_start(self) -> None:
        """Engine > Start / the Command-R accelerator"""
        if not self.is_running():
            self._start_engine()

    def _menu_stop(self) -> None:
        """Engine > Stop / the Command-. accelerator"""
        if self.is_running():
            self._stop_engine()

    def _open_config_file(self) -> None:
        """Open config.json in the default text editor"""
        path = os.path.abspath(self.config.config_file)
        try:
            subprocess.Popen(['/usr/bin/open', '-t', path])
        except Exception as e:
            self._append_log("⚠️  Failed to open config.json: {}".format(e))

    def _reveal_config(self) -> None:
        """Reveal config.json in Finder, or its containing folder if unsaved"""
        path = os.path.abspath(self.config.config_file)
        target = path if os.path.exists(path) else os.path.dirname(path)
        if target != path:
            self._set_status("⚠️  Config not saved yet - showing the folder")
        try:
            subprocess.Popen(['/usr/bin/open', '-R', target])
        except Exception as e:
            self._append_log("⚠️  Failed to reveal config: {}".format(e))

    def _show_about(self) -> None:
        """App > About MP-OSC"""
        version = docs.app_version()
        title = "MP-OSC {}".format(version).strip() if version else "MP-OSC"
        messagebox.showinfo(
            parent=self.root,
            title="About MP-OSC",
            message=title,
            detail="MediaPipe pose and hand tracking, streamed over OSC.\n\n"
                   "https://github.com/Noah-Hardy/mp-osc",
        )

    def _show_preferences(self) -> None:
        """App > Settings… (⌘,) - opens the real Settings window, or brings it forward"""
        if self.settings_window is not None and self.settings_window.exists():
            self.settings_window.show()
            return
        self.settings_window = SettingsWindow(
            self.root, self.config,
            var_force_cpu=self.var_force_cpu,
            var_force_gpu=self.var_force_gpu,
            var_force_legacy=self.var_force_legacy,
            var_no_holistic=self.var_no_holistic,
            var_show_fps=self.var_show_fps,
            on_open_config=self._open_config_file,
            on_reveal_config=self._reveal_config,
            on_check_now=lambda: self._check_for_updates(manual=True),
            on_close=self._forget_settings,
            on_saved=self._reload_form_from_config,
        )

    def _forget_settings(self) -> None:
        """Drop the reference once the Settings window closes"""
        self.settings_window = None

    def _show_help(self, slug: str = None) -> None:
        """Open the docs viewer, or bring the existing one forward"""
        if self.help_window is not None and self.help_window.exists():
            self.help_window.show(slug)
            return
        self.help_window = HelpWindow(self.root, on_close=self._forget_help)
        if slug is not None:
            self.help_window.show(slug)

    def _forget_help(self) -> None:
        """Drop the reference once the viewer closes"""
        self.help_window = None

    def _open_full_docs(self) -> None:
        """Help > Open Full Documentation in Browser"""
        try:
            path = docs.open_site()
            self._set_status("📖 Opened documentation in browser")
            self._append_log("📖 Documentation written to {}".format(path))
        except Exception as e:
            self._append_log("⚠️  Failed to open documentation: {}".format(e))

    def _open_github(self) -> None:
        """Help > Project on GitHub"""
        try:
            webbrowser.open('https://github.com/Noah-Hardy/mp-osc')
        except Exception as e:
            self._append_log("⚠️  Failed to open GitHub: {}".format(e))

    # ------------------------------------------------------------------------
    # Update checking - src.updater.UpdateController does the network/
    # filesystem work on a worker thread; every method here runs on the main
    # thread, driven from _poll() via TAG_UPDATE payloads.
    # ------------------------------------------------------------------------
    def _check_for_updates(self, manual: bool = False) -> None:
        """Help > Check for Updates…, Settings' Check Now, and the silent launch check"""
        if self.updater.busy:
            if manual:
                self._set_status("🔄 Already checking for updates…")
            return
        if manual:
            self._set_status("🔄 Checking for updates…")
        self.updater.check_async(manual=manual)

    def _handle_update(self, payload: dict) -> None:
        """Dispatch one UpdateController payload (from _poll, main thread only)"""
        persist = payload.get('persist')
        if persist:
            for key, value in persist.items():
                self.config.set('updates', key, value)
            self.config.save()

        kind = payload.get('kind')

        if kind == 'none':
            if payload.get('manual'):
                self._set_status(payload.get('message') or "✅ You're up to date")
            return

        if kind == 'error':
            message = payload.get('message') or "Couldn't check for updates."
            if payload.get('manual'):
                messagebox.showinfo("Check for Updates", message, parent=self.root)
            self._set_status("⚠️  {}".format(message))
            return

        if kind == 'available':
            release = payload.get('release')
            self._set_status("⬆️  MP-OSC {} is available".format(release.version))
            self._open_update_dialog(release)
            return

        # Everything else is an install-flow update for whichever dialog is open.
        if self.update_dialog is None or not self.update_dialog.exists():
            return
        if kind == 'progress':
            self.update_dialog.show_progress(payload.get('done', 0), payload.get('total', 0))
        elif kind == 'verifying':
            self.update_dialog.show_verifying(payload.get('phase', ''))
        elif kind == 'ready':
            self.update_dialog.show_ready()
            self._install_ready(payload.get('staged'))
        elif kind == 'failed':
            self.update_dialog.show_failed(payload.get('message', ''))

    def _open_update_dialog(self, release) -> None:
        if self.update_dialog is not None and self.update_dialog.exists():
            self.update_dialog.lift()
            return
        self.update_dialog = UpdateDialog(
            self.root, release,
            on_install=lambda: self._start_update_install(release),
            on_skip=lambda: self._skip_update(release),
            on_later=self._forget_update_dialog,
            on_cancel=self._cancel_update_install,
            on_close=self._forget_update_dialog,
        )

    def _forget_update_dialog(self) -> None:
        if self.update_dialog is not None:
            self.update_dialog.destroy()
        self.update_dialog = None

    def _skip_update(self, release) -> None:
        self.config.set('updates', 'skipped_version', release.tag)
        self.config.save()
        self._forget_update_dialog()

    def _start_update_install(self, release) -> None:
        if self.is_running():
            if not messagebox.askyesno(
                    "Stop Engine and Install?",
                    "MP-OSC needs to stop the tracking engine to install this update. "
                    "Stop it now?",
                    parent=self.root):
                return
        self.updater.install_async(release)

    def _cancel_update_install(self) -> None:
        self.updater.cancel()

    def _install_ready(self, staged) -> None:
        """Update verified and staged - stop the engine, hand off, then quit

        The swap script waits for THIS process to exit before moving the new
        bundle into place, so spawning it has to be the last thing we do.
        """
        if self.is_running():
            self._stop_engine()
        self._await_install_shutdown(staged, time.monotonic())

    def _await_install_shutdown(self, staged, started_at: float) -> None:
        if self.proc is not None and self.proc.poll() is None:
            if time.monotonic() - started_at <= CLOSE_GRACE:
                self.root.after(100, lambda: self._await_install_shutdown(staged, started_at))
                return
            try:
                self.proc.kill()
            except Exception:
                pass

        try:
            spawn_installer(staged)
        except Exception as e:
            self._append_log("⚠️  Failed to launch the installer: {}".format(e))
            if self.update_dialog is not None and self.update_dialog.exists():
                self.update_dialog.show_failed("Couldn't launch the installer: {}".format(e))
            return

        self.root.destroy()

    # ------------------------------------------------------------------------
    # Widget enable/disable helpers
    # ------------------------------------------------------------------------
    def _register(self, widget: Any, enabled_state: str) -> None:
        """Track a form widget so it can be disabled while the engine runs"""
        self._form_widgets.append((widget, enabled_state))

    def _set_form_enabled(self, enabled: bool) -> None:
        """Enable or disable every engine-affecting form widget"""
        for widget, enabled_state in self._form_widgets:
            try:
                widget.configure(state=(enabled_state if enabled else 'disabled'))
            except tk.TclError:
                pass
        if enabled:
            self._update_source_state()
        self._sync_menu_state()

    def _update_source_state(self) -> None:
        """Grey out the input widgets that do not apply to the chosen source"""
        if self.is_running():
            return

        use_ndi = self.var_source.get() == 'ndi'

        try:
            self.spin_camera.configure(state='disabled' if use_ndi else 'normal')
        except tk.TclError:
            pass

        if NDI_AVAILABLE:
            state = 'normal' if use_ndi else 'disabled'
            try:
                self.combo_ndi.configure(state=state)
                self.btn_refresh.configure(state=state)
            except tk.TclError:
                pass
        else:
            try:
                self.radio_ndi.configure(state='disabled')
                self.combo_ndi.configure(state='disabled')
                self.btn_refresh.configure(state='disabled')
            except tk.TclError:
                pass

    # ------------------------------------------------------------------------
    # Log pane
    # ------------------------------------------------------------------------
    def _append_log(self, text: str) -> None:
        """Append text to the read-only log pane, trimming old lines"""
        if not text.endswith('\n'):
            text += '\n'
        tag = ()
        if text.startswith(('❌', '🛑')):
            tag = ('error',)
        elif text.startswith('⚠️'):
            tag = ('warn',)
        self.log.configure(state='normal')
        self.log.insert('end', text, tag)

        # Cap the buffer so long sessions do not grow without bound
        line_count = int(self.log.index('end-1c').split('.')[0])
        if line_count > MAX_LOG_LINES:
            self.log.delete('1.0', '{}.0'.format(line_count - MAX_LOG_LINES))

        self.log.see('end')
        self.log.configure(state='disabled')

    def _clear_log(self) -> None:
        """Empty the log pane"""
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def _set_status(self, message: str) -> None:
        """Update the status line"""
        self.var_status.set(message)

    # ------------------------------------------------------------------------
    # Command construction
    # ------------------------------------------------------------------------
    def _int_or_none(self, raw: str) -> Optional[int]:
        """Parse an int from a form field, returning None when blank/invalid"""
        raw = (raw or '').strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def _build_command(self) -> list:
        """
        Translate the form into an argv list for the engine subprocess

        Returns:
            List of command line tokens
        """
        if getattr(sys, 'frozen', False):
            base = [sys.executable]
        else:
            base = [sys.executable, os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'main.py')]

        cmd = list(base)
        cmd.append(self.var_mode.get())

        # OSC output
        host = self.var_host.get().strip()
        if host:
            cmd += ['--host', host]
        port = self._int_or_none(self.var_port.get())
        if port is not None:
            cmd += ['--port', str(port)]

        # Input source (camera and NDI are mutually exclusive)
        if self.var_source.get() == 'ndi':
            cmd.append('--ndi')
            ndi_source = self.var_ndi_source.get().strip()
            if ndi_source:
                cmd += ['--ndi-source', ndi_source]
        else:
            camera = self._int_or_none(self.var_camera.get())
            if camera is not None:
                cmd += ['--camera', str(camera)]

        # Model and performance
        cmd += ['--pose-model', self.var_pose_model.get()]
        fps_cap = self._int_or_none(self.var_fps_cap.get())
        if fps_cap is not None and fps_cap > 0:
            cmd += ['--fps-cap', str(fps_cap)]
        if self.var_show_fps.get():
            cmd.append('--fps')

        # Always explicit: the checkbox, not the saved config, decides
        cmd.append('--mirror' if self.var_mirror.get() else '--no-mirror')

        # Backend toggles (mutually exclusive - enforced by _enforce_delegate_choice)
        if self.var_force_cpu.get():
            cmd.append('--force-cpu')
        elif self.var_force_gpu.get():
            cmd.append('--force-gpu')
        if self.var_force_legacy.get():
            cmd.append('--force-legacy')
        if self.var_no_holistic.get():
            cmd.append('--no-holistic')

        return cmd

    # ------------------------------------------------------------------------
    # Engine lifecycle
    # ------------------------------------------------------------------------
    def is_running(self) -> bool:
        """True while the engine subprocess is alive"""
        return self.proc is not None and self.proc.poll() is None

    def _toggle_engine(self) -> None:
        """Start the engine, or request a stop if it is already running"""
        if self.is_running():
            self._stop_engine()
        else:
            self._start_engine()

    def _start_engine(self) -> None:
        """Spawn the engine subprocess and begin streaming its output"""
        if self.is_running():
            # Unreachable via btn_start (it toggles), but the menu/⌘R
            # accelerator can invoke this directly - guard against a double
            # invocation orphaning the first child process.
            return

        # Catch the obviously malformed cases before wasting a subprocess
        # spawn. A genuinely bad hostname still reaches the engine, which
        # now reports socket.gaierror cleanly instead of a raw traceback
        # (see main.py's OSC client construction).
        if not self.var_host.get().strip():
            self._set_status("❌ OSC host is required")
            self._append_log("❌ OSC host is required - engine not started")
            return

        port = self._int_or_none(self.var_port.get())
        if port is None or not valid_port(port):
            self._set_status("❌ Invalid OSC port (must be 0-65535)")
            self._append_log("❌ Invalid OSC port - engine not started")
            return

        cmd = self._build_command()

        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        # Tells the engine to run as a macOS accessory app so its preview
        # window doesn't add a second Dock icon (see src.macos_app).
        env['MPOSC_LAUNCHED_FROM_GUI'] = '1'

        # From source, run the child in the repo root so it resolves the same
        # relative config.json the GUI just wrote. Frozen builds use an absolute
        # config path (see src.config.default_config_path), so the working
        # directory is irrelevant there and must not point inside the bundle.
        cwd = None
        if not getattr(sys, 'frozen', False):
            cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        self._append_log("🚀 Launching: {}".format(' '.join(cmd)))

        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                cwd=cwd
            )
        except Exception as e:
            self.proc = None
            self._append_log("❌ Failed to start engine: {}".format(e))
            self._set_status("❌ Failed to start engine")
            return

        self.stop_requested_at = None
        self.terminated = False
        self.killed = False

        self.reader = threading.Thread(target=self._read_output, args=(self.proc,), daemon=True)
        self.reader.start()

        self.btn_start.configure(text="Stop", style='Error.TButton')
        self._set_form_enabled(False)
        self._starting = True
        self._start_spinner()

    # ------------------------------------------------------------------------
    # Startup spinner - animates the status line until the engine's ready
    # sentinel (printed by main.py right before its processing loop) arrives.
    # ------------------------------------------------------------------------
    def _start_spinner(self) -> None:
        self._stop_spinner_job()
        self._spinner_idx = 0
        self._tick_spinner()

    def _tick_spinner(self) -> None:
        frame = SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]
        self._spinner_idx += 1
        self.var_status.set("{} Starting engine…".format(frame))
        self._spinner_job = self.root.after(SPINNER_INTERVAL_MS, self._tick_spinner)

    def _stop_spinner_job(self) -> None:
        if self._spinner_job is not None:
            try:
                self.root.after_cancel(self._spinner_job)
            except tk.TclError:
                pass
            self._spinner_job = None

    def _stop_spinner(self) -> None:
        self._stop_spinner_job()
        self._starting = False

    def _engine_became_ready(self) -> None:
        self._stop_spinner()
        if self.is_running():
            self._set_status("🎥 Engine launched (PID {})".format(self.proc.pid))

    def _read_output(self, proc: subprocess.Popen) -> None:
        """Worker thread: pump child stdout into the queue (no tk access here)"""
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    self._queue.put((TAG_LOG, line.rstrip('\n')))
        except Exception as e:
            self._queue.put((TAG_LOG, "⚠️  Output reader stopped: {}".format(e)))

    def _stop_engine(self) -> None:
        """Send SIGINT so the child runs its normal cleanup path"""
        if not self.is_running():
            return
        try:
            self.proc.send_signal(signal.SIGINT)
        except Exception as e:
            self._append_log("⚠️  Failed to signal engine: {}".format(e))
        self.stop_requested_at = time.monotonic()
        self._stop_spinner()
        self.btn_start.configure(text="Stopping…", state='disabled')
        self._set_status("🛑 Stopping engine…")
        self._sync_menu_state()

    def _escalate_stop(self) -> None:
        """Escalate SIGINT to SIGTERM then SIGKILL if the child hangs"""
        if self.stop_requested_at is None or not self.is_running():
            return
        elapsed = time.monotonic() - self.stop_requested_at

        if elapsed > KILL_AFTER and not self.killed:
            self.killed = True
            self._append_log("⚠️  Engine unresponsive - sending SIGKILL")
            try:
                self.proc.kill()
            except Exception:
                pass
        elif elapsed > TERMINATE_AFTER and not self.terminated:
            self.terminated = True
            self._append_log("⚠️  Engine still running - sending SIGTERM")
            try:
                self.proc.terminate()
            except Exception:
                pass

    def _on_engine_exit(self, returncode: int) -> None:
        """Reset the UI once the child has exited"""
        self._stop_spinner()
        if returncode == 0:
            self._append_log("✅ Engine exited (code 0)")
            self._set_status("✅ Engine stopped")
        elif returncode < 0:
            # Popen.poll() reports a negative code when the child was killed
            # by a signal (SIGTERM -> -15, SIGKILL -> -9). If we're the ones
            # who escalated the Stop request to that signal, say so instead
            # of surfacing a bare negative number - self.terminated/killed
            # are still set here, cleared just below.
            if self.terminated or self.killed:
                message = "⚠️  Engine had to be force-stopped"
            else:
                message = "⚠️  Engine terminated unexpectedly (signal {})".format(-returncode)
            self._append_log(message)
            self._set_status(message)
        else:
            message = ENGINE_EXIT_MESSAGES.get(returncode)
            if message is None:
                message = "⚠️  Engine exited with code {}".format(returncode)
            self._append_log(message)
            self._set_status(message)

        self.proc = None
        self.reader = None
        self.stop_requested_at = None
        self.terminated = False
        self.killed = False

        self.btn_start.configure(text="Start", style='Accent.TButton', state='normal')
        self._set_form_enabled(True)

    # ------------------------------------------------------------------------
    # Queue polling (single point where tk widgets are touched)
    # ------------------------------------------------------------------------
    def _poll(self) -> None:
        """Drain the worker queue and watch the child process"""
        try:
            while True:
                tag, payload = self._queue.get_nowait()
                if tag == TAG_LOG:
                    if self._starting and payload.startswith(ENGINE_READY_PREFIX):
                        self._engine_became_ready()
                    self._append_log(payload)
                elif tag == TAG_NDI:
                    self._apply_ndi_sources(payload)
                elif tag == TAG_UPDATE:
                    self._handle_update(payload)
        except queue.Empty:
            pass

        if self.proc is not None:
            returncode = self.proc.poll()
            if returncode is None:
                self._escalate_stop()
            else:
                # Let the reader thread flush the tail of stdout first
                if self.reader is not None and self.reader.is_alive():
                    self.reader.join(0.05)
                self._on_engine_exit(returncode)

        self.root.after(POLL_INTERVAL_MS, self._poll)

    # ------------------------------------------------------------------------
    # NDI source discovery
    # ------------------------------------------------------------------------
    def _refresh_ndi_sources(self) -> None:
        """Kick off a background NDI discovery scan"""
        if not NDI_AVAILABLE:
            self._set_status("⚠️  NDI library not available")
            return

        try:
            self.btn_refresh.configure(state='disabled')
        except tk.TclError:
            pass
        self._set_status("🔍 Searching for NDI sources…")

        worker = threading.Thread(target=self._scan_ndi_sources, daemon=True)
        worker.start()

    def _scan_ndi_sources(self) -> None:
        """Worker thread: block on NDI discovery, hand results to the queue"""
        try:
            sources = list_ndi_sources()
        except Exception as e:
            self._queue.put((TAG_LOG, "❌ NDI discovery failed: {}".format(e)))
            sources = []
        self._queue.put((TAG_NDI, sources))

    def _apply_ndi_sources(self, sources: List[str]) -> None:
        """Main thread: push discovered NDI sources into the combobox"""
        self.combo_ndi.configure(values=list(sources))

        if sources:
            self._set_status("✅ Found {} NDI source(s)".format(len(sources)))
            self._append_log("🎥 NDI sources: {}".format(', '.join(sources)))
            if not self.var_ndi_source.get().strip():
                self.var_ndi_source.set(sources[0])
        else:
            self._set_status("⚠️  No NDI sources found on the network")
            self._append_log("⚠️  No NDI sources found on the network")

        if not self.is_running() and self.var_source.get() == 'ndi':
            try:
                self.btn_refresh.configure(state='normal')
            except tk.TclError:
                pass

    # ------------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------------
    def _save_config(self) -> None:
        """
        Write the form fields that have config keys back to config.json
        Config.set is runtime-only, so save() is what persists them
        """
        try:
            port = self._int_or_none(self.var_port.get())
            if port is None or not valid_port(port):
                self._set_status("❌ Invalid OSC port (must be 0-65535)")
                self._append_log("❌ Invalid OSC port - config not saved")
                return

            camera = self._int_or_none(self.var_camera.get())
            if camera is None:
                self._set_status("❌ Invalid camera device ID")
                self._append_log("❌ Invalid camera device ID - config not saved")
                return

            fps_cap = self._int_or_none(self.var_fps_cap.get())
            if fps_cap is None:
                fps_cap = 0  # blank means uncapped

            ndi_source = self.var_ndi_source.get().strip()

            self.config.set('osc', 'host', self.var_host.get().strip())
            self.config.set('osc', 'port', port)
            self.config.set('camera', 'device_id', camera)
            self.config.set('camera', 'use_ndi', self.var_source.get() == 'ndi')
            self.config.set('camera', 'ndi_source', ndi_source)
            self.config.set('mediapipe', 'pose_model_type', self.var_pose_model.get())
            self.config.set('performance', 'target_fps', fps_cap)
            self.config.set('performance', 'show_fps', bool(self.var_show_fps.get()))
            self.config.set('performance', 'force_cpu', bool(self.var_force_cpu.get()))
            self.config.set('performance', 'force_gpu', bool(self.var_force_gpu.get()))
            self.config.set('performance', 'force_legacy', bool(self.var_force_legacy.get()))
            self.config.set('performance', 'no_holistic', bool(self.var_no_holistic.get()))
            self.config.set('display', 'mirror_preview', bool(self.var_mirror.get()))

            self.config.save()
        except Exception as e:
            self._set_status("❌ Failed to save config: {}".format(e))
            self._append_log("❌ Failed to save config: {}".format(e))
            return

        self._set_status("💾 Configuration saved to {}".format(self.config.config_file))
        self._append_log("💾 Configuration saved to {}".format(self.config.config_file))
        self._append_log("   (tracking mode is launch-only and is not stored - "
                         "see mp-osc → Settings… for everything else)")

    # ------------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------------
    def _on_close(self) -> None:
        """Ask the engine to stop, then close once it is gone"""
        if self.is_running():
            self._stop_engine()
            self._await_shutdown(time.monotonic())
        else:
            self.root.destroy()

    def _await_shutdown(self, started_at: float) -> None:
        """Non-blocking retry loop that destroys the window once the child exits"""
        if self.proc is None or self.proc.poll() is not None:
            self.root.destroy()
            return

        if time.monotonic() - started_at > CLOSE_GRACE:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.root.destroy()
            return

        self.root.after(100, lambda: self._await_shutdown(started_at))


# ============================================================================
# ENTRY POINT
# ============================================================================
def run_gui() -> None:
    """Launch the settings window"""
    root = tk.Tk()
    LauncherGui(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
