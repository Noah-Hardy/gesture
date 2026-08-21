#!/usr/bin/env python3
"""
Dark Theme Module

One palette, applied to every tkinter/ttk window in the app. The launcher
runs in dark theatres and control booths, so the design goal is simple: dark
grey background, green accent, no widget left in stock aqua white.

macOS's aqua ttk theme ignores background/foreground on most widgets by
design (it always paints the system look), so a dark theme is only possible
by switching to 'clam' and configuring every style from scratch here.
"""

# ============================================================================
# IMPORTS
# ============================================================================
import tkinter as tk
from tkinter import ttk

# ============================================================================
# PALETTE
# ============================================================================
PALETTE = {
    'bg':        '#1E2129',   # window ground - matches scripts/make_icon.py BG_TOP
    'surface':   '#262A33',   # cards / section bodies
    'surface_2': '#2E333D',   # entries, comboboxes, the log pane
    'border':    '#3A404C',
    'text':      '#E6E8EC',
    'text_dim':  '#9AA1AD',
    'accent':    '#3DDC84',   # green - Start button, links, focus
    'accent_2':  '#2FB86C',   # pressed/hover accent
    'warn':      '#E5A54B',
    'error':     '#E5695B',
}


# ============================================================================
# THEME APPLICATION
# ============================================================================
def apply_theme(root: tk.Misc) -> ttk.Style:
    """
    Switch to the 'clam' ttk theme and configure every widget style used by
    the app. Call once, on the Tk root, before building any widgets.

    Returns:
        The configured ttk.Style, in case a caller needs to add more styles.
    """
    p = PALETTE
    style = ttk.Style(root)
    style.theme_use('clam')

    # tk_setPalette covers plain tk widgets (tk.Text, tk.Menu, messagebox
    # dialogs) that ttk styling never reaches.
    root.tk_setPalette(
        background=p['bg'], foreground=p['text'],
        activeBackground=p['surface_2'], activeForeground=p['text'],
        selectBackground=p['accent'], selectForeground=p['bg'],
        highlightBackground=p['bg'], highlightColor=p['accent'],
        insertBackground=p['text'],
    )
    root.configure(bg=p['bg'])

    style.configure('.', background=p['bg'], foreground=p['text'],
                    fieldbackground=p['surface_2'], bordercolor=p['border'],
                    lightcolor=p['border'], darkcolor=p['border'],
                    troughcolor=p['surface'], focuscolor=p['accent'])

    style.configure('TFrame', background=p['bg'])
    style.configure('Card.TFrame', background=p['surface'])

    style.configure('TLabel', background=p['bg'], foreground=p['text'])
    style.configure('Dim.TLabel', background=p['bg'], foreground=p['text_dim'])
    style.configure('Card.TLabel', background=p['surface'], foreground=p['text'])

    style.configure('TLabelframe', background=p['bg'], bordercolor=p['border'])
    style.configure('TLabelframe.Label', background=p['bg'], foreground=p['text_dim'])

    style.configure('TSeparator', background=p['border'])

    style.configure('TButton', background=p['surface_2'], foreground=p['text'],
                    bordercolor=p['border'], focuscolor=p['accent'], padding=(10, 5))
    style.map('TButton',
             background=[('disabled', p['surface']), ('pressed', p['border']),
                         ('active', p['border'])],
             foreground=[('disabled', p['text_dim'])])

    style.configure('Accent.TButton', background=p['accent'], foreground='#0B120D')
    style.map('Accent.TButton',
             background=[('disabled', p['surface']), ('pressed', p['accent_2']),
                         ('active', p['accent_2'])],
             foreground=[('disabled', p['text_dim'])])

    style.configure('Error.TButton', background=p['error'], foreground='#160404')
    style.map('Error.TButton',
             background=[('disabled', p['surface']), ('pressed', '#C2493C'),
                         ('active', '#C2493C')],
             foreground=[('disabled', p['text_dim'])])

    style.configure('TEntry', fieldbackground=p['surface_2'], foreground=p['text'],
                    insertcolor=p['text'], bordercolor=p['border'])
    style.map('TEntry', fieldbackground=[('disabled', p['surface'])],
             foreground=[('disabled', p['text_dim'])])

    style.configure('TSpinbox', fieldbackground=p['surface_2'], foreground=p['text'],
                    insertcolor=p['text'], bordercolor=p['border'], arrowcolor=p['text'])
    style.map('TSpinbox', fieldbackground=[('disabled', p['surface'])])

    style.configure('TCombobox', fieldbackground=p['surface_2'], foreground=p['text'],
                    background=p['surface_2'], arrowcolor=p['text'], bordercolor=p['border'])
    style.map('TCombobox',
             fieldbackground=[('readonly', p['surface_2']), ('disabled', p['surface'])],
             foreground=[('disabled', p['text_dim'])])
    # The dropdown listbox is a plain tk widget under the hood, not ttk.
    root.option_add('*TCombobox*Listbox.background', p['surface_2'])
    root.option_add('*TCombobox*Listbox.foreground', p['text'])
    root.option_add('*TCombobox*Listbox.selectBackground', p['accent'])
    root.option_add('*TCombobox*Listbox.selectForeground', p['bg'])

    style.configure('TCheckbutton', background=p['bg'], foreground=p['text'])
    style.map('TCheckbutton', foreground=[('disabled', p['text_dim'])])
    style.configure('Card.TCheckbutton', background=p['surface'], foreground=p['text'])

    style.configure('TRadiobutton', background=p['bg'], foreground=p['text'])
    style.map('TRadiobutton', foreground=[('disabled', p['text_dim'])])

    style.configure('TNotebook', background=p['bg'], bordercolor=p['border'])
    style.configure('TNotebook.Tab', background=p['surface'], foreground=p['text_dim'],
                    padding=(12, 6))
    style.map('TNotebook.Tab',
             background=[('selected', p['bg'])],
             foreground=[('selected', p['text'])])

    style.configure('TScrollbar', background=p['surface_2'], troughcolor=p['bg'],
                    bordercolor=p['bg'], arrowcolor=p['text_dim'])
    style.map('TScrollbar', background=[('active', p['border'])])

    style.configure('Treeview', background=p['surface_2'], fieldbackground=p['surface_2'],
                    foreground=p['text'], bordercolor=p['border'])
    style.map('Treeview',
             background=[('selected', p['accent'])],
             foreground=[('selected', p['bg'])])
    style.configure('Treeview.Heading', background=p['surface'], foreground=p['text_dim'])

    style.configure('TProgressbar', background=p['accent'], troughcolor=p['surface_2'],
                    bordercolor=p['border'])

    # Flat, left-aligned button used as a CollapsibleSection header.
    style.configure('Section.TButton', background=p['bg'], foreground=p['text'],
                    bordercolor=p['bg'], relief='flat', anchor='w',
                    padding=(4, 6), font=('TkDefaultFont', 0, 'bold'))
    style.map('Section.TButton',
             background=[('active', p['surface'])],
             bordercolor=[('active', p['bg'])])

    return style


