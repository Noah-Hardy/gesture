#!/usr/bin/env python3
"""
Update Dialog Module

The window shown when src.updater finds a newer release: release notes,
then a progress bar while it downloads and verifies, then either a relaunch
or a plain-English failure. Main thread only - src.gui drives every method
here from its existing queue.Queue -> 100ms after() poll, the same place it
already handles engine log lines and NDI discovery results.

Modeled on src.help_window (Toplevel lifecycle, font/tag setup, Markdown
rendering) rather than importing it, since the two windows have different
enough shapes that sharing a base class would cost more than it saves.
"""

# ============================================================================
# IMPORTS
# ============================================================================
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable, Optional

from src import docs
from src import theme
from src.updater import Release

TABLE_WIDTH = 70


# ============================================================================
# UPDATE DIALOG
# ============================================================================
class UpdateDialog:
    """
    Non-modal Toplevel that walks the user through one update: notes ->
    progress -> done (relaunching) or failed. There is exactly one of these
    alive at a time - src.gui owns that singleton, same as its HelpWindow.
    """

    def __init__(self, parent: tk.Misc, release: Release, *,
                on_install: Callable[[], None],
                on_skip: Callable[[], None],
                on_later: Callable[[], None],
                on_cancel: Callable[[], None],
                on_close: Optional[Callable[[], None]] = None) -> None:
        self._on_install = on_install
        self._on_skip = on_skip
        self._on_later = on_later
        self._on_cancel = on_cancel
        self._on_close = on_close
        self.release = release

        self.top = tk.Toplevel(parent)
        self.top.title(f"MP-OSC {release.version} Available")
        self.top.minsize(560, 420)
        self.top.geometry("620x480")
        self.top.configure(bg=theme.PALETTE['bg'])
        self.top.protocol("WM_DELETE_WINDOW", self._handle_close)
        self.top.bind('<Escape>', lambda e: self._handle_close())

        self._build_fonts()
        self._build_layout()
        self._configure_tags()
        self._render_notes(release)
        self._show_notes_buttons()

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------
    def exists(self) -> bool:
        return self.top is not None and self.top.winfo_exists()

    def lift(self) -> None:
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()

    def destroy(self) -> None:
        if self.top is None:
            return
        top, self.top = self.top, None
        top.destroy()

    def _handle_close(self) -> None:
        """The window's close box - behaves like Later unless we're mid-install"""
        if self._busy:
            return  # don't let the user close out from under a running install
        if self._on_close is not None:
            self._on_close()
        self._on_later()

    # ------------------------------------------------------------------------
    # Fonts / layout - same derivation pattern as src.help_window
    # ------------------------------------------------------------------------
    def _build_fonts(self) -> None:
        base = tkfont.nametofont('TkDefaultFont')
        mono = tkfont.nametofont('TkFixedFont')
        base_size = base.actual('size')

        self.fonts = {'body': base.copy(), 'mono': mono.copy(), 'bold': base.copy()}
        self.fonts['bold'].configure(weight='bold')
        h1 = base.copy(); h1.configure(size=base_size + 5, weight='bold')
        h2 = base.copy(); h2.configure(size=base_size + 2, weight='bold')
        self.fonts['h1'] = h1
        self.fonts['h2'] = h2

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.top, padding=12)
        outer.grid(row=0, column=0, sticky='nsew')
        self.top.rowconfigure(0, weight=1)
        self.top.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        self.header = ttk.Label(outer, text='', font=self.fonts['h1'])
        self.header.grid(row=0, column=0, sticky='w', pady=(0, 8))

        text_frame = ttk.Frame(outer)
        text_frame.grid(row=1, column=0, sticky='nsew')
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        self.text = tk.Text(text_frame, wrap='word', state='disabled', borderwidth=0,
                            highlightthickness=0, padx=12, pady=8, cursor='arrow',
                            font=self.fonts['body'])
        theme.style_text_widget(self.text)
        self.text.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(text_frame, orient='vertical', command=self.text.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.text.configure(yscrollcommand=scroll.set)

        # Progress bar, hidden until an install starts - its own row so it
        # never collides with the notes/status rows around it.
        self.progress = ttk.Progressbar(outer, orient='horizontal', mode='determinate')

        # Status line, shown during progress/verify/failed states
        self.status_var = tk.StringVar(value='')
        self.status_label = ttk.Label(outer, textvariable=self.status_var, style='Dim.TLabel')
        self.status_label.grid(row=3, column=0, sticky='w', pady=(8, 0))

        # Button bar - contents are swapped per state via the _show_*/_enter_* helpers
        self.button_bar = ttk.Frame(outer)
        self.button_bar.grid(row=4, column=0, sticky='ew', pady=(10, 0))
        self.button_bar.columnconfigure(0, weight=1)

        self._busy = False

    def _configure_tags(self) -> None:
        t = self.text
        t.tag_configure('body', font=self.fonts['body'], spacing3=8)
        t.tag_configure('h1', font=self.fonts['h1'], spacing3=8)
        t.tag_configure('h2', font=self.fonts['h2'], spacing1=10, spacing3=6)
        t.tag_configure('h3', font=self.fonts['bold'], spacing1=8, spacing3=4)
        t.tag_configure('bullet', font=self.fonts['body'], lmargin1=18, lmargin2=32, spacing3=2)
        t.tag_configure('number', font=self.fonts['body'], lmargin1=18, lmargin2=32, spacing3=2)
        t.tag_configure('code_block', font=self.fonts['mono'], lmargin1=20, lmargin2=20,
                       spacing1=6, spacing3=6)
        t.tag_configure('rule', font=self.fonts['mono'], foreground=theme.PALETTE['text_dim'])
        t.tag_configure('table', font=self.fonts['mono'], spacing3=2)
        t.tag_configure('code_inline', font=self.fonts['mono'])
        t.tag_configure('bold', font=self.fonts['bold'])
        t.tag_configure('link', foreground=theme.PALETTE['accent'], underline=True)
        for tag in ('code_inline', 'bold', 'link'):
            t.tag_raise(tag)

    # ------------------------------------------------------------------------
    # Release notes (state 1)
    # ------------------------------------------------------------------------
    def _render_notes(self, release: Release) -> None:
        self.header.configure(text=f"MP-OSC {release.version} is available")
        body = release.notes.strip() or "No release notes were provided."
        blocks = docs.parse(body)
        plan = docs.render_tk(blocks, width_chars=TABLE_WIDTH)

        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        for chunk, tags in plan.chunks:
            self.text.insert('end', chunk, tags)
        self.text.configure(state='disabled')

        for tag, href in plan.links.items():
            self.text.tag_bind(tag, '<Button-1>', lambda e, u=href: self._open_link(u))

        self.status_var.set(f"You're running an older version. Tag: {release.tag}")

    def _open_link(self, href: str) -> None:
        if href.startswith(('http://', 'https://')):
            import webbrowser
            try:
                webbrowser.open(href)
            except Exception:
                pass

    def _show_notes_buttons(self) -> None:
        self._busy = False
        self.progress.grid_remove()
        for child in self.button_bar.winfo_children():
            child.destroy()

        ttk.Button(self.button_bar, text="Skip This Version",
                  command=self._on_skip).grid(row=0, column=1, sticky='e', padx=(0, 8))
        ttk.Button(self.button_bar, text="Later",
                  command=self._on_later).grid(row=0, column=2, sticky='e', padx=(0, 8))
        ttk.Button(self.button_bar, text="Install and Relaunch", style='Accent.TButton',
                  command=self._on_install).grid(row=0, column=3, sticky='e')

    # ------------------------------------------------------------------------
    # Progress (state 2) - driven by src.gui from UpdateController payloads
    # ------------------------------------------------------------------------
    def show_progress(self, done: int, total: int) -> None:
        self._enter_busy_state()
        self.progress.configure(mode='determinate', maximum=max(total, 1), value=done)
        if total:
            mb_done = done / (1 << 20)
            mb_total = total / (1 << 20)
            pct = min(100, int(done * 100 / total))
            self.status_var.set(f"Downloading update… {mb_done:.0f} / {mb_total:.0f} MB ({pct}%)")
        else:
            self.status_var.set("Downloading update…")

    def show_verifying(self, phase: str) -> None:
        self._enter_busy_state()
        self.progress.configure(mode='indeterminate')
        self.progress.start(12)
        labels = {'extract': 'Unpacking the update…', 'signature': 'Verifying the update…'}
        self.status_var.set(labels.get(phase, 'Verifying the update…'))

    def _enter_busy_state(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.header.configure(text=f"MP-OSC {self.release.version}")
        self.progress.grid(row=2, column=0, sticky='ew', pady=(8, 0))
        for child in self.button_bar.winfo_children():
            child.destroy()
        ttk.Button(self.button_bar, text="Cancel", command=self._on_cancel).grid(
            row=0, column=3, sticky='e')

    # ------------------------------------------------------------------------
    # Terminal states
    # ------------------------------------------------------------------------
    def show_ready(self) -> None:
        """The update is staged and about to relaunch - nothing left to click"""
        try:
            self.progress.stop()
        except tk.TclError:
            pass
        self.status_var.set("Installing and relaunching MP-OSC…")
        for child in self.button_bar.winfo_children():
            child.destroy()

    def show_failed(self, message: str) -> None:
        try:
            self.progress.stop()
        except tk.TclError:
            pass
        self.progress.grid_remove()
        self._busy = False
        self.header.configure(text="Update Failed")
        self.status_var.set(message or "The update could not be installed.")

        for child in self.button_bar.winfo_children():
            child.destroy()
        ttk.Button(self.button_bar, text="Close", command=self._handle_close).grid(
            row=0, column=2, sticky='e', padx=(0, 8))
        ttk.Button(self.button_bar, text="Open Release Page", style='Accent.TButton',
                  command=lambda: self._open_link(self.release.html_url)).grid(
            row=0, column=3, sticky='e')
