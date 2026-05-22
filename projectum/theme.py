"""Theme + stylesheet builder for Projectum.

Two themes ship: ``dark`` (Catppuccin Mocha-inspired) and ``light`` (Latte).
Custom-painted widgets read the colors as module-level attributes so they
pick up the current theme on the next paint event — call
:func:`apply_theme` then trigger ``widget.update()`` to repaint.

Stylesheet-using widgets need ``MainWindow.setStyleSheet(build_stylesheet())``
re-applied after a theme change, and any widget that bakes colors into its
own ``setStyleSheet`` during ``__init__`` must be re-built or re-styled.
"""

from __future__ import annotations

import hashlib


# ──────────────────────── theme definitions ────────────────────────


DARK_THEME = {
    "BG": "#11121b",
    "SURFACE": "#181927",
    "SURFACE_2": "#1f2033",
    "SURFACE_3": "#2a2c47",
    "BORDER": "#252741",
    "TEXT": "#dce0f0",
    "TEXT_DIM": "#9aa0c3",
    "TEXT_MUTED": "#5d6584",
    "ACCENT": "#c4a7ff",
    "ACCENT_HOVER": "#d4bdff",
    "ACCENT_2": "#f5b8e2",
    "SUCCESS": "#a6e3a1",
    "WARNING": "#f9e2af",
    "DANGER": "#f38ba8",
    "DANGER_HOVER": "#f5949f",
    "LIST_HOVER": "rgba(31, 32, 51, 0.55)",
    "INFO": "#89dceb",
}

LIGHT_THEME = {
    "BG": "#eff1f5",
    "SURFACE": "#e6e9ef",
    "SURFACE_2": "#dce0e8",
    "SURFACE_3": "#ccd0da",
    "BORDER": "#bcc0cc",
    "TEXT": "#4c4f69",
    "TEXT_DIM": "#6c6f85",
    "TEXT_MUTED": "#9ca0b0",
    "ACCENT": "#8839ef",
    "ACCENT_HOVER": "#7d34d9",
    "ACCENT_2": "#ea76cb",
    "SUCCESS": "#40a02b",
    "WARNING": "#df8e1d",
    "DANGER": "#d20f39",
    "DANGER_HOVER": "#c40d36",
    "LIST_HOVER": "rgba(220, 224, 232, 0.6)",
    "INFO": "#04a5e5",
}

NORD_THEME = {
    "BG": "#2e3440",
    "SURFACE": "#3b4252",
    "SURFACE_2": "#434c5e",
    "SURFACE_3": "#4c566a",
    "BORDER": "#4c566a",
    "TEXT": "#eceff4",
    "TEXT_DIM": "#d8dee9",
    "TEXT_MUTED": "#7b8794",
    "ACCENT": "#88c0d0",
    "ACCENT_HOVER": "#9bcfdc",
    "ACCENT_2": "#b48ead",
    "SUCCESS": "#a3be8c",
    "WARNING": "#ebcb8b",
    "DANGER": "#bf616a",
    "DANGER_HOVER": "#cb727a",
    "LIST_HOVER": "rgba(67, 76, 94, 0.55)",
    "INFO": "#88c0d0",
}

DRACULA_THEME = {
    "BG": "#282a36",
    "SURFACE": "#343746",
    "SURFACE_2": "#44475a",
    "SURFACE_3": "#565869",
    "BORDER": "#44475a",
    "TEXT": "#f8f8f2",
    "TEXT_DIM": "#bfbfbf",
    "TEXT_MUTED": "#6272a4",
    "ACCENT": "#bd93f9",
    "ACCENT_HOVER": "#c9a3fc",
    "ACCENT_2": "#ff79c6",
    "SUCCESS": "#50fa7b",
    "WARNING": "#f1fa8c",
    "DANGER": "#ff5555",
    "DANGER_HOVER": "#ff6e6e",
    "LIST_HOVER": "rgba(68, 71, 90, 0.55)",
    "INFO": "#8be9fd",
}

