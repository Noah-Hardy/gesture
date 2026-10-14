#!/usr/bin/env python3
"""
Design Tokens

The single source of truth for the Gesture interface: palette, radii, spacing,
fonts, and the generated application stylesheet. Widgets read colours from
here (never hard-code hex), and `build_stylesheet()` renders the QSS from the
same values at startup, so a palette change is one edit.

Seeded from the Claude Design handoff ("Gesture Interface"); the palette below
is authoritative.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QLinearGradient, QRadialGradient

# ============================================================================
# PALETTE
# ============================================================================
PALETTE = {
    'bg_deep':   '#0B0D11',   # focus-ring gap, deepest ground
    'bg_window': '#1A1D24',   # window ground
    'panel':     '#1F232B',   # card top (gradient to panel_end)
    'panel_end': '#1B1E25',   # card bottom
    'rail':      '#171A20',   # navigation rail
    'stage':     '#101318',   # preview stage centre (radial to stage_end)
    'stage_end': '#1C2028',
    'console':   '#141720',
    'field':     '#171A20',   # inputs, trough
    'row_alt':   '#1C2027',   # zebra rows
    'header':    '#1F232B',   # table header row
    'border':    '#2B3038',
    'border_2':  '#3A404C',
    'hairline':  '#22262F',
    'text':      '#E6E8EC',
    'text_2':    '#C3C7CF',
    'dim':       '#9AA1AD',
    'faint':     '#6B7280',
    'accent':    '#3DDC84',
    'accent_2':  '#2F7856',
    'warn':      '#E5A54B',
    'error':     '#E5695B',
    'on_accent': '#0B120D',   # text/knob on an accent fill
}


def color(name: str, alpha: float | None = None) -> QColor:
    """
    Resolve a palette key (or a raw '#RRGGBB') to a QColor.

    Args:
        name: Palette key such as 'accent', or a hex string.
        alpha: Optional opacity 0..1 applied on top.

    Returns:
        The QColor.
    """
    c = QColor(PALETTE.get(name, name))
    if alpha is not None:
        c.setAlphaF(alpha)
    return c


def panel_gradient(height: float) -> QLinearGradient:
    """Vertical card gradient (panel -> panel_end) spanning `height` pixels."""
    g = QLinearGradient(0, 0, 0, height)
    g.setColorAt(0, color('panel'))
    g.setColorAt(1, color('panel_end'))
    return g


def stage_gradient(cx: float, cy: float, radius: float) -> QRadialGradient:
    """Radial preview-stage gradient (stage centre -> stage_end edge)."""
    g = QRadialGradient(cx, cy, radius)
    g.setColorAt(0, color('stage'))
    g.setColorAt(1, color('stage_end'))
    return g


# ============================================================================
# METRICS
# ============================================================================
class Radius:
    """Corner radii in px (the design uses 7-14)."""
    CHIP = 5
    OPTION = 7
    CONTROL = 8
    TROUGH = 9
    CARD = 10
    WINDOW = 14


class Spacing:
    """Layout metrics in px."""
    ROW_HEIGHT = 30
    LABEL_WIDTH = 250
    ROW_PAD = 9          # vertical row padding in the design
    ROW_GAP = 16         # label column -> control
    MARKER_GUTTER = 11   # dirty marker sits this far left of the label text
    FOCUS_GAP = 2        # bg_deep gap between control and ring
    FOCUS_RING = 2       # accent ring width
    FOCUS_MARGIN = 4     # space every focusable widget reserves for the ring
    CONTROL_WIDTH = 268  # Select / wide text field
    SLIDER_MAX = 340


class Font:
    """Font helpers. Pixel sizes, Inter when installed, system font otherwise."""

    UI_FAMILY = 'Inter'
    MONO_FAMILY = 'Menlo'
    _ui_family: str | None = None
    _mono_family: str | None = None

    @classmethod
    def ui(cls, px: float = 13, weight: int = 400) -> QFont:
        """
        UI font (11-13px typical).

        Args:
            px: Pixel size.
            weight: CSS-style weight (400 regular, 500 medium).

        Returns:
            A QFont with tabular numerals enabled where Qt supports it.
        """
        if cls._ui_family is None:
            fams = QFontDatabase.families()
            cls._ui_family = cls.UI_FAMILY if cls.UI_FAMILY in fams else (
                QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family())
        return cls._make(cls._ui_family, px, weight)

    @classmethod
    def mono(cls, px: float = 11, weight: int = 400) -> QFont:
        """Monospace font (Menlo, else the system fixed font) for addresses and logs."""
        if cls._mono_family is None:
            fams = QFontDatabase.families()
            cls._mono_family = cls.MONO_FAMILY if cls.MONO_FAMILY in fams else (
                QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family())
        return cls._make(cls._mono_family, px, weight)

    @staticmethod
    def _make(family: str, px: float, weight: int) -> QFont:
        f = QFont(family)
        f.setPixelSize(int(round(px)))
        f.setWeight(QFont.Weight(max(1, min(1000, weight))))
        try:  # Qt >= 6.7: OpenType feature tags (tabular numerals)
            f.setFeature(QFont.Tag('tnum'), 1)
        except (AttributeError, TypeError):
            pass
        return f


# ============================================================================
# STYLESHEET
# ============================================================================
def build_stylesheet() -> str:
    """
    Render the application stylesheet from the tokens.

    Custom widgets paint themselves; this covers the stock Qt widgets the app
    still uses (menus, scroll bars, tooltips, text editors, dialogs).

    Returns:
        QSS text. Not committed to disk: generated at startup.
    """
    p = PALETTE
    return f"""
QWidget {{
    color: {p['text']};
    font-size: 13px;
    selection-background-color: {p['accent']};
    selection-color: {p['on_accent']};
}}
QMainWindow, QDialog {{ background: {p['bg_window']}; }}
QToolTip {{
    background: {p['panel']}; color: {p['text']};
    border: 1px solid {p['border_2']}; border-radius: {Radius.CHIP}px; padding: 4px 6px;
}}
QLineEdit {{
    background: {p['field']}; color: {p['text']};
    border: 1px solid {p['border_2']}; border-radius: {Radius.CONTROL}px;
    padding: 0 10px; min-height: {Spacing.ROW_HEIGHT - 2}px;
}}
QPlainTextEdit, QTextEdit, QTextBrowser {{
    background: {p['console']}; color: {p['text_2']};
    border: 1px solid {p['border']}; border-radius: {Radius.TROUGH}px;
}}
QMenu {{
    background: {p['panel']}; border: 1px solid {p['border_2']};
    border-radius: {Radius.TROUGH}px; padding: 4px;
}}
QMenu::item {{ padding: 6px 18px 6px 12px; border-radius: {Radius.OPTION - 2}px; }}
QMenu::item:selected {{ background: {p['row_alt']}; color: {p['accent']}; }}
QMenu::item:disabled {{ color: {p['faint']}; }}
QMenu::separator {{ height: 1px; background: {p['hairline']}; margin: 4px 6px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p['border_2']}; border-radius: 3px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {p['faint']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p['border_2']}; border-radius: 3px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {p['faint']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def apply(app) -> None:
    """Install the UI font and generated stylesheet on a QApplication."""
    app.setFont(Font.ui(13))
    app.setStyleSheet(build_stylesheet())