def style_text_widget(widget: tk.Text) -> None:
    """Apply the dark palette to a plain tk.Text (not covered by ttk styles)"""
    p = PALETTE
    widget.configure(
        background=p['surface_2'], foreground=p['text'],
        insertbackground=p['text'],
        selectbackground=p['accent'], selectforeground=p['bg'],
        highlightbackground=p['border'], highlightcolor=p['accent'],
    )


# ============================================================================
# COLLAPSIBLE SECTION
# ============================================================================
class CollapsibleSection(ttk.Frame):
    """
    A titled section with a disclosure header (▾/▸) that shows or hides its
    body frame. Options that don't need to be visible all the time - like
    Model & Performance - default to collapsed, keeping the window short.
    Clicking the header animates the body open/closed; set_open snaps.

    Usage:
        section = CollapsibleSection(parent, title="Model & Performance",
                                     open=False, on_toggle=save_state)
        section.grid(row=row, column=0, sticky='ew')
        body = section.body  # a ttk.Frame - build the section's widgets in it

    fill=True makes the open body stretch to absorb extra height (for the
    log pane); the caller still owns the parent row's weight.
    """

    ANIM_MS = 140   # total expand/collapse duration
    TICK_MS = 15    # per-frame interval (~9 frames)

    def __init__(self, parent, title: str, open: bool = True, on_toggle=None,
                 fill: bool = False):
        super().__init__(parent)
        self._open = open
        self._on_toggle = on_toggle
        self._fill = fill
        self._anim_job = None
        self._anim_height = 0  # last height set by a running animation
        self.columnconfigure(0, weight=1)

        self._header = ttk.Button(self, style='Section.TButton',
                                  command=self._toggle, cursor='pointinghand')
        self._header.grid(row=0, column=0, sticky='ew')

        # The body sits inside a clip frame whose height is tweened during
        # animation (grid_propagate off); settled, the clip sizes naturally.
        self._clip = ttk.Frame(self)
        self._clip.columnconfigure(0, weight=1)
        if fill:
            self._clip.rowconfigure(0, weight=1)
        self.body = ttk.Frame(self._clip, padding=(8, 8, 8, 8))
        self.body.grid(row=0, column=0, sticky='nsew' if fill else 'ew')

        self._update_header_text(title)
        self._sync_body()

    def _update_header_text(self, title: str) -> None:
        arrow = '▾' if self._open else '▸'
        self._header.configure(text=f"{arrow}  {title}")
        self._title = title

    def _grid_clip(self) -> None:
        self._clip.grid(row=1, column=0, sticky='nsew' if self._fill else 'ew',
                        pady=(2, 0))

    def _sync_body(self) -> None:
        """Snap to the settled open/closed state without animating"""
        self._cancel_anim()
        self._clip.grid_propagate(True)
        if self._fill:
            self.rowconfigure(1, weight=1)
        if self._open:
            self._grid_clip()
        else:
            self._clip.grid_remove()

    def _toggle(self) -> None:
        self._open = not self._open
        self._update_header_text(self._title)
        self._animate(self._open)
        if self._on_toggle is not None:
            self._on_toggle(self._open)

    # ------------------------------------------------------------------
    # Animation
    # ------------------------------------------------------------------
    def _cancel_anim(self) -> None:
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except tk.TclError:
                pass
            self._anim_job = None

    def _animate(self, opening: bool) -> None:
        was_animating = self._anim_job is not None
        self._cancel_anim()
        try:
            self.update_idletasks()
            target = self.body.winfo_reqheight() if opening else 0
            # Reversing mid-animation starts from wherever the tween got to
            start = self._anim_height if was_animating else (
                self.body.winfo_reqheight() if not opening else 0)
        except tk.TclError:
            return

        # While tweening, the section's own body row must not stretch the
        # clip past its explicit height (only matters when fill=True).
        if self._fill:
            self.rowconfigure(1, weight=0)
        self._grid_clip()
        self._clip.grid_propagate(False)

        steps = max(1, self.ANIM_MS // self.TICK_MS)
        delta = target - start

        def tick(i: int = 1) -> None:
            self._anim_job = None
            try:
                if not self.winfo_exists():
                    return
                eased = 1 - (1 - i / steps) ** 2
                self._anim_height = max(1, int(start + delta * eased))
                self._clip.configure(height=self._anim_height)
                if i < steps:
                    self._anim_job = self.after(self.TICK_MS, lambda: tick(i + 1))
                else:
                    self._finish_anim(opening)
            except tk.TclError:
                pass

        tick()

    def _finish_anim(self, opening: bool) -> None:
        self._anim_job = None
        if not opening:
            self._clip.grid_remove()
        self._clip.grid_propagate(True)
        if self._fill:
            self.rowconfigure(1, weight=1)

    @property
    def is_open(self) -> bool:
        return self._open

    def set_open(self, value: bool) -> None:
        if value == self._open:
            return
        self._open = value
        self._update_header_text(self._title)
        self._sync_body()