SOLARIZED_DARK_THEME = {
    "BG": "#002b36",
    "SURFACE": "#073642",
    "SURFACE_2": "#0e4452",
    "SURFACE_3": "#155563",
    "BORDER": "#0e4452",
    "TEXT": "#fdf6e3",
    "TEXT_DIM": "#eee8d5",
    "TEXT_MUTED": "#586e75",
    "ACCENT": "#268bd2",
    "ACCENT_HOVER": "#3a9be0",
    "ACCENT_2": "#d33682",
    "SUCCESS": "#859900",
    "WARNING": "#b58900",
    "DANGER": "#dc322f",
    "DANGER_HOVER": "#e64946",
    "LIST_HOVER": "rgba(14, 68, 82, 0.55)",
    "INFO": "#268bd2",
}

SOLARIZED_LIGHT_THEME = {
    "BG": "#fdf6e3",
    "SURFACE": "#eee8d5",
    "SURFACE_2": "#e7e1cf",
    "SURFACE_3": "#dad4c4",
    "BORDER": "#dad4c4",
    "TEXT": "#073642",
    "TEXT_DIM": "#586e75",
    "TEXT_MUTED": "#93a1a1",
    "ACCENT": "#268bd2",
    "ACCENT_HOVER": "#1e7bbf",
    "ACCENT_2": "#d33682",
    "SUCCESS": "#859900",
    "WARNING": "#b58900",
    "DANGER": "#dc322f",
    "DANGER_HOVER": "#c52e2c",
    "LIST_HOVER": "rgba(231, 225, 207, 0.55)",
    "INFO": "#268bd2",
}

GRUVBOX_DARK_THEME = {
    "BG": "#282828",
    "SURFACE": "#32302f",
    "SURFACE_2": "#3c3836",
    "SURFACE_3": "#504945",
    "BORDER": "#504945",
    "TEXT": "#ebdbb2",
    "TEXT_DIM": "#bdae93",
    "TEXT_MUTED": "#7c6f64",
    "ACCENT": "#fabd2f",
    "ACCENT_HOVER": "#fbc658",
    "ACCENT_2": "#d3869b",
    "SUCCESS": "#b8bb26",
    "WARNING": "#fe8019",
    "DANGER": "#fb4934",
    "DANGER_HOVER": "#fc6048",
    "LIST_HOVER": "rgba(60, 56, 54, 0.55)",
    "INFO": "#83a598",
}

TOKYO_NIGHT_THEME = {
    "BG": "#1a1b26",
    "SURFACE": "#1f2335",
    "SURFACE_2": "#24283b",
    "SURFACE_3": "#2f3549",
    "BORDER": "#2f3549",
    "TEXT": "#c0caf5",
    "TEXT_DIM": "#a9b1d6",
    "TEXT_MUTED": "#565f89",
    "ACCENT": "#7aa2f7",
    "ACCENT_HOVER": "#8db4f8",
    "ACCENT_2": "#bb9af7",
    "SUCCESS": "#9ece6a",
    "WARNING": "#e0af68",
    "DANGER": "#f7768e",
    "DANGER_HOVER": "#f88a9d",
    "LIST_HOVER": "rgba(36, 40, 59, 0.55)",
    "INFO": "#7dcfff",
}

ROSE_PINE_THEME = {
    "BG": "#191724",
    "SURFACE": "#1f1d2e",
    "SURFACE_2": "#26233a",
    "SURFACE_3": "#403d52",
    "BORDER": "#26233a",
    "TEXT": "#e0def4",
    "TEXT_DIM": "#908caa",
    "TEXT_MUTED": "#6e6a86",
    "ACCENT": "#c4a7e7",
    "ACCENT_HOVER": "#d4b9ec",
    "ACCENT_2": "#eb6f92",
    "SUCCESS": "#9ccfd8",
    "WARNING": "#f6c177",
    "DANGER": "#eb6f92",
    "DANGER_HOVER": "#ef829e",
    "LIST_HOVER": "rgba(38, 35, 58, 0.55)",
    "INFO": "#9ccfd8",
}

