#!/usr/bin/env python3
"""
Help Window Module

The in-app documentation viewer: a topic sidebar plus a styled reader pane,
both driven by the parsed Markdown in src.docs. New module rather than more
mass in gui.py -- this brings its own font table, tag configuration, topic
state and link handling, which is a distinct concern from the launcher form.
"""

# ============================================================================
# IMPORTS
# ============================================================================
import tkinter as tk
import webbrowser
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable, Dict, Optional

from src import docs
from src import theme

TABLE_WIDTH = 80
LINK_CURSOR_CANDIDATES = ('pointinghand', 'hand2', 'arrow')


# ============================================================================
# HELP WINDOW
# ============================================================================
class HelpWindow:
    """
    Non-modal documentation viewer: a Treeview topic sidebar and a read-only
    styled tk.Text reader pane, sharing one parsed-Markdown source with the
    browser HTML via src.docs.
    """

    def __init__(self, parent: tk.Misc, on_close: Optional[Callable[[], None]] = None) -> None:
        """Build the window and populate it with the Welcome topic"""
        self._on_close = on_close
        self._link_targets: Dict[str, str] = {}
        self.current_slug: Optional[str] = None

        self.top = tk.Toplevel(parent)
        self.top.title("MP-OSC Help")
        self.top.minsize(820, 520)
        self.top.geometry("920x640")
        self.top.configure(bg=theme.PALETTE['bg'])
        self.top.protocol("WM_DELETE_WINDOW", self.destroy)
        self.top.bind('<Escape>', lambda e: self.destroy())
        self.top.bind('<Command-w>', lambda e: self.destroy())

        self._build_fonts()
        self._build_layout()
        self._configure_tags()
        self._populate_tree()

        self.show(docs.TOPICS[0].slug)

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------
    def exists(self) -> bool:
        """True while the underlying Toplevel is still alive"""
        return self.top is not None and self.top.winfo_exists()

    def show(self, slug: Optional[str] = None) -> None:
        """Bring the window forward, optionally switching to a specific topic

        Renders directly rather than relying on selection_set() to trigger
        <<TreeviewSelect>>: that virtual event is only delivered through the
        Tk event loop, which programmatic navigation (menu items, reopening
        the singleton) cannot assume is actively pumping.
        """
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()
        target = slug or self.current_slug or docs.TOPICS[0].slug
        if target != self.current_slug:
            self._render_topic(target)

    def destroy(self) -> None:
        """Close the window and notify the owner"""
        if self.top is None:
            return
        top, self.top = self.top, None
        top.destroy()
        if self._on_close is not None:
            self._on_close()

    # ------------------------------------------------------------------------
    # Fonts -- the first place in the repo to configure fonts, deliberately.
    # Every face is derived from the system's own named fonts (never a
    # hardcoded family/size) so the viewer still tracks the aqua theme.
    # ------------------------------------------------------------------------
    def _build_fonts(self) -> None:
        base = tkfont.nametofont('TkDefaultFont')
        mono = tkfont.nametofont('TkFixedFont')
        base_size = base.actual('size')

        self.fonts = {'body': base.copy(), 'mono': mono.copy(), 'bold': base.copy()}
        self.fonts['bold'].configure(weight='bold')

        h1 = base.copy(); h1.configure(size=base_size + 7, weight='bold')
        h2 = base.copy(); h2.configure(size=base_size + 3, weight='bold')
        h3 = base.copy(); h3.configure(size=base_size + 1, weight='bold')
        self.fonts['h1'] = h1
        self.fonts['h2'] = h2
        self.fonts['h3'] = h3

    # ------------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------------
    def _build_layout(self) -> None:
        outer = ttk.Frame(self.top, padding=10)
        outer.grid(row=0, column=0, sticky='nsew')
        self.top.rowconfigure(0, weight=1)
        self.top.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)

        sidebar = ttk.Frame(outer, width=214)
        sidebar.grid(row=0, column=0, sticky='ns', padx=(0, 10))
        sidebar.rowconfigure(0, weight=1)
        sidebar.grid_propagate(False)

        self.tree = ttk.Treeview(sidebar, show='tree', selectmode='browse')
        self.tree.grid(row=0, column=0, sticky='nsew')
        tree_scroll = ttk.Scrollbar(sidebar, orient='vertical', command=self.tree.yview)
        tree_scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        reader = ttk.LabelFrame(outer, text="", padding=4)
        reader.grid(row=0, column=1, sticky='nsew')
        reader.rowconfigure(0, weight=1)
        reader.columnconfigure(0, weight=1)
        self.reader = reader

        self.text = tk.Text(
            reader, wrap='word', state='disabled', borderwidth=0,
            highlightthickness=0, padx=14, pady=10, width=84, height=30,
            cursor='arrow', font=self.fonts['body'],
        )
        theme.style_text_widget(self.text)
        self.text.grid(row=0, column=0, sticky='nsew')
        text_scroll = ttk.Scrollbar(reader, orient='vertical', command=self.text.yview)
        text_scroll.grid(row=0, column=1, sticky='ns')
        self.text.configure(yscrollcommand=text_scroll.set)

        buttons = ttk.Frame(outer)
        buttons.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        buttons.columnconfigure(1, weight=1)

        open_btn = ttk.Button(buttons, text="\U0001F310 Open Full Documentation",
                              command=self._open_full_docs)
        open_btn.grid(row=0, column=0, sticky='w')

        close_btn = ttk.Button(buttons, text="Close", command=self.destroy)
        close_btn.grid(row=0, column=2, sticky='e')

    # ------------------------------------------------------------------------
    # Tag configuration
    # ------------------------------------------------------------------------
    def _configure_tags(self) -> None:
        t = self.text
        t.tag_configure('body', font=self.fonts['body'], spacing3=8)
        t.tag_configure('h1', font=self.fonts['h1'], spacing3=8)
        t.tag_configure('h2', font=self.fonts['h2'], spacing1=14, spacing3=6)
        t.tag_configure('h3', font=self.fonts['h3'], spacing1=10, spacing3=4)
        t.tag_configure('bullet', font=self.fonts['body'], lmargin1=18, lmargin2=32, spacing3=2)
        t.tag_configure('number', font=self.fonts['body'], lmargin1=18, lmargin2=32, spacing3=2)
        t.tag_configure('code_block', font=self.fonts['mono'], lmargin1=24, lmargin2=24,
                       spacing1=6, spacing3=6)
        t.tag_configure('rule', font=self.fonts['mono'], foreground=theme.PALETTE['text_dim'])
        t.tag_configure('table', font=self.fonts['mono'], spacing3=2)

        # Inline tags are created after block tags and explicitly raised, so
        # inline styling always wins over the block tag it's nested inside
        # (tk.Text tag precedence is creation order, later tags win).
        t.tag_configure('code_inline', font=self.fonts['mono'])
        t.tag_configure('bold', font=self.fonts['bold'])
        t.tag_configure('link', foreground=theme.PALETTE['accent'], underline=True)
        for tag in ('code_inline', 'bold', 'link'):
            t.tag_raise(tag)

    # ------------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------------
    def _populate_tree(self) -> None:
        last_group = None
        for topic in docs.TOPICS:
            if topic.group != last_group:
                self.tree.insert('', 'end', iid=f'group:{topic.group}',
                                 text=topic.group, open=True, tags=('group',))
                last_group = topic.group
            self.tree.insert(f'group:{topic.group}', 'end', iid=topic.slug, text=topic.label)
        self.tree.tag_configure('group', font=self.fonts['bold'])

    def _select_tree_item(self, slug: str) -> None:
        self.tree.selection_set(slug)
        self.tree.see(slug)

    def _on_select(self, event=None) -> None:
        """User clicked a sidebar row -- render it, unless it's already showing"""
        selection = self.tree.selection()
        if not selection:
            return
        slug = selection[0]
        if slug.startswith('group:') or slug == self.current_slug:
            return
        self._render_topic(slug)

    # ------------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------------
    def _render_topic(self, slug: str) -> None:
        """Render one topic into the reader pane, replacing whatever was there"""
        for tag in self.text.tag_names():
            if tag.startswith('link-'):
                self.text.tag_delete(tag)
        self._link_targets.clear()

        blocks = docs.load_topic(slug)
        plan = docs.render_tk(blocks, width_chars=TABLE_WIDTH)

        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        for chunk, tags in plan.chunks:
            self.text.insert('end', chunk, tags)
        self.text.configure(state='disabled')

        self._bind_links(plan.links)
        self.text.yview_moveto(0.0)

        topic = docs.topic_by_slug(slug)
        self.reader.configure(text=topic.label)
        self.current_slug = slug
        if self.tree.selection() != (slug,):
            self._select_tree_item(slug)

    # ------------------------------------------------------------------------
    # Links
    # ------------------------------------------------------------------------
    def _bind_links(self, links: Dict[str, str]) -> None:
        cursor = self._pick_cursor()
        for tag, href in links.items():
            self._link_targets[tag] = href
            self.text.tag_bind(tag, '<Button-1>', lambda e, u=href: self._open_link(u))
            self.text.tag_bind(tag, '<Enter>', lambda e, c=cursor: self.text.configure(cursor=c))
            self.text.tag_bind(tag, '<Leave>', lambda e: self.text.configure(cursor='arrow'))

    def _pick_cursor(self) -> str:
        for candidate in LINK_CURSOR_CANDIDATES:
            try:
                self.text.configure(cursor=candidate)
                self.text.configure(cursor='arrow')
                return candidate
            except tk.TclError:
                continue
        return 'arrow'

    def _open_link(self, href: str) -> None:
        if not href.startswith(('http://', 'https://', 'mailto:')):
            return
        try:
            webbrowser.open(href)
        except Exception:
            pass

    def _open_full_docs(self) -> None:
        """Render the full HTML site and open it in the default browser"""
        try:
            docs.open_site()
        except Exception:
            try:
                webbrowser.open('https://github.com/Noah-Hardy/mp-osc#readme')
            except Exception:
                pass
