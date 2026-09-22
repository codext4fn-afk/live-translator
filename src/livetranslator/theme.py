"""Colours, type, icons and the Qt stylesheet."""

from __future__ import annotations

from pathlib import Path

# Low alphas on purpose: the acrylic backdrop supplies the depth, so these only tint.
GLASS_BASE = "rgba(28, 28, 34, 0.55)"      # window fill over the blur
GLASS_PANEL = "rgba(255, 255, 255, 0.055)"  # cards
GLASS_PANEL_HI = "rgba(255, 255, 255, 0.085)"
GLASS_CONTROL = "rgba(255, 255, 255, 0.085)"
GLASS_CONTROL_HI = "rgba(255, 255, 255, 0.145)"
GLASS_SUNKEN = "rgba(0, 0, 0, 0.22)"        # inputs read as recessed

# Hairlines.
EDGE = "rgba(255, 255, 255, 0.12)"
EDGE_HI = "rgba(255, 255, 255, 0.22)"
EDGE_SOFT = "rgba(255, 255, 255, 0.07)"

LABEL = "#FFFFFF"
LABEL_2 = "rgba(235, 235, 245, 0.68)"
LABEL_3 = "rgba(235, 235, 245, 0.42)"

# The interface is deliberately neutral; these are the only accents in use.
BLUE = "#0A84FF"
BLUE_HI = "#4DA6FF"
GREEN = "#30D158"
RED = "#FF453A"
ORANGE = "#FF9F0A"

def tint(hex_colour: str, alpha: float) -> str:
    """Convert #RRGGBB to an rgba() string at the given alpha."""
    value = hex_colour.lstrip("#")
    if len(value) != 6:
        return hex_colour
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


# Three stops read top-left to bottom-right.
GRADIENTS: dict[str, tuple[str, str, str]] = {
    "graphite": ("rgba(44, 44, 54, 0.90)", "rgba(30, 30, 38, 0.90)",
                 "rgba(22, 22, 28, 0.92)"),
    "midnight": ("rgba(32, 44, 74, 0.90)", "rgba(22, 30, 52, 0.91)",
                 "rgba(15, 19, 34, 0.93)"),
    "plum":     ("rgba(56, 36, 68, 0.90)", "rgba(38, 25, 48, 0.91)",
                 "rgba(24, 17, 31, 0.93)"),
    "forest":   ("rgba(30, 52, 44, 0.90)", "rgba(21, 36, 31, 0.91)",
                 "rgba(14, 24, 21, 0.93)"),
    "ember":    ("rgba(64, 40, 34, 0.90)", "rgba(44, 27, 24, 0.91)",
                 "rgba(28, 18, 16, 0.93)"),
    "slate":    ("rgba(46, 52, 60, 0.90)", "rgba(32, 37, 44, 0.91)",
                 "rgba(20, 24, 29, 0.93)"),
    "ash":      ("rgba(92, 92, 100, 0.88)", "rgba(58, 58, 65, 0.90)",
                 "rgba(32, 32, 38, 0.92)"),
    "ink":      ("rgba(26, 26, 30, 0.92)", "rgba(16, 16, 19, 0.93)",
                 "rgba(8, 8, 10, 0.94)"),
}

DEFAULT_GRADIENT = "graphite"

# Human-readable order for the settings panel.
GRADIENT_ORDER = ("graphite", "slate", "ink", "ash", "midnight", "plum", "forest",
                  "ember")


def gradient_stops(name: str) -> tuple[str, str, str]:
    return GRADIENTS.get(name, GRADIENTS[DEFAULT_GRADIENT])


# Generous radii throughout: the reference language rounds far more than a typical desktop.
R_WINDOW = 28
R_CARD = 24
R_CONTROL = 18
R_PILL = 22

# Inter ships in assets/fonts and loads at startup, so this resolves everywhere.
FONT_STACK = (
    '"Inter Variable Text", "Inter Variable", "Inter", '
    '"Segoe UI Variable Text", "Segoe UI", sans-serif'
)
# Windows 11's icon font.
ICON_FONT = '"Segoe Fluent Icons", "Segoe MDL2 Assets"'