THEMES: dict[str, dict[str, str]] = {
    "dark": DARK_THEME,
    "light": LIGHT_THEME,
    "nord": NORD_THEME,
    "dracula": DRACULA_THEME,
    "tokyo_night": TOKYO_NIGHT_THEME,
    "rose_pine": ROSE_PINE_THEME,
    "gruvbox": GRUVBOX_DARK_THEME,
    "solarized_dark": SOLARIZED_DARK_THEME,
    "solarized_light": SOLARIZED_LIGHT_THEME,
}

# Display labels for the settings dropdown, in the order they should appear.
THEME_LABELS: list[tuple[str, str]] = [
    ("dark", "Catppuccin Mocha (Dark)"),
    ("light", "Catppuccin Latte (Light)"),
    ("nord", "Nord"),
    ("dracula", "Dracula"),
    ("tokyo_night", "Tokyo Night"),
    ("rose_pine", "Rosé Pine"),
    ("gruvbox", "Gruvbox Dark"),
    ("solarized_dark", "Solarized Dark"),
    ("solarized_light", "Solarized Light"),
]


DEFAULT_THEME = "dark"
DEFAULT_FONT_FAMILY = "Inter"  # widely available; user picks any installed family
DEFAULT_FONT_SIZE = 13
FONT_SIZE_MIN = 9
FONT_SIZE_MAX = 28


# Curated palette for per-tag chip colors (independent of theme).
TAG_PALETTE = [
    "#c4a7ff", "#f5b8e2", "#a6e3a1", "#f9e2af", "#f38ba8",
    "#89dceb", "#94e2d5", "#fab387", "#cba6f7", "#ffc6c2",
]


# ──────────────────────── current state ────────────────────────


# Module-level mirrors of the active theme. Custom paintEvents read these
# via `from . import theme` then `theme.ACCENT` etc., so they pick up the
# current values on the next repaint.
_current_theme_name = DEFAULT_THEME
_current_font_family = DEFAULT_FONT_FAMILY
_current_font_size = DEFAULT_FONT_SIZE

BG = DARK_THEME["BG"]
SURFACE = DARK_THEME["SURFACE"]
SURFACE_2 = DARK_THEME["SURFACE_2"]
SURFACE_3 = DARK_THEME["SURFACE_3"]
BORDER = DARK_THEME["BORDER"]
TEXT = DARK_THEME["TEXT"]
TEXT_DIM = DARK_THEME["TEXT_DIM"]
TEXT_MUTED = DARK_THEME["TEXT_MUTED"]
ACCENT = DARK_THEME["ACCENT"]
ACCENT_HOVER = DARK_THEME["ACCENT_HOVER"]
ACCENT_2 = DARK_THEME["ACCENT_2"]
SUCCESS = DARK_THEME["SUCCESS"]
WARNING = DARK_THEME["WARNING"]
DANGER = DARK_THEME["DANGER"]
DANGER_HOVER = DARK_THEME["DANGER_HOVER"]
LIST_HOVER = DARK_THEME["LIST_HOVER"]
INFO = DARK_THEME["INFO"]


def current_theme_name() -> str:
    return _current_theme_name


def current_font_family() -> str:
    return _current_font_family


def current_font_size() -> int:
    return _current_font_size


def theme_label(name: str) -> str:
    """Return the human-friendly label for a theme key."""
    for key, label in THEME_LABELS:
        if key == name:
            return label
    return name


