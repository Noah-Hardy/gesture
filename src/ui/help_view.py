#!/usr/bin/env python3
"""
Help View

The in-app documentation: a topic list (Guide / Reference, from
src.docs.TOPICS) on the left and the rendered page (src.docs.render_qt) on
the right. Navigable by `help://<slug>#<anchor>` links, which fault cards and
settings notes use to open the pane at a section.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QTextDocument
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from src import docs
from src.ui.tokens import PALETTE, Font

HELP_SCHEME = 'help'


# ============================================================================
# URL RESOLUTION
# ============================================================================
def resolve_help_url(url) -> Tuple[str, Optional[str]]:
    """
    Split a `help://<slug>#<anchor>` link into its topic and section.

    Args:
        url: A str or QUrl.

    Returns:
        (slug, anchor); anchor is None when the link has no fragment.

    Raises:
        ValueError: Not a help:// URL, or the slug is not a known topic.
    """
    text = url.toString() if isinstance(url, QUrl) else str(url)
    parts = urlparse(text)
    if parts.scheme != HELP_SCHEME:
        raise ValueError(f'Not a help:// URL: {text!r}')
    slug = parts.netloc or parts.path.strip('/')
    try:
        docs.topic_by_slug(slug)
    except KeyError as e:
        raise ValueError(str(e)) from e
    return slug, (parts.fragment or None)


# ============================================================================
# VIEW
# ============================================================================
class HelpView(QWidget):
    """Two-column documentation viewer."""

    topicChanged = Signal(str)   # slug of the page now showing

    LIST_WIDTH = 230

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._docs: Dict[str, QTextDocument] = {}
        self._items: Dict[str, QListWidgetItem] = {}
        self._slug: Optional[str] = None

        self._list = QListWidget()
        self._list.setFixedWidth(self.LIST_WIDTH)
        self._list.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setFrameShape(QListWidget.Shape.NoFrame)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setStyleSheet(f"""
            QListWidget {{ background: transparent; border: none;
                border-right: 1px solid {PALETTE['border']}; padding: 14px 10px; outline: 0; }}
            QListWidget::item {{ padding: 7px 8px; border-radius: 8px; color: {PALETTE['dim']}; }}
            QListWidget::item:hover {{ background: {PALETTE['row_alt']}; }}
            QListWidget::item:selected {{ background: {PALETTE['row_alt']}; color: {PALETTE['text']}; }}
        """)
        self._list.setFont(Font.ui(12))
        last_group = None
        for topic in docs.TOPICS:
            if topic.group != last_group:
                header = QListWidgetItem(topic.group.upper())
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                header.setForeground(self._color('faint'))
                hf = Font.ui(10, 500)
                hf.setLetterSpacing(hf.SpacingType.AbsoluteSpacing, 1.2)
                header.setFont(hf)
                self._list.addItem(header)
                last_group = topic.group
            item = QListWidgetItem(topic.label)
            item.setData(Qt.ItemDataRole.UserRole, topic.slug)
            self._list.addItem(item)
            self._items[topic.slug] = item
        self._list.currentItemChanged.connect(self._on_list_changed)

        self._kicker = QLabel()
        kf = Font.ui(11, 500)
        kf.setLetterSpacing(kf.SpacingType.AbsoluteSpacing, 1.5)
        self._kicker.setFont(kf)
        self._kicker.setStyleSheet(f"color: {PALETTE['accent']}; background: transparent;")

        self._browser = QTextBrowser()
        self._browser.setOpenLinks(False)
        self._browser.setFrameShape(QTextBrowser.Shape.NoFrame)
        self._browser.setStyleSheet('QTextBrowser { background: transparent; border: none; }')
        self._browser.anchorClicked.connect(self.handle_link)

        page = QVBoxLayout()
        page.setContentsMargins(40, 30, 40, 0)
        page.setSpacing(10)
        page.addWidget(self._kicker)
        page.addWidget(self._browser, 1)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._list)
        root.addLayout(page, 1)

        self.open('welcome')

    @staticmethod
    def _color(name: str):
        from src.ui.tokens import color
        return color(name)

    # ------------------------------------------------------------------
    @property
    def current_slug(self) -> Optional[str]:
        """Slug of the topic currently showing."""
        return self._slug

    def open(self, slug: str, anchor: Optional[str] = None) -> None:
        """
        Show a topic, optionally scrolled to a `##` section.

        Args:
            slug: A topic slug from src.docs.TOPICS.
            anchor: A heading anchor (src.docs.heading_slug); unknown anchors
                leave the page at the top.

        Raises:
            KeyError: Unknown slug.
        """
        topic = docs.topic_by_slug(slug)
        if slug not in self._docs:
            self._docs[slug] = docs.render_qt(docs.load_topic(slug), parent=self)
        if slug != self._slug:
            self._slug = slug
            self._kicker.setText(topic.group.upper())
            self._browser.setDocument(self._docs[slug])
            self._list.blockSignals(True)
            self._list.setCurrentItem(self._items[slug])
            self._list.blockSignals(False)
            self.topicChanged.emit(slug)
        self._browser.verticalScrollBar().setValue(0)
        if anchor:
            self._browser.scrollToAnchor(anchor)

    def handle_link(self, url: QUrl) -> None:
        """Route a clicked link: help:// opens in place, http(s)/mailto externally."""
        if url.scheme() == HELP_SCHEME:
            try:
                self.open(*resolve_help_url(url))
            except ValueError:
                pass
        elif url.scheme() in ('http', 'https', 'mailto'):
            QDesktopServices.openUrl(url)

    def _on_list_changed(self, current: QListWidgetItem, _previous) -> None:
        slug = current.data(Qt.ItemDataRole.UserRole) if current else None
        if slug:
            self.open(slug)
