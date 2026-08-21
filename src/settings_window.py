#!/usr/bin/env python3
"""
Settings Window Module

The tabbed preferences window reachable from mp-osc -> Settings... (Command-,)
and from the Help/File menus' existing config shortcuts. Everything the
launcher's collapsible "Model & Performance" section doesn't expose day to
day lives here instead: tracking thresholds, preview styling, performance
tuning, and the update checker.

Modeled on src.help_window's Toplevel lifecycle (exists/show/destroy,
on_close callback) so src.gui can hold it as a singleton the same way.
"""

# ============================================================================
# IMPORTS
# ============================================================================
import time
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk
from typing import Callable, Optional

from src import theme
from src.config import Config, valid_unit_float

FORCE_GPU_WARNING = "Force GPU has a known memory leak on Apple Silicon - use with caution."


# ============================================================================
# SETTINGS WINDOW
# ============================================================================
class SettingsWindow:
    """
    Non-modal Toplevel with a General / Tracking / Preview / Advanced
    notebook. Every field is bound to a real config.json key and only
    written back on Save; Restore Defaults resets the form (not the file)
    until Save is pressed.
    """

    def __init__(self, parent: tk.Misc, config: Config, *,
                var_force_cpu: tk.BooleanVar,
                var_force_gpu: tk.BooleanVar,
                var_force_legacy: tk.BooleanVar,
                var_no_holistic: tk.BooleanVar,
                var_show_fps: tk.BooleanVar,
                on_open_config: Callable[[], None],
                on_reveal_config: Callable[[], None],
                on_check_now: Callable[[], None],
                on_close: Optional[Callable[[], None]] = None,
                on_saved: Optional[Callable[[], None]] = None) -> None:
        self.config = config
        self._on_open_config = on_open_config
        self._on_reveal_config = on_reveal_config
        self._on_check_now = on_check_now
        self._on_close = on_close
        self._on_saved = on_saved

        # Shared with LauncherGui - these feed straight into the argv the
        # launcher builds (src.gui._build_command), so Settings binds the
        # same variables rather than owning private copies that could drift.
        self.var_force_cpu = var_force_cpu
        self.var_force_gpu = var_force_gpu
        self.var_force_legacy = var_force_legacy
        self.var_no_holistic = var_no_holistic
        self.var_show_fps = var_show_fps

        self.top = tk.Toplevel(parent)
        self.top.title("MP-OSC Settings")
        self.top.configure(bg=theme.PALETTE['bg'])
        self.top.protocol("WM_DELETE_WINDOW", self.destroy)
        self.top.bind('<Escape>', lambda e: self.destroy())
        self.top.bind('<Command-w>', lambda e: self.destroy())

        self._fields = []  # list of (section, key, var, kind) for save/restore
        self._int_minimums = {}  # (section, key) -> lowest int _save accepts
        self._build_layout()
        self._load_from_config()
        self._size_to_content()

    def _size_to_content(self) -> None:
        """Open at the notebook's requested size so no tab is ever clipped

        The Notebook requests the size of its largest tab, so the natural
        size already fits Advanced (the tallest one). Only the height is
        clamped, for very small screens; the window stays user-resizable.
        """
        self.top.update_idletasks()
        req_w = self.top.winfo_reqwidth()
        req_h = self.top.winfo_reqheight()
        max_h = int(self.top.winfo_screenheight() * 0.85)
        self.top.minsize(min(req_w, 640), min(req_h, 460))
        if req_h > max_h:
            self.top.geometry(f"{req_w}x{max_h}")

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------
    def exists(self) -> bool:
        return self.top is not None and self.top.winfo_exists()

    def show(self) -> None:
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()

    def destroy(self) -> None:
        if self.top is None:
            return
        top, self.top = self.top, None
        top.destroy()
        if self._on_close is not None:
            self._on_close()

    # ------------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------------
    def _build_layout(self) -> None:
        outer = ttk.Frame(self.top, padding=10)
        outer.grid(row=0, column=0, sticky='nsew')
        self.top.rowconfigure(0, weight=1)
        self.top.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        notebook = ttk.Notebook(outer)
        notebook.grid(row=0, column=0, sticky='nsew')

        self._build_general_tab(notebook)
        self._build_tracking_tab(notebook)
        self._build_preview_tab(notebook)
        self._build_advanced_tab(notebook)

        buttons = ttk.Frame(outer)
        buttons.grid(row=1, column=0, sticky='ew', pady=(10, 0))
        buttons.columnconfigure(1, weight=1)

        ttk.Button(buttons, text="Restore Defaults", command=self._restore_defaults).grid(
            row=0, column=0, sticky='w')
        ttk.Button(buttons, text="Close", command=self.destroy).grid(row=0, column=2, sticky='e')
        ttk.Button(buttons, text="Save", style='Accent.TButton', command=self._save).grid(
            row=0, column=3, sticky='e', padx=(8, 0))

    def _tab(self, notebook: ttk.Notebook, title: str) -> ttk.Frame:
        frame = ttk.Frame(notebook, padding=12)
        frame.columnconfigure(1, weight=1)
        notebook.add(frame, text=title)
        return frame

    # ------------------------------------------------------------------------
    # Field helpers - each returns the row index the caller should use next
    # ------------------------------------------------------------------------
    def _bind(self, section: str, key: str, var, kind: str) -> None:
        self._fields.append((section, key, var, kind))

    def _row_check(self, parent, row, label, section, key, note=None) -> int:
        var = tk.BooleanVar()
        ttk.Checkbutton(parent, text=label, variable=var).grid(
            row=row, column=0, columnspan=2, sticky='w', pady=2)
        if note:
            ttk.Label(parent, text=note, style='Dim.TLabel').grid(
                row=row + 1, column=0, columnspan=2, sticky='w', padx=(20, 0))
            row += 1
        self._bind(section, key, var, 'bool')
        return row + 1

    def _row_entry(self, parent, row, label, section, key, width=12, note=None) -> int:
        var = tk.StringVar()
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', pady=2)
        ttk.Entry(parent, textvariable=var, width=width).grid(
            row=row, column=1, sticky='w', padx=(6, 0), pady=2)
        if note:
            ttk.Label(parent, text=note, style='Dim.TLabel').grid(
                row=row, column=2, sticky='w', padx=(8, 0))
        self._bind(section, key, var, 'str')
        return row + 1

    def _row_int(self, parent, row, label, section, key, width=8, note=None, minimum=0) -> int:
        var = tk.StringVar()
        if minimum > 0:
            self._int_minimums[(section, key)] = minimum
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', pady=2)
        ttk.Spinbox(parent, textvariable=var, from_=minimum, to=999999, width=width).grid(
            row=row, column=1, sticky='w', padx=(6, 0), pady=2)
        if note:
            ttk.Label(parent, text=note, style='Dim.TLabel').grid(
                row=row, column=2, sticky='w', padx=(8, 0))
        self._bind(section, key, var, 'int')
        return row + 1

    def _row_float(self, parent, row, label, section, key, width=8) -> int:
        var = tk.StringVar()
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', pady=2)
        ttk.Spinbox(parent, textvariable=var, from_=0.0, to=1.0, increment=0.05, width=width).grid(
            row=row, column=1, sticky='w', padx=(6, 0), pady=2)
        self._bind(section, key, var, 'float')
        return row + 1

    def _row_combo(self, parent, row, label, section, key, values, width=12) -> int:
        var = tk.StringVar()
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', pady=2)
        ttk.Combobox(parent, textvariable=var, values=list(values), state='readonly',
                    width=width).grid(row=row, column=1, sticky='w', padx=(6, 0), pady=2)
        self._bind(section, key, var, 'str')
        return row + 1

    def _row_color(self, parent, row, label, section, key) -> int:
        """A [B,G,R] color list (as used by cv2 drawing) edited via a swatch button.

        Stored order matches what pose_processor/hand_processor already pass
        straight to cv2 draw calls - the swatch preview approximates the
        on-screen color rather than round-tripping it exactly.
        """
        var = tk.StringVar()  # holds "#rrggbb" for the swatch itself
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', pady=2)
        swatch = tk.Label(parent, width=6, relief='flat', cursor='pointinghand')
        swatch.grid(row=row, column=1, sticky='w', padx=(6, 0), pady=2)

        def pick():
            initial = var.get() or '#808080'
            result = colorchooser.askcolor(color=initial, parent=self.top, title=label)
            if result and result[1]:
                var.set(result[1])
                swatch.configure(bg=result[1])

        swatch.bind('<Button-1>', lambda e: pick())
        self._bind(section, key, var, 'color')
        return row + 1

    # ------------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------------
    def _build_general_tab(self, notebook: ttk.Notebook) -> None:
        frame = self._tab(notebook, "General")
        row = 0

        row = self._row_check(frame, row, "Check for updates on launch",
                              'updates', 'check_on_launch')
        row = self._row_check(frame, row, "Include pre-release builds",
                              'updates', 'include_prereleases',
                              note="Pre-releases show up before a stable build exists.")

        self.last_check_var = tk.StringVar(value='')
        ttk.Label(frame, textvariable=self.last_check_var, style='Dim.TLabel').grid(
            row=row, column=0, columnspan=2, sticky='w', pady=(4, 2))
        row += 1
        ttk.Button(frame, text="Check Now", command=self._check_now).grid(
            row=row, column=0, sticky='w', pady=(0, 12))
        row += 1

        ttk.Separator(frame, orient='horizontal').grid(
            row=row, column=0, columnspan=3, sticky='ew', pady=8)
        row += 1

        ttk.Button(frame, text="Open config.json", command=self._on_open_config).grid(
            row=row, column=0, sticky='w', pady=2)
        row += 1
        ttk.Button(frame, text="Reveal config.json in Finder",
                  command=self._on_reveal_config).grid(row=row, column=0, sticky='w', pady=2)

    def _check_now(self) -> None:
        self._on_check_now()
        self._refresh_last_check_label()

    def _refresh_last_check_label(self) -> None:
        last_check = self.config.get('updates', 'last_check', 0) or 0
        if last_check:
            when = time.strftime('%Y-%m-%d %H:%M', time.localtime(last_check))
            self.last_check_var.set(f"Last checked: {when}")
        else:
            self.last_check_var.set("Last checked: never")

    def _build_tracking_tab(self, notebook: ttk.Notebook) -> None:
        frame = self._tab(notebook, "Tracking")
        row = 0
        ttk.Label(frame, text="Pose", style='Dim.TLabel').grid(
            row=row, column=0, sticky='w', pady=(0, 2)); row += 1
        row = self._row_combo(frame, row, "Model:", 'mediapipe', 'pose_model_type',
                              ('lite', 'full', 'heavy'))
        row = self._row_int(frame, row, "Number of poses:", 'mediapipe', 'num_poses', width=6,
                            note="Tasks API only; >1 disables the combined holistic model.")
        row = self._row_float(frame, row, "Min detection confidence:",
                              'mediapipe', 'min_detection_confidence')
        row = self._row_float(frame, row, "Min tracking confidence:",
                              'mediapipe', 'min_tracking_confidence')
        row = self._row_float(frame, row, "Min pose presence confidence:",
                              'mediapipe', 'min_pose_presence_confidence')
        row = self._row_check(frame, row, "Smooth landmarks", 'mediapipe', 'smooth_landmarks')

        row += 1
        ttk.Separator(frame, orient='horizontal').grid(
            row=row, column=0, columnspan=3, sticky='ew', pady=8); row += 1

        ttk.Label(frame, text="Hands", style='Dim.TLabel').grid(
            row=row, column=0, sticky='w', pady=(0, 2)); row += 1
        row = self._row_int(frame, row, "Number of hands:", 'hand', 'num_hands', width=6)
        row = self._row_float(frame, row, "Min detection confidence:",
                              'hand', 'min_detection_confidence')
        row = self._row_float(frame, row, "Min presence confidence:",
                              'hand', 'min_presence_confidence')
        row = self._row_float(frame, row, "Min tracking confidence:",
                              'hand', 'min_tracking_confidence')

    def _build_preview_tab(self, notebook: ttk.Notebook) -> None:
        frame = self._tab(notebook, "Preview")
        row = 0
        row = self._row_check(frame, row, "Show the camera preview window",
                              'display', 'show_window')
        row = self._row_check(frame, row, "Mirror preview (display only)",
                              'display', 'mirror_preview')
        row = self._row_entry(frame, row, "Window title:", 'display', 'window_title', width=28)

        row += 1
        ttk.Separator(frame, orient='horizontal').grid(
            row=row, column=0, columnspan=3, sticky='ew', pady=8); row += 1

        row = self._row_color(frame, row, "Landmark color:", 'display', 'landmark_color')
        row = self._row_int(frame, row, "Landmark thickness:", 'display', 'landmark_thickness', width=6)
        row = self._row_int(frame, row, "Landmark radius:", 'display', 'landmark_radius', width=6)
        row = self._row_color(frame, row, "Connection color:", 'display', 'connection_color')
        row = self._row_int(frame, row, "Connection thickness:", 'display', 'connection_thickness', width=6)
        row = self._row_int(frame, row, "Connection radius:", 'display', 'connection_radius', width=6)

    def _build_advanced_tab(self, notebook: ttk.Notebook) -> None:
        frame = self._tab(notebook, "Advanced")
        row = 0
        ttk.Label(frame, text="Camera", style='Dim.TLabel').grid(
            row=row, column=0, sticky='w', pady=(0, 2)); row += 1
        row = self._row_int(frame, row, "Capture width:", 'camera', 'width', width=8)
        row = self._row_int(frame, row, "Capture height:", 'camera', 'height', width=8)
        row = self._row_int(frame, row, "Capture FPS:", 'camera', 'fps', width=8)
        row = self._row_int(frame, row, "Buffer size:", 'camera', 'buffer_size', width=8,
                            minimum=1)

        row += 1
        ttk.Separator(frame, orient='horizontal').grid(
            row=row, column=0, columnspan=3, sticky='ew', pady=8); row += 1

        ttk.Label(frame, text="Performance", style='Dim.TLabel').grid(
            row=row, column=0, sticky='w', pady=(0, 2)); row += 1
        row = self._row_int(frame, row, "Target FPS (0 = uncapped):",
                            'performance', 'target_fps', width=8)
        ttk.Checkbutton(frame, text="Show FPS/stats line",
                        variable=self.var_show_fps).grid(row=row, column=0, columnspan=2,
                                                         sticky='w', pady=2)
        row += 1
        row = self._row_check(frame, row, "Enable garbage collection", 'performance', 'gc_enabled')
        row = self._row_int(frame, row, "GC interval (frames):",
                            'performance', 'gc_interval', width=8)

        row += 1
        ttk.Separator(frame, orient='horizontal').grid(
            row=row, column=0, columnspan=3, sticky='ew', pady=8); row += 1

        ttk.Label(frame, text="OSC", style='Dim.TLabel').grid(
            row=row, column=0, sticky='w', pady=(0, 2)); row += 1
        row = self._row_int(frame, row, "Send queue size:", 'osc', 'queue_size', width=8,
                            note="Older queued messages are dropped once full.", minimum=1)

        row += 1
        ttk.Separator(frame, orient='horizontal').grid(
            row=row, column=0, columnspan=3, sticky='ew', pady=8); row += 1

        ttk.Label(frame, text="Backend (applies on next Start)", style='Dim.TLabel').grid(
            row=row, column=0, sticky='w', pady=(0, 2)); row += 1
        ttk.Checkbutton(frame, text="Force CPU delegate",
                        variable=self.var_force_cpu).grid(row=row, column=0, columnspan=2,
                                                          sticky='w', pady=2)
        row += 1
        ttk.Checkbutton(frame, text="Force GPU delegate",
                        variable=self.var_force_gpu).grid(row=row, column=0, columnspan=2,
                                                          sticky='w', pady=2)
        row += 1
        ttk.Label(frame, text=FORCE_GPU_WARNING, style='Dim.TLabel').grid(
            row=row, column=0, columnspan=2, sticky='w', padx=(20, 0))
        row += 1
        ttk.Checkbutton(frame, text="Force legacy MediaPipe API",
                        variable=self.var_force_legacy).grid(row=row, column=0, columnspan=2,
                                                             sticky='w', pady=2)
        row += 1
        ttk.Checkbutton(frame, text='No holistic (separate pose + hand models in "all" mode)',
                        variable=self.var_no_holistic).grid(row=row, column=0, columnspan=2,
                                                            sticky='w', pady=2)

    # ------------------------------------------------------------------------
    # Load / Save / Restore
    # ------------------------------------------------------------------------
    def _load_from_config(self) -> None:
        for section, key, var, kind in self._fields:
            value = self.config.get(section, key)
            self._set_var(var, kind, value)
        self._refresh_last_check_label()

        # The four backend toggles and Show FPS are owned by LauncherGui and
        # already reflect config on first build (see src.gui._init_variables).

    def _set_var(self, var, kind, value) -> None:
        if kind == 'bool':
            var.set(bool(value))
        elif kind == 'color':
            if isinstance(value, (list, tuple)) and len(value) == 3:
                b, g, r = value  # stored BGR, per cv2 usage elsewhere in the app
                var.set('#{:02x}{:02x}{:02x}'.format(int(r), int(g), int(b)))
        else:
            var.set('' if value is None else str(value))

    def _restore_defaults(self) -> None:
        if not messagebox.askyesno(
                "Restore Defaults", "Reset every field on this window to its default value?\n"
                "Nothing is saved until you click Save.", parent=self.top):
            return
        defaults = Config.DEFAULT_CONFIG
        for section, key, var, kind in self._fields:
            value = defaults.get(section, {}).get(key)
            self._set_var(var, kind, value)

    def _save(self) -> None:
        errors = []
        pending = []  # (section, key, value) applied only if everything validates

        for section, key, var, kind in self._fields:
            raw = var.get()
            try:
                if kind == 'bool':
                    value = bool(raw)
                elif kind == 'int':
                    value = int(str(raw).strip())
                    if value < self._int_minimums.get((section, key), 0):
                        raise ValueError('below minimum')
                elif kind == 'float':
                    value = float(str(raw).strip())
                    if not valid_unit_float(value):
                        raise ValueError('must be between 0.0 and 1.0')
                elif kind == 'color':
                    hex_color = str(raw).lstrip('#')
                    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
                    value = [b, g, r]  # back to the app's stored BGR order
                else:
                    value = str(raw)
            except (ValueError, IndexError):
                errors.append(f"{section}.{key}: invalid value \"{raw}\"")
                continue
            pending.append((section, key, value))

        if errors:
            messagebox.showerror("Invalid Settings", "\n".join(errors), parent=self.top)
            return

        for section, key, value in pending:
            self.config.set(section, key, value)

        self.config.set('performance', 'force_cpu', bool(self.var_force_cpu.get()))
        self.config.set('performance', 'force_gpu', bool(self.var_force_gpu.get()))
        self.config.set('performance', 'force_legacy', bool(self.var_force_legacy.get()))
        self.config.set('performance', 'no_holistic', bool(self.var_no_holistic.get()))
        self.config.set('performance', 'show_fps', bool(self.var_show_fps.get()))

        self.config.save()
        if self._on_saved is not None:
            self._on_saved()
        self.destroy()