def apply_theme(name: str) -> None:
    """Mutate the module-level color globals to a named theme."""
    global _current_theme_name
    global BG, SURFACE, SURFACE_2, SURFACE_3, BORDER
    global TEXT, TEXT_DIM, TEXT_MUTED
    global ACCENT, ACCENT_HOVER, ACCENT_2
    global SUCCESS, WARNING, DANGER, DANGER_HOVER, LIST_HOVER, INFO
    if name not in THEMES:
        name = DEFAULT_THEME
    t = THEMES[name]
    _current_theme_name = name
    BG = t["BG"]
    SURFACE = t["SURFACE"]
    SURFACE_2 = t["SURFACE_2"]
    SURFACE_3 = t["SURFACE_3"]
    BORDER = t["BORDER"]
    TEXT = t["TEXT"]
    TEXT_DIM = t["TEXT_DIM"]
    TEXT_MUTED = t["TEXT_MUTED"]
    ACCENT = t["ACCENT"]
    ACCENT_HOVER = t["ACCENT_HOVER"]
    ACCENT_2 = t["ACCENT_2"]
    SUCCESS = t["SUCCESS"]
    WARNING = t["WARNING"]
    DANGER = t["DANGER"]
    DANGER_HOVER = t["DANGER_HOVER"]
    LIST_HOVER = t["LIST_HOVER"]
    INFO = t["INFO"]


def apply_font(family: str | None = None, size: int | None = None) -> None:
    global _current_font_family, _current_font_size
    if family is not None:
        _current_font_family = family
    if size is not None:
        _current_font_size = int(size)


def tag_color(tag: str, overrides: dict | None = None) -> str:
    if overrides and tag in overrides:
        return overrides[tag]
    digest = hashlib.md5(tag.encode("utf-8")).digest()
    return TAG_PALETTE[digest[0] % len(TAG_PALETTE)]


# ──────────────────────── stylesheet builder ────────────────────────