class Icon:
    """Segoe Fluent Icons code points, named for what they do here."""

    PAUSE = ""
    PLAY = ""
    # Delete (a bin) rather than Clear, whose glyph is an X in a box.
    CLEAR = ""
    OPACITY_UP = ""     # Brightness
    OPACITY_DOWN = ""   # QuietHours
    CLOSE = ""
    SEND = ""
    COPY = ""
    MIC = ""
    GLOBE = ""
    SPARKLE = ""        # Lightbulb-ish: a suggestion
    CHEVRON = ""
    SETTINGS = ""
    PIN = ""
    UNPIN = ""
    WARNING = ""


def stylesheet(font_size: int, font_family: str = "",
               gradient: str = DEFAULT_GRADIENT) -> str:
    """Build the application stylesheet at the configured base size."""
    fs = font_size
    family = f'"{font_family}", {FONT_STACK}' if font_family else FONT_STACK
    g0, g1, g2 = gradient_stops(gradient)

    return f"""
    * {{
        font-family: {family};
        font-size: {fs}pt;
        color: {LABEL};
        outline: none;
    }}

    /* ------------------------------------------------------------ window shell */
    /* The window's glass is painted here rather than by the compositor, so the corners
       outside this radius stay fully transparent. A vertical gradient plus the sheen
       strip below does the work that a real blur would otherwise do. */
    /* No border: an outline traced the rounded edge and read as a grey ring around
       the window. The gradient's own top stop gives it enough definition. */
    #root {{
        background: qlineargradient(x1:0, y1:0, x2:0.35, y2:1,
            stop:0 {g0},
            stop:0.55 {g1},
            stop:1 {g2});
        border-radius: {R_WINDOW}px;
        border: none;
    }}
    /* Thin bright line just inside the top edge: the sheen on a pane of glass. */
    #sheen {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 rgba(255,255,255,0.20), stop:1 rgba(255,255,255,0.0));
        border-top-left-radius: {R_WINDOW}px;
        border-top-right-radius: {R_WINDOW}px;
    }}

    /* The bar lights up while the window is being carried, so it is obvious the drag
       has taken hold - the `dragging` property is toggled from the drag handlers. */
    #titleBar {{
        background: transparent;
        border-radius: {R_CONTROL}px;
    }}
    #titleBar[dragging="true"] {{
        background-color: rgba(255, 255, 255, 0.10);
        border: 1px solid {EDGE_HI};
    }}
    #title {{ font-size: {fs}pt; font-weight: 600; color: {LABEL}; }}
    #subtitle {{ font-size: {fs - 3}pt; color: {LABEL_3}; }}

    /* --------------------------------------------------------- chrome buttons */
    /* Rounded squares, not circles - square footprint with a soft radius, which is
       what the reference control-centre tiles use. */
    #chrome {{
        font-family: {ICON_FONT};
        font-size: {fs}pt;
        background-color: {GLASS_CONTROL};
        border: 1px solid {EDGE_SOFT};
        border-radius: 11px;
        min-width: 32px; max-width: 32px;
        min-height: 32px; max-height: 32px;
        color: {LABEL_2};
        padding: 0px;
    }}
    #chrome:hover {{
        background-color: {GLASS_CONTROL_HI};
        border: 1px solid {EDGE_HI};
        color: {LABEL};
    }}
    #chrome:pressed {{ background-color: {tint(BLUE, 0.55)}; color: white; }}

    /* The close button carries its own objectName, which REPLACES #chrome rather than
       adding to it - so it has to restate the whole base or it renders as a bare glyph
       with no rounded square behind it. */
    #chromeClose {{
        font-family: {ICON_FONT};
        font-size: {fs}pt;
        background-color: {GLASS_CONTROL};
        border: 1px solid {EDGE_SOFT};
        border-radius: 11px;
        min-width: 32px; max-width: 32px;
        min-height: 32px; max-height: 32px;
        color: {LABEL_2};
        padding: 0px;
    }}
    #chromeClose:hover {{
        background-color: {tint(RED, 0.80)};
        border: 1px solid {tint(RED, 0.85)};
        color: white;
    }}
    #chromeActive {{
        font-family: {ICON_FONT};
        font-size: {fs}pt;
        background-color: {tint(ORANGE, 0.85)};
        border: 1px solid {tint(ORANGE, 0.9)};
        border-radius: 11px;
        min-width: 32px; max-width: 32px;
        min-height: 32px; max-height: 32px;
        color: #1C1C1E;
    }}

    /* ------------------------------------------------------------------ cards */
    /* Cards are their own pane of glass: a vertical wash brightest at the top, so
       each one catches light rather than sitting flat on the window. */
    #card {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 rgba(255,255,255,0.105),
            stop:0.45 rgba(255,255,255,0.058),
            stop:1 rgba(255,255,255,0.040));
        border-radius: {R_CARD}px;
        border: 1px solid {EDGE};
    }}
    #card:hover {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 rgba(255,255,255,0.145),
            stop:0.45 rgba(255,255,255,0.085),
            stop:1 rgba(255,255,255,0.060));
        border: 1px solid {EDGE_HI};
    }}

    /* Neutral label, no chip and no colour. Language is identified by its name right
       beside it, so tinting the code as well was decoration rather than information. */
    #langBadge {{
        background: transparent;
        border: none;
        padding: 0px;
        color: {LABEL_2};
        font-size: {fs - 1}pt;
        font-weight: 700;
        letter-spacing: 0.5px;
    }}
    #langName {{ color: {LABEL_3}; font-size: {fs - 1}pt; }}
    #meta {{ color: {LABEL_3}; font-size: {fs - 1}pt; }}
    #original {{ color: {LABEL_2}; font-size: {fs}pt; }}
    #originalPrimary {{ color: {LABEL}; font-size: {fs + 4}pt; font-weight: 600; }}
    #translation {{ color: {LABEL}; font-size: {fs + 4}pt; font-weight: 600; }}
    #pending {{ color: {LABEL_3}; font-size: {fs}pt; }}
    #errorText {{ color: {RED}; font-size: {fs}pt; }}

    /* ---------------------------------------------------------------- replies */
    #replyRow {{
        background-color: {GLASS_CONTROL};
        border-radius: {R_CONTROL}px;
        border: 1px solid transparent;
    }}
    #replyRow:hover {{
        background-color: {GLASS_CONTROL_HI};
        border: 1px solid {EDGE_HI};
    }}
    #replyIndex {{
        color: {LABEL_3};
        font-size: {fs - 1}pt;
        font-weight: 700;
        min-width: 15px;
    }}
    #replyText {{ color: {LABEL}; font-size: {fs + 1}pt; font-weight: 600; }}
    /* Pronunciation sits between the two: more prominent than the gloss because it is
       what you actually read aloud, quieter than the text it describes. */
    #replySay {{ color: {LABEL_2}; font-size: {fs - 1}pt; font-style: italic; }}
    #replyGloss {{ color: {LABEL_3}; font-size: {fs - 1}pt; }}
    #copyIcon {{ font-family: {ICON_FONT}; font-size: {fs - 1}pt; color: {LABEL_3}; }}

    /* ---------------------------------------------------------------- compose */
    #composeBar {{
        background-color: {GLASS_PANEL};
        border-radius: {R_CARD}px;
        border: 1px solid {EDGE_SOFT};
    }}
    /* The compose row is three true pills: radius is half the control height, so the
       ends are semicircles rather than rounded corners. */
    #composeInput {{
        background-color: {GLASS_SUNKEN};
        border: 1px solid {EDGE_SOFT};
        border-radius: 21px;
        min-height: 40px; max-height: 40px;
        padding: 0px 18px;
        color: {LABEL};
        selection-background-color: {BLUE};
    }}
    #composeInput:focus {{
        border: 1px solid {tint(BLUE, 0.8)};
        background-color: rgba(10, 132, 255, 0.10);
    }}
    #langPicker {{
        background-color: {GLASS_CONTROL};
        border: 1px solid {EDGE_SOFT};
        border-radius: 21px;
        min-height: 40px; max-height: 40px;
        padding: 0px 14px 0px 18px;
        color: {LABEL_2};
        font-size: {fs - 1}pt;
        font-weight: 600;
        /* Wide enough for "Auto · Portuguese" without eliding. */
        min-width: 150px;
    }}
    #langPicker:hover {{ background-color: {GLASS_CONTROL_HI}; color: {LABEL}; }}
    #langPicker::drop-down {{ border: none; width: 18px; }}
    #langPicker::down-arrow {{ image: none; }}
    #langPicker QAbstractItemView {{
        background-color: #24242A;
        border: 1px solid {EDGE_HI};
        border-radius: 12px;
        padding: 5px;
        selection-background-color: {BLUE};
        color: {LABEL};
    }}
    /* A true circle: radius is exactly half of a square 40px button. */
    #sendButton {{
        font-family: {ICON_FONT};
        background-color: {BLUE};
        border: 1px solid {tint(BLUE_HI, 0.5)};
        border-radius: 20px;
        min-width: 40px; max-width: 40px;
        min-height: 40px; max-height: 40px;
        color: white;
        font-size: {fs}pt;
    }}
    #sendButton:hover {{ background-color: {BLUE_HI}; }}
    #sendButton:pressed {{ background-color: #0060DF; }}
    #sendButton:disabled {{ background-color: {GLASS_CONTROL}; color: {LABEL_3}; }}

    /* No green wash and no coloured outlines: the colour belongs on the text itself.
       The panel is the same neutral glass as everything else. */
    #outgoingPanel {{
        background-color: {GLASS_CONTROL};
        border: 1px solid {EDGE_SOFT};
        border-radius: {R_CONTROL}px;
    }}
    #outgoingPrimary {{ color: {LABEL}; font-size: {fs + 2}pt; font-weight: 600; }}
    #outgoingSay {{ color: {LABEL_2}; font-size: {fs - 1}pt; font-style: italic; }}
    #outgoingAlt {{ color: {LABEL_2}; font-size: {fs}pt; }}
    #outgoingNote {{ color: {ORANGE}; font-size: {fs - 1}pt; }}
    #outgoingEcho {{ color: {LABEL_3}; font-size: {fs - 1}pt; }}
    #outgoingRow {{
        border-radius: 12px;
        border: none;
        background: transparent;
    }}
    #outgoingRow:hover {{ background-color: rgba(255, 255, 255, 0.07); }}
    #dismiss {{
        font-family: {ICON_FONT};
        font-size: {fs - 3}pt;
        background: transparent;
        border: none;
        border-radius: 9px;
        min-width: 18px; max-width: 18px;
        min-height: 18px; max-height: 18px;
        color: {LABEL_3};
        padding: 0px;
    }}
    /* Neutral, not red. This dismisses a result; it does not destroy anything. */
    #dismiss:hover {{
        background-color: rgba(255, 255, 255, 0.16);
        color: {LABEL};
    }}

    /* --------------------------------------------------------------- settings */
    #settingsPanel {{
        background-color: rgba(30, 30, 38, 0.97);
        border: 1px solid {EDGE_HI};
        border-radius: {R_CARD}px;
    }}
    #settingsTitle {{
        color: {LABEL};
        font-size: {fs}pt;
        font-weight: 600;
    }}
    #settingsHint {{ color: {LABEL_3}; font-size: {fs - 2}pt; }}
    #swatchName {{ color: {LABEL_2}; font-size: {fs - 2}pt; font-weight: 600; }}
    #swatchNameActive {{ color: {LABEL}; font-size: {fs - 2}pt; font-weight: 700; }}
    #swatch {{
        border-radius: 14px;
        border: 1px solid {EDGE};
    }}
    #swatch:hover {{ border: 1px solid {EDGE_HI}; }}
    #swatchActive {{
        border-radius: 14px;
        border: 2px solid {BLUE};
    }}

    /* ----------------------------------------------------------------- status */
    #statusIcon {{ font-family: {ICON_FONT}; font-size: {fs + 1}pt; }}
    /* Plain grey text in the title bar. No pill, no outline, no colour. */
    #toast {{
        color: {LABEL_2};
        font-size: {fs - 1}pt;
        font-weight: 600;
        background: transparent;
        border: none;
        padding: 0px;
    }}
    #empty {{ color: {LABEL_3}; font-size: {fs + 1}pt; }}
    #emptyIcon {{ font-family: {ICON_FONT}; font-size: {fs + 20}pt; color: {LABEL_3}; }}

    /* -------------------------------------------------------------- scrollbar */
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px 1px; }}
    QScrollBar::handle:vertical {{
        background: rgba(255, 255, 255, 0.18);
        border-radius: 4px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: rgba(255, 255, 255, 0.32); }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}

    QToolTip {{
        background-color: #24242A;
        color: {LABEL};
        border: 1px solid {EDGE_HI};
        border-radius: 8px;
        padding: 5px 9px;
    }}
    """


def load_bundled_fonts() -> list[str]:
    """Register the fonts in assets/fonts so no installation is needed."""
    from PySide6.QtGui import QFontDatabase

    families: list[str] = []
    folder = Path(__file__).resolve().parents[2] / "assets" / "fonts"
    if not folder.is_dir():
        return families

    for path in sorted(folder.glob("*.tt[fc]")) + sorted(folder.glob("*.otf")):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id != -1:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    return families