def build_stylesheet() -> str:
    """Render the full stylesheet from the current theme + font settings.

    Note: font-family is NOT set on the universal selector — it's applied via
    ``QApplication.setFont`` so the chosen family actually propagates (Qt
    QSS font-family is inconsistent across widget types). Specific widgets
    that need a different family (the monospace path label) keep their own
    explicit ``font-family`` rule.
    """
    size = _current_font_size
    return f"""
* {{
    color: {TEXT};
}}

QWidget {{
    font-size: {size}px;
}}

QMainWindow, QWidget#root, QWidget#frameWrapper {{
    background-color: {BG};
}}

QWidget#sidebar {{
    background-color: {SURFACE};
    border-right: 1px solid {BORDER};
}}

QWidget#detailPanel, QWidget#detailEmpty {{
    background-color: {SURFACE};
}}

QWidget#topBar {{
    background-color: {SURFACE};
    border-bottom: 1px solid {BORDER};
}}

QWidget#welcome {{
    background-color: {BG};
}}

QLabel#welcomeTitle {{
    color: {TEXT};
    font-size: {size + 25}px;
    font-weight: 300;
}}

QLabel#welcomeSubtitle {{
    color: {ACCENT_2};
    font-size: {max(size - 2, 10)}px;
    font-weight: 700;
    letter-spacing: 6px;
}}

QLabel#welcomeHint {{
    color: {TEXT_DIM};
    font-size: {size + 1}px;
}}

QLabel#brandTag {{
    color: {TEXT_MUTED};
    font-size: {max(size - 3, 9)}px;
    font-weight: 600;
    letter-spacing: 2px;
}}

QPushButton {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 14px;
    color: {TEXT};
    font-size: {max(size - 1, 11)}px;
}}

QPushButton:hover {{
    background-color: {SURFACE_3};
    border-color: {SURFACE_3};
}}

QPushButton:pressed {{
    background-color: {SURFACE_2};
}}

QPushButton#primary {{
    background-color: {ACCENT};
    color: {BG};
    border: 1px solid {ACCENT};
    font-weight: 600;
    font-size: {size}px;
    padding: 10px 22px;
}}

QPushButton#primary:hover {{
    background-color: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}

QPushButton#ghost {{
    background-color: transparent;
    border: 1px dashed {BORDER};
    color: {TEXT_DIM};
    padding: 5px 12px;
    border-radius: 8px;
    font-size: {max(size - 2, 10)}px;
}}

QPushButton#ghost:hover {{
    color: {TEXT};
    border-color: {ACCENT};
    border-style: solid;
}}

QLineEdit, QTextEdit {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 9px 12px;
    color: {TEXT};
    font-size: {size}px;
    selection-background-color: {ACCENT};
    selection-color: {BG};
}}

QLineEdit:focus, QTextEdit:focus {{
    /* Quieter focus state — only the border lightens so writing in a
       notes pane doesn't flash the whole background brighter. */
    border-color: {TEXT_MUTED};
}}

QTextEdit#notes {{
    font-size: {size + 1}px;
    line-height: 1.5;
    padding: 14px 16px;
}}

QListWidget {{
    background-color: transparent;
    border: none;
    outline: none;
    padding: 4px 4px 4px 4px;
}}

QListWidget::item {{
    background-color: transparent;
    border: 1px solid transparent;
    border-left: 3px solid transparent;
    border-radius: 10px;
    margin: 1px 2px;
    padding: 0;
}}

QListWidget::item:selected {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER};
    border-left: 3px solid {ACCENT};
}}

QListWidget::item:hover:!selected {{
    background-color: {LIST_HOVER};
}}

QScrollBar:vertical {{
    background-color: transparent;
    width: 10px;
    margin: 4px 2px;
}}

QScrollBar::handle:vertical {{
    background-color: {SURFACE_3};
    border-radius: 4px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {TEXT_MUTED};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: none;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}

QScrollBar:horizontal {{
    background-color: transparent;
    height: 10px;
    margin: 2px 4px;
}}

QScrollBar::handle:horizontal {{
    background-color: {SURFACE_3};
    border-radius: 4px;
    min-width: 30px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {TEXT_MUTED};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
    background: none;
}}

QLabel#title {{
    font-size: {size + 9}px;
    font-weight: 600;
    color: {TEXT};
}}

QLabel#subtitle {{
    color: {TEXT_DIM};
    font-size: {max(size - 1, 11)}px;
}}

QLabel#sectionLabel {{
    color: {TEXT_MUTED};
    font-size: {max(size - 3, 9)}px;
    font-weight: 700;
    letter-spacing: 2px;
}}

QLabel#metaLabel {{
    color: {TEXT_DIM};
    font-size: {max(size - 1, 11)}px;
}}

QLabel#metaValue {{
    color: {TEXT};
    font-size: {size}px;
    font-weight: 600;
}}

QLabel#metaValueSuccess {{
    color: {SUCCESS};
    font-size: {size}px;
    font-weight: 600;
}}

QLabel#path {{
    color: {TEXT_MUTED};
    font-size: {max(size - 2, 10)}px;
    font-family: "JetBrains Mono", "Fira Code", "DejaVu Sans Mono", monospace;
}}

QLabel#emptyHint {{
    color: {TEXT_MUTED};
    font-size: {size}px;
}}

QPushButton#filterChip {{
    background-color: transparent;
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 4px 12px;
    color: {TEXT_DIM};
    font-size: {max(size - 2, 10)}px;
    font-weight: 600;
}}

QPushButton#filterChip:hover {{
    color: {TEXT};
    border-color: {SURFACE_3};
    background-color: {SURFACE_2};
}}

QPushButton#filterChip:checked {{
    background-color: {ACCENT};
    color: {BG};
    border-color: {ACCENT};
}}

QSplitter::handle {{
    background-color: {BORDER};
    width: 1px;
    height: 1px;
}}

QSplitter::handle:hover {{
    background-color: {ACCENT};
}}

QWidget#tabBar {{
    background-color: {SURFACE};
    border-bottom: 1px solid {BORDER};
}}

QPushButton#tab {{
    background-color: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    padding: 10px 18px;
    color: {TEXT_DIM};
    font-size: {max(size - 1, 11)}px;
    font-weight: 600;
}}

QPushButton#tab:hover {{
    color: {TEXT};
}}

QPushButton#tab:checked {{
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
}}

QProgressBar {{
    background-color: {SURFACE_2};
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: transparent;
}}

QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 3px;
}}

QLabel#videoListEmpty {{
    color: {TEXT_MUTED};
    font-size: {size}px;
}}

QLabel#errorMessage {{
    color: {DANGER};
    font-size: {max(size - 2, 10)}px;
    background: transparent;
}}

QMenu {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
}}

QMenu::item {{
    padding: 6px 18px 6px 14px;
    border-radius: 6px;
    color: {TEXT};
    font-size: {max(size - 1, 11)}px;
}}

QMenu::item:selected {{
    background-color: {ACCENT};
    color: {BG};
}}

QMenu::separator {{
    height: 1px;
    background-color: {BORDER};
    margin: 4px 6px;
}}

QToolTip {{
    background-color: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px 8px;
}}

QComboBox {{
    background-color: {SURFACE_2};
    border: 1.5px solid {BORDER};
    border-radius: 10px;
    padding: 9px 36px 9px 14px;
    color: {TEXT};
    font-size: {size}px;
    selection-background-color: {ACCENT};
    selection-color: {BG};
    min-height: 18px;
}}

QComboBox:hover {{
    border-color: {ACCENT};
    background-color: {SURFACE_3};
}}

QComboBox:focus, QComboBox:on {{
    border-color: {ACCENT};
    background-color: {SURFACE_3};
}}

QComboBox::drop-down {{
    border: none;
    width: 28px;
    subcontrol-position: top right;
}}

QComboBox QAbstractItemView {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 10px;
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: {BG};
    outline: 0;
    padding: 6px;
}}

QComboBox QAbstractItemView::item {{
    background-color: transparent;
    padding: 8px 14px;
    border-radius: 6px;
    color: {TEXT};
    min-height: 22px;
}}

QComboBox QAbstractItemView::item:selected {{
    background-color: {ACCENT};
    color: {BG};
}}

QComboBox QAbstractItemView::item:hover {{
    background-color: {SURFACE_3};
    color: {TEXT};
}}

QSpinBox, QDoubleSpinBox {{
    background-color: {SURFACE_2};
    border: 1.5px solid {BORDER};
    border-radius: 10px;
    padding: 9px 12px;
    color: {TEXT};
    font-size: {size}px;
    selection-background-color: {ACCENT};
    selection-color: {BG};
    min-height: 18px;
}}

QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {ACCENT};
    background-color: {SURFACE_3};
}}

QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {ACCENT};
    background-color: {SURFACE_3};
}}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background-color: transparent;
    border: none;
    width: 22px;
    margin: 1px;
}}

QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {SURFACE_3};
    border-radius: 4px;
}}

QInputDialog {{
    background-color: {SURFACE};
}}

QWidget#colorPickerPopup, QWidget#settingsDialog, QWidget#commandPalette {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}

QLineEdit#commandPaletteInput {{
    background-color: {SURFACE_2};
    border: 1.5px solid {BORDER};
    border-radius: 10px;
    padding: 12px 14px;
    color: {TEXT};
    font-size: {size + 2}px;
}}

QLineEdit#commandPaletteInput:focus {{
    border-color: {ACCENT};
    background-color: {SURFACE_3};
}}

QListWidget#commandPaletteList {{
    background-color: transparent;
    border: none;
    outline: none;
}}

QListWidget#commandPaletteList::item {{
    background-color: transparent;
    border-radius: 8px;
    padding: 10px 12px;
    margin: 2px 0;
    color: {TEXT};
}}

QListWidget#commandPaletteList::item:selected {{
    background-color: {ACCENT};
    color: {BG};
}}

QListWidget#commandPaletteList::item:hover:!selected {{
    background-color: {SURFACE_2};
}}

QFrame#settingsDivider {{
    background-color: {BORDER};
    max-height: 1px;
    border: none;
}}

QLabel#settingsTitle {{
    color: {TEXT};
    font-size: {size + 4}px;
    font-weight: 600;
}}

QPushButton#iconButton {{
    background-color: transparent;
    border: none;
    padding: 6px;
    border-radius: 8px;
}}

QPushButton#iconButton:hover {{
    background-color: {SURFACE_2};
}}
"""


# Backwards-compat alias for existing callers (`from .theme import STYLESHEET`).
# Note: this captures the *initial* (dark) stylesheet; live theme changes must
# call build_stylesheet() and re-apply via MainWindow.setStyleSheet().
STYLESHEET = build_stylesheet()
