"""Main window for Projectum."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    Qt, QTimer, QSize, QThreadPool, QFileSystemWatcher, QObject, QEvent,
)
from PySide6.QtGui import QFont, QIcon, QKeySequence, QShortcut, QAction
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QFileDialog, QSplitter, QListWidget, QListWidgetItem,
    QLineEdit, QTextEdit, QPlainTextEdit, QButtonGroup, QStackedWidget,
    QMessageBox, QMenu, QCompleter, QProgressBar,
)
from PySide6.QtGui import QColor, QCursor, QDesktopServices
from PySide6.QtCore import QUrl
from functools import partial

from .store import Playlist, Project, ProjectStore, Video
from . import theme
from .theme import tag_color
from .anims import (
    animate_progress, collapse_list_item, cross_fade_stack, fade_in,
    fade_out, fade_window, slide_in_height, slide_out_height,
)
from .widgets import (
    BrandMark, ColorPickerPopup, CommandPalette, CompletionToggle, FlowLayout,
    FrameWrapper, IconButton, PlaylistRow, ProjectRow, SettingsDialog,
    SizeRunnable, TagChip, TagEditor, TitleBar, VideoRow, WindowControlButton,
    make_markdown_pane,
)
from .youtube import PlaylistFetchRunnable


class FocusManager(QObject):
    """App-wide event filter: clicking outside a focused text input blurs it."""

    def eventFilter(self, _obj, event) -> bool:
        if event.type() != QEvent.Type.MouseButtonPress:
            return False
        app = QApplication.instance()
        if app is None:
            return False
        focused = app.focusWidget()
        if not isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return False
        try:
            gp = event.globalPosition().toPoint()
        except AttributeError:
            gp = event.globalPos()
        widget_at = app.widgetAt(gp)
        # While the inline TagEditor is open, clicks on a sibling TagChip
        # (color picker right-click, remove-button left-click) are part of
        # the same edit session — clearing focus would close the editor
        # before the chip's own handler runs.
        from .widgets import TagChip, TagEditor
        editor_open = isinstance(focused, TagEditor)
        cur = widget_at
        while cur is not None:
            if cur is focused:
                return False
            if editor_open and isinstance(cur, TagChip):
                return False
            cur = cur.parent()
        focused.clearFocus()
        return False


ICON_PATH = Path(__file__).parent / "assets" / "icon.svg"


def state_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "projectum"


def state_path() -> Path:
    return state_dir() / "state.json"


def load_state() -> dict:
    p = state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(data: dict) -> None:
    p = state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Projectum")
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        )
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1180, 760)
        self.setMinimumSize(940, 580)

        self.store: ProjectStore | None = None
        self.current_filter = "all"
        self.search_query = ""
        self.tag_filter: str | None = None
        self.current_project: Project | None = None
        self._loading_details = False
        self._size_pool = QThreadPool.globalInstance()
        self._size_pending_for: str | None = None
        self._row_items: dict[str, QListWidgetItem] = {}
        self._tag_editor: TagEditor | None = None

        # Playlists state
        self.current_tab: str = "projects"
        self.current_playlist: Playlist | None = None
        self.current_video: Video | None = None
        self._loading_video_details = False
        self._playlist_items: dict[str, QListWidgetItem] = {}
        self._video_items: dict[str, QListWidgetItem] = {}
        self._playlist_url_input: QLineEdit | None = None
        self._playlist_tag_editor: TagEditor | None = None
        self._pending_fetches: dict[str, tuple[object, object]] = {}
        self._video_notes_save_timer = QTimer(self)
        self._video_notes_save_timer.setSingleShot(True)
        self._video_notes_save_timer.setInterval(450)
        self._video_notes_save_timer.timeout.connect(self._save_video_notes)

        self._playlist_notes_save_timer = QTimer(self)
        self._playlist_notes_save_timer.setSingleShot(True)
        self._playlist_notes_save_timer.setInterval(450)
        self._playlist_notes_save_timer.timeout.connect(self._save_playlist_notes)
        self._loading_playlist_details = False

        self._global_notes_save_timer = QTimer(self)
        self._global_notes_save_timer.setSingleShot(True)
        self._global_notes_save_timer.setInterval(450)
        self._global_notes_save_timer.timeout.connect(self._save_global_notes)
        self._loading_global_notes = False
        # Guards re-entrant textChanged firing when we modify char formats
        # to highlight search matches.
        self._highlighting_notes = False

        self._notes_save_timer = QTimer(self)
        self._notes_save_timer.setSingleShot(True)
        self._notes_save_timer.setInterval(450)
        self._notes_save_timer.timeout.connect(self._save_notes)

        self._watcher_debounce = QTimer(self)
        self._watcher_debounce.setSingleShot(True)
        self._watcher_debounce.setInterval(300)
        self._watcher_debounce.timeout.connect(self.refresh)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_watcher_changed)

        # Settings dialog instance lives across opens to preserve combo focus
        # state; we hold one weak slot.
        self._settings_dialog: SettingsDialog | None = None
        self._command_palette: CommandPalette | None = None

        self._build_ui()
        # Stylesheet applied at QApplication level in run() so popups and
        # the settings dialog inherit. Theme changes call apply_app_styling.
        self._bind_shortcuts()

    # ─── UI construction ─────────────────────────────────────────

    def _build_ui(self) -> None:
        self._frame_wrapper = FrameWrapper()
        self.setCentralWidget(self._frame_wrapper)

        root = QWidget()
        root.setObjectName("root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_top_bar())

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.welcome = self._build_welcome()
        self.stack.addWidget(self.welcome)

        self.main_view = self._build_main_view()
        self.stack.addWidget(self.main_view)

        self.stack.setCurrentWidget(self.welcome)

        self._frame_wrapper.set_content(root)

    def _build_top_bar(self) -> QWidget:
        bar = TitleBar()
        bar.setFixedHeight(46)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 0, 0)
        h.setSpacing(10)

        # Custom-painted W in the current accent color (theme-reactive,
        # replaces the static SVG so it follows the active theme).
        self._brand_mark = BrandMark(size=24)
        h.addWidget(self._brand_mark, alignment=Qt.AlignmentFlag.AlignVCenter)

        brand = QLabel("Projectum")
        bf = QFont()
        bf.setPointSize(14)
        bf.setWeight(QFont.Weight.DemiBold)
        brand.setFont(bf)
        h.addWidget(brand)

        by = QLabel("by wleeaf")
        by.setObjectName("brandTag")
        h.addWidget(by)

        h.addStretch()

        self.folder_label = QLabel("")
        self.folder_label.setObjectName("path")
        self.folder_label.setMaximumWidth(380)
        h.addWidget(self.folder_label)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("subtitle")
        h.addWidget(self.stats_label)

        self.open_btn = QPushButton("Open folder")
        self.open_btn.clicked.connect(self.choose_folder)
        h.addWidget(self.open_btn)

        self._settings_btn = IconButton("gear", size=30)
        self._settings_btn.setToolTip("Settings")
        self._settings_btn.clicked.connect(self._open_settings)
        h.addWidget(self._settings_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        # 12px breathing room before window controls
        sep = QWidget()
        sep.setFixedWidth(12)
        h.addWidget(sep)

        self._min_btn = WindowControlButton("min")
        self._min_btn.clicked.connect(self.showMinimized)
        h.addWidget(self._min_btn)

        self._max_btn = WindowControlButton("max")
        self._max_btn.clicked.connect(self._toggle_maximize)
        h.addWidget(self._max_btn)

        self._close_btn = WindowControlButton("close")
        self._close_btn.clicked.connect(self.close)
        h.addWidget(self._close_btn)

        return bar

    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _build_welcome(self) -> QWidget:
        w = QWidget()
        w.setObjectName("welcome")
        v = QVBoxLayout(w)
        v.setContentsMargins(40, 40, 40, 40)
        v.setSpacing(0)
        v.addStretch()

        sub = QLabel("PROJECTUM")
        sub.setObjectName("welcomeSubtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(sub)
        v.addSpacing(18)

        title = QLabel("Track every project in one place.")
        title.setObjectName("welcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(title)
        v.addSpacing(14)

        hint = QLabel(
            "Point Projectum at a folder. Every subfolder becomes a project."
        )
        hint.setObjectName("welcomeHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(hint)
        v.addSpacing(40)

        btn = QPushButton("Choose a folder…")
        btn.setObjectName("primary")
        btn.setMinimumWidth(220)
        btn.clicked.connect(self.choose_folder)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(btn)
        row.addStretch()
        v.addLayout(row)
        v.addStretch()

        foot = QLabel("MADE BY WLEEAF")
        foot.setObjectName("brandTag")
        foot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(foot)

        return w

    def _build_main_view(self) -> QWidget:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        v.addWidget(self._build_tab_bar())

        self.content_stack = QStackedWidget()
        self.projects_view = self._build_projects_view()
        self.playlists_view = self._build_playlists_view()
        self.notes_view = self._build_notes_view()
        self.content_stack.addWidget(self.projects_view)
        self.content_stack.addWidget(self.playlists_view)
        self.content_stack.addWidget(self.notes_view)
        v.addWidget(self.content_stack, 1)

        return container

    def _build_tab_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("tabBar")
        bar.setFixedHeight(40)
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 0, 14, 0)
        h.setSpacing(2)

        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)

        for key, label in [
            ("projects", "Projects"),
            ("playlists", "Playlists"),
            ("notes", "Notes"),
        ]:
            b = QPushButton(label)
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setProperty("tab_key", key)
            if key == "projects":
                b.setChecked(True)
            b.clicked.connect(self._on_tab_clicked)
            self._tab_group.addButton(b)
            h.addWidget(b)

        h.addStretch()
        return bar

    def _on_tab_clicked(self) -> None:
        btn = self.sender()
        key = btn.property("tab_key")
        self.current_tab = key
        target = {
            "projects": self.projects_view,
            "playlists": self.playlists_view,
            "notes": self.notes_view,
        }.get(key, self.projects_view)
        cross_fade_stack(
            self.content_stack, self.content_stack.indexOf(target), duration=220,
        )

    def _build_projects_view(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_detail_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 800])
        return splitter

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(320)
        v = QVBoxLayout(sidebar)
        v.setContentsMargins(14, 16, 14, 14)
        v.setSpacing(12)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search projects, tags, notes…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search)
        v.addWidget(self.search_input)

        chip_row = QHBoxLayout()
        chip_row.setSpacing(6)
        self.filter_group = QButtonGroup(self)
        self.filter_group.setExclusive(True)
        for key, label in [("all", "All"), ("active", "Active"), ("completed", "Done")]:
            b = QPushButton(label)
            b.setObjectName("filterChip")
            b.setCheckable(True)
            b.setProperty("filter_key", key)
            if key == "all":
                b.setChecked(True)
            b.clicked.connect(self._on_filter_clicked)
            self.filter_group.addButton(b)
            chip_row.addWidget(b)
        chip_row.addStretch()

        self.tag_filter_btn = QPushButton("Tag")
        self.tag_filter_btn.setObjectName("filterChip")
        self.tag_filter_btn.clicked.connect(self._show_tag_menu)
        chip_row.addWidget(self.tag_filter_btn)

        v.addLayout(chip_row)

        self.list_widget = QListWidget()
        self.list_widget.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list_widget.setUniformItemSizes(False)
        self.list_widget.setFrameShape(QListWidget.Shape.NoFrame)
        self.list_widget.currentItemChanged.connect(self._on_select)
        # Drag-to-reorder + right-click pin/unpin menu.
        self.list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(
            self._project_row_context_menu
        )
        self.list_widget.model().rowsMoved.connect(self._persist_project_order)
        v.addWidget(self.list_widget, 1)

        self.empty_list_hint = QLabel("")
        self.empty_list_hint.setObjectName("emptyHint")
        self.empty_list_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_list_hint.setVisible(False)
        v.addWidget(self.empty_list_hint)

        return sidebar

    def _build_detail_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("detailPanel")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.detail_stack = QStackedWidget()
        outer.addWidget(self.detail_stack, 1)

        # Empty state
        empty = QWidget()
        empty.setObjectName("detailEmpty")
        ev = QVBoxLayout(empty)
        ev.addStretch()
        msg = QLabel("Select a project")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 14px; background: transparent;"
        )
        ev.addWidget(msg)
        ev.addStretch()
        self.detail_stack.addWidget(empty)

        detail = QWidget()
        detail.setObjectName("detailPanel")
        dv = QVBoxLayout(detail)
        dv.setContentsMargins(36, 30, 36, 28)
        dv.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(14)
        self.detail_toggle = CompletionToggle()
        self.detail_toggle.toggled.connect(self._on_detail_toggle)
        header.addWidget(self.detail_toggle, alignment=Qt.AlignmentFlag.AlignTop)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        self.title_label = QLabel("")
        self.title_label.setObjectName("title")
        self.title_label.setWordWrap(True)
        title_col.addWidget(self.title_label)

        self.path_label = QLabel("")
        self.path_label.setObjectName("path")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        title_col.addWidget(self.path_label)
        header.addLayout(title_col, 1)
        dv.addLayout(header)
        dv.addSpacing(24)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(36)
        self.modified_box = self._make_meta("Last modified")
        self.size_box = self._make_meta("Size")
        self.status_box = self._make_meta("Status")
        self.tested_box = self._make_toggle_meta("Tested", color_key="INFO")
        # The inner toggle is the actionable bit; wire its signal here so
        # the rest of the meta-box helper stays generic.
        self.tested_toggle = self.tested_box._toggle  # type: ignore[attr-defined]
        self.tested_toggle.setToolTip("Mark this project as tested")
        self.tested_toggle.toggled.connect(self._on_tested_toggle)
        for w in (self.modified_box, self.size_box, self.status_box, self.tested_box):
            meta_row.addWidget(w)
        meta_row.addStretch()
        dv.addLayout(meta_row)
        dv.addSpacing(28)

        tags_label = QLabel("TAGS")
        tags_label.setObjectName("sectionLabel")
        dv.addWidget(tags_label)
        dv.addSpacing(8)

        self.tags_row_widget = QWidget()
        self.tags_layout = QHBoxLayout(self.tags_row_widget)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setSpacing(6)
        dv.addWidget(self.tags_row_widget)

        self.tag_palette_wrap = QWidget()
        pw_outer = QVBoxLayout(self.tag_palette_wrap)
        pw_outer.setContentsMargins(0, 10, 0, 0)
        pw_outer.setSpacing(6)
        palette_label = QLabel("PICK FROM EXISTING")
        palette_label.setObjectName("sectionLabel")
        pw_outer.addWidget(palette_label)
        self.tag_palette_inner = QWidget()
        self.tag_palette_layout = FlowLayout(self.tag_palette_inner, spacing=5)
        pw_outer.addWidget(self.tag_palette_inner)
        self.tag_palette_wrap.setVisible(False)
        dv.addWidget(self.tag_palette_wrap)
        dv.addSpacing(20)

        notes_header = QHBoxLayout()
        notes_label = QLabel("NOTES")
        notes_label.setObjectName("sectionLabel")
        notes_header.addWidget(notes_label)
        notes_header.addStretch()

        self.notes_edit = QTextEdit()
        self.notes_edit.setObjectName("notes")
        self.notes_edit.setPlaceholderText("Write something about this project…")
        self.notes_edit.textChanged.connect(self._on_notes_changed)
        self.notes_toggle, self.notes_stack, self.notes_preview = make_markdown_pane(self.notes_edit)
        notes_header.addWidget(self.notes_toggle)
        dv.addLayout(notes_header)
        dv.addSpacing(8)
        dv.addWidget(self.notes_stack, 1)

        self.detail_stack.addWidget(detail)
        self.detail_stack.setCurrentIndex(0)
        return panel

    # ─── Playlists view ─────────────────────────────────────────

    def _build_playlists_view(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_playlists_sidebar())
        splitter.addWidget(self._build_playlist_detail_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 800])
        return splitter

    def _build_playlists_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(320)
        v = QVBoxLayout(sidebar)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        self.playlist_add_container = QWidget()
        cv = QVBoxLayout(self.playlist_add_container)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(4)

        self.playlist_add_btn = QPushButton("+ Add YouTube playlist")
        self.playlist_add_btn.setObjectName("primary")
        self.playlist_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.playlist_add_btn.clicked.connect(self._show_playlist_url_input)
        cv.addWidget(self.playlist_add_btn)

        self.playlist_error_label = QLabel("")
        self.playlist_error_label.setObjectName("errorMessage")
        self.playlist_error_label.setWordWrap(True)
        self.playlist_error_label.setVisible(False)
        cv.addWidget(self.playlist_error_label)

        v.addWidget(self.playlist_add_container)

        self.playlists_list_widget = QListWidget()
        self.playlists_list_widget.setVerticalScrollMode(
            QListWidget.ScrollMode.ScrollPerPixel
        )
        self.playlists_list_widget.setFrameShape(QListWidget.Shape.NoFrame)
        self.playlists_list_widget.currentItemChanged.connect(self._on_playlist_select)
        self.playlists_list_widget.setDragDropMode(
            QListWidget.DragDropMode.InternalMove
        )
        self.playlists_list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.playlists_list_widget.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.playlists_list_widget.customContextMenuRequested.connect(
            self._playlist_row_context_menu
        )
        self.playlists_list_widget.model().rowsMoved.connect(
            self._persist_playlist_order
        )
        v.addWidget(self.playlists_list_widget, 1)

        self.playlists_empty_hint = QLabel("No playlists yet.\nPaste a YouTube playlist URL to start.")
        self.playlists_empty_hint.setObjectName("emptyHint")
        self.playlists_empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.playlists_empty_hint.setVisible(True)
        v.addWidget(self.playlists_empty_hint)

        return sidebar

    def _build_playlist_detail_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("detailPanel")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.playlist_detail_stack = QStackedWidget()
        outer.addWidget(self.playlist_detail_stack, 1)

        # Empty state
        empty = QWidget()
        empty.setObjectName("detailEmpty")
        ev = QVBoxLayout(empty)
        ev.addStretch()
        msg = QLabel("Select a playlist")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 14px; background: transparent;"
        )
        ev.addWidget(msg)
        ev.addStretch()
        self.playlist_detail_stack.addWidget(empty)

        # Detail state
        detail = QWidget()
        detail.setObjectName("detailPanel")
        dv = QVBoxLayout(detail)
        dv.setContentsMargins(36, 28, 36, 24)
        dv.setSpacing(0)

        # Header is a two-column row: existing playlist metadata on the
        # left, the general playlist-notes editor on the right. Both
        # columns share the same height (driven by the left column's
        # content), so notes are always visible alongside the metadata.
        header_row = QHBoxLayout()
        header_row.setSpacing(28)

        left_col = QVBoxLayout()
        left_col.setSpacing(0)

        self.playlist_title_label = QLabel("")
        self.playlist_title_label.setObjectName("title")
        self.playlist_title_label.setWordWrap(True)
        left_col.addWidget(self.playlist_title_label)
        left_col.addSpacing(4)

        self.playlist_uploader_label = QLabel("")
        self.playlist_uploader_label.setObjectName("subtitle")
        left_col.addWidget(self.playlist_uploader_label)
        left_col.addSpacing(18)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(28)
        self.pl_count_box = self._make_meta("Videos")
        self.pl_watched_box = self._make_meta("Watched")
        self.pl_fetched_box = self._make_meta("Last refreshed")
        for w in (self.pl_count_box, self.pl_watched_box, self.pl_fetched_box):
            meta_row.addWidget(w)
        meta_row.addStretch()
        left_col.addLayout(meta_row)
        left_col.addSpacing(14)

        self.pl_progress = QProgressBar()
        self.pl_progress.setTextVisible(False)
        self.pl_progress.setRange(0, 100)
        self.pl_progress.setFixedHeight(6)
        left_col.addWidget(self.pl_progress)
        left_col.addSpacing(18)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.pl_refresh_btn = QPushButton("Refresh")
        self.pl_refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pl_refresh_btn.clicked.connect(self._refresh_current_playlist)
        action_row.addWidget(self.pl_refresh_btn)

        self.pl_remove_btn = QPushButton("Remove")
        self.pl_remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pl_remove_btn.clicked.connect(self._remove_current_playlist)
        action_row.addWidget(self.pl_remove_btn)

        self.pl_open_btn = QPushButton("Open on YouTube")
        self.pl_open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pl_open_btn.clicked.connect(self._open_current_playlist_url)
        action_row.addWidget(self.pl_open_btn)

        action_row.addStretch()
        left_col.addLayout(action_row)
        left_col.addSpacing(18)

        pl_tags_label = QLabel("TAGS")
        pl_tags_label.setObjectName("sectionLabel")
        left_col.addWidget(pl_tags_label)
        left_col.addSpacing(6)

        self.playlist_tags_wrap = QWidget()
        self.playlist_tags_layout = QHBoxLayout(self.playlist_tags_wrap)
        self.playlist_tags_layout.setContentsMargins(0, 0, 0, 0)
        self.playlist_tags_layout.setSpacing(6)
        left_col.addWidget(self.playlist_tags_wrap)

        self.playlist_palette_wrap = QWidget()
        pp_outer = QVBoxLayout(self.playlist_palette_wrap)
        pp_outer.setContentsMargins(0, 10, 0, 0)
        pp_outer.setSpacing(6)
        pp_label = QLabel("PICK FROM EXISTING")
        pp_label.setObjectName("sectionLabel")
        pp_outer.addWidget(pp_label)
        self.playlist_palette_inner = QWidget()
        self.playlist_palette_layout = FlowLayout(
            self.playlist_palette_inner, spacing=5
        )
        pp_outer.addWidget(self.playlist_palette_inner)
        self.playlist_palette_wrap.setVisible(False)
        left_col.addWidget(self.playlist_palette_wrap)
        left_col.addStretch(1)

        header_row.addLayout(left_col, 3)

        right_col = QVBoxLayout()
        right_col.setSpacing(6)
        pl_notes_header = QHBoxLayout()
        pl_notes_label = QLabel("PLAYLIST NOTES")
        pl_notes_label.setObjectName("sectionLabel")
        pl_notes_header.addWidget(pl_notes_label)
        pl_notes_header.addStretch()

        self.playlist_notes_edit = QTextEdit()
        self.playlist_notes_edit.setObjectName("notes")
        self.playlist_notes_edit.setPlaceholderText(
            "Write something about this playlist…"
        )
        self.playlist_notes_edit.textChanged.connect(
            self._on_playlist_notes_changed
        )
        (self.playlist_notes_toggle, self.playlist_notes_stack,
         self.playlist_notes_preview) = make_markdown_pane(self.playlist_notes_edit)
        pl_notes_header.addWidget(self.playlist_notes_toggle)
        right_col.addLayout(pl_notes_header)
        right_col.addWidget(self.playlist_notes_stack, 1)
        header_row.addLayout(right_col, 2)

        dv.addLayout(header_row)
        dv.addSpacing(14)

        # Vertical splitter: video list on top, per-video notes below.
        # Playlist-level notes live in the header_row above.
        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setHandleWidth(1)
        vsplit.setChildrenCollapsible(False)

        videos_wrap = QWidget()
        vw = QVBoxLayout(videos_wrap)
        vw.setContentsMargins(0, 0, 0, 0)
        vw.setSpacing(6)
        v_label = QLabel("VIDEOS")
        v_label.setObjectName("sectionLabel")
        vw.addWidget(v_label)
        self.video_list_widget = QListWidget()
        self.video_list_widget.setVerticalScrollMode(
            QListWidget.ScrollMode.ScrollPerPixel
        )
        self.video_list_widget.setFrameShape(QListWidget.Shape.NoFrame)
        self.video_list_widget.currentItemChanged.connect(self._on_video_select)
        vw.addWidget(self.video_list_widget, 1)
        vsplit.addWidget(videos_wrap)

        notes_wrap = QWidget()
        nw = QVBoxLayout(notes_wrap)
        nw.setContentsMargins(0, 8, 0, 0)
        nw.setSpacing(6)
        video_notes_header = QHBoxLayout()
        self.video_notes_label = QLabel("NOTES")
        self.video_notes_label.setObjectName("sectionLabel")
        video_notes_header.addWidget(self.video_notes_label)
        video_notes_header.addStretch()

        self.video_notes_edit = QTextEdit()
        self.video_notes_edit.setObjectName("notes")
        self.video_notes_edit.setPlaceholderText("Select a video to write notes…")
        self.video_notes_edit.setEnabled(False)
        self.video_notes_edit.textChanged.connect(self._on_video_notes_changed)
        (self.video_notes_toggle, self.video_notes_stack,
         self.video_notes_preview) = make_markdown_pane(self.video_notes_edit)
        video_notes_header.addWidget(self.video_notes_toggle)
        nw.addLayout(video_notes_header)
        nw.addWidget(self.video_notes_stack, 1)
        vsplit.addWidget(notes_wrap)

        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 2)
        dv.addWidget(vsplit, 1)

        self.playlist_detail_stack.addWidget(detail)
        self.playlist_detail_stack.setCurrentIndex(0)
        return panel

    # ─── Notes view ──────────────────────────────────────────────

    def _build_notes_view(self) -> QWidget:
        container = QWidget()
        container.setObjectName("detailPanel")
        v = QVBoxLayout(container)
        v.setContentsMargins(36, 30, 36, 28)
        v.setSpacing(10)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.notes_search_input = QLineEdit()
        self.notes_search_input.setPlaceholderText(
            "Search notes — Enter / Shift+Enter for next / previous"
        )
        self.notes_search_input.setClearButtonEnabled(True)
        self.notes_search_input.textChanged.connect(self._on_notes_search_changed)
        self.notes_search_input.returnPressed.connect(self._notes_search_next)
        search_row.addWidget(self.notes_search_input, 1)
        self.notes_search_count = QLabel("")
        self.notes_search_count.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px; background: transparent;"
        )
        search_row.addWidget(self.notes_search_count)

        self.global_notes_edit = QTextEdit()
        self.global_notes_edit.setObjectName("notes")
        self.global_notes_edit.setPlaceholderText("Write anything here…")
        self.global_notes_edit.textChanged.connect(self._on_global_notes_changed)
        # Re-highlight matches after the user types so highlights stay current.
        self.global_notes_edit.textChanged.connect(self._refresh_notes_highlights)
        (self.global_notes_toggle, self.global_notes_stack,
         self.global_notes_preview) = make_markdown_pane(self.global_notes_edit)
        search_row.addWidget(self.global_notes_toggle)
        v.addLayout(search_row)
        v.addWidget(self.global_notes_stack, 1)

        # Cached search state.
        self._notes_match_count = 0

        # Shift+Enter for previous match while focus is in the search box.
        prev_sc = QShortcut(
            QKeySequence("Shift+Return"), self.notes_search_input,
            activated=self._notes_search_prev,
        )
        prev_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        return container

    def _on_notes_search_changed(self, _text: str) -> None:
        self._refresh_notes_highlights()
        # Jump to the first match so the user sees something immediately.
        if self._notes_match_count and self.notes_search_input.text().strip():
            self._notes_search_seek(forward=True, from_start=True)

    def _refresh_notes_highlights(self) -> None:
        """Highlight all matches of the search query inside the notes editor."""
        if self._highlighting_notes:
            return
        from PySide6.QtGui import QTextCharFormat, QColor, QTextCursor
        if not hasattr(self, "notes_search_input") or not hasattr(self, "global_notes_edit"):
            return
        query = self.notes_search_input.text()
        self._highlighting_notes = True
        try:
            doc = self.global_notes_edit.document()
            # Clear previous highlights by re-applying default char format
            # across the whole doc (cheap, idempotent).
            cur = QTextCursor(doc)
            cur.select(QTextCursor.SelectionType.Document)
            clear_fmt = QTextCharFormat()
            cur.setCharFormat(clear_fmt)
            cur.clearSelection()
            count = 0
            if query.strip():
                hl = QTextCharFormat()
                base = QColor(theme.ACCENT)
                base.setAlpha(85)
                hl.setBackground(base)
                hl.setForeground(QColor(theme.TEXT))
                search_cursor = QTextCursor(doc)
                while True:
                    search_cursor = doc.find(query, search_cursor)
                    if search_cursor.isNull():
                        break
                    search_cursor.mergeCharFormat(hl)
                    count += 1
            self._notes_match_count = count
        finally:
            self._highlighting_notes = False
        if hasattr(self, "notes_search_count"):
            self.notes_search_count.setText(
                f"{count} match{'es' if count != 1 else ''}" if query.strip() else ""
            )

    def _notes_search_next(self) -> None:
        self._notes_search_seek(forward=True)

    def _notes_search_prev(self) -> None:
        self._notes_search_seek(forward=False)

    def _notes_search_seek(self, *, forward: bool, from_start: bool = False) -> None:
        from PySide6.QtGui import QTextDocument, QTextCursor
        query = self.notes_search_input.text()
        if not query.strip():
            return
        doc = self.global_notes_edit.document()
        flags = QTextDocument.FindFlag(0)
        if not forward:
            flags |= QTextDocument.FindFlag.FindBackward
        start = (
            QTextCursor(doc) if from_start
            else self.global_notes_edit.textCursor()
        )
        found = doc.find(query, start, flags)
        if found.isNull():
            # Wrap around.
            wrap = QTextCursor(doc)
            if not forward:
                wrap.movePosition(QTextCursor.MoveOperation.End)
            found = doc.find(query, wrap, flags)
        if not found.isNull():
            self.global_notes_edit.setTextCursor(found)
            self.global_notes_edit.ensureCursorVisible()

    def _make_meta(self, label_text: str) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(4)
        lbl = QLabel(label_text.upper())
        lbl.setObjectName("sectionLabel")
        val = QLabel("—")
        val.setObjectName("metaValue")
        l.addWidget(lbl)
        l.addWidget(val)
        w._value = val  # type: ignore[attr-defined]
        return w

    def _make_toggle_meta(self, label_text: str, *, color_key: str = "SUCCESS") -> QWidget:
        """Meta box whose value slot is a CompletionToggle instead of a label."""
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(4)
        lbl = QLabel(label_text.upper())
        lbl.setObjectName("sectionLabel")
        toggle = CompletionToggle(color_key=color_key)
        l.addWidget(lbl)
        l.addWidget(toggle)
        w._toggle = toggle  # type: ignore[attr-defined]
        return w

    def _set_status_meta(self, completed: bool) -> None:
        val: QLabel = self.status_box._value  # type: ignore[attr-defined]
        val.setText("Completed" if completed else "Active")
        if completed:
            val.setStyleSheet(
                f"color: {theme.SUCCESS}; font-size: 13px; font-weight: 600;"
            )
        else:
            val.setStyleSheet(
                f"color: {theme.TEXT}; font-size: 13px; font-weight: 500;"
            )

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.choose_folder)
        QShortcut(QKeySequence("Ctrl+F"), self,
                  activated=lambda: self.search_input.setFocus())
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.refresh)
        QShortcut(QKeySequence("Ctrl+N"), self,
                  activated=lambda: self.notes_edit.setFocus()
                  if self.current_project else None)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._open_command_palette)

    # ─── Folder lifecycle ────────────────────────────────────────

    def choose_folder(self) -> None:
        start = ""
        if self.store:
            start = str(self.store.root)
        elif (last := load_state().get("last_folder")):
            start = last
        path = QFileDialog.getExistingDirectory(self, "Choose project folder", start)
        if path:
            self.load_folder(Path(path))

    def load_folder(self, path: Path) -> None:
        if not path.is_dir():
            QMessageBox.warning(self, "Projectum", f"Not a folder:\n{path}")
            return
        # Flush pending writes to the *previous* folder before swapping
        # stores — otherwise typed-but-unsaved notes get lost.
        self._flush_pending_writes()
        try:
            self.store = ProjectStore(path)
        except Exception as e:
            QMessageBox.critical(self, "Projectum", f"Could not open folder:\n{e}")
            return

        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())
        self._watcher.addPath(str(path))

        save_state({"last_folder": str(path)})

        if self.stack.currentWidget() is not self.main_view:
            cross_fade_stack(
                self.stack,
                self.stack.indexOf(self.main_view),
                duration=260,
            )
        self.folder_label.setText(str(path))
        self.folder_label.setToolTip(str(path))
        self.current_project = None
        self.current_playlist = None
        self.current_video = None
        self.tag_filter = None
        self._update_tag_filter_label()
        self._full_rebuild_list()
        self._update_stats()
        self._rebuild_playlists_list()
        # Load the folder-level scratchpad. Guard so setPlainText doesn't
        # trip the debounced save back into the freshly-loaded store.
        self._loading_global_notes = True
        try:
            self.global_notes_edit.setPlainText(self.store.notes)
        finally:
            self._loading_global_notes = False
        self.detail_stack.setCurrentIndex(0)
        self.playlist_detail_stack.setCurrentIndex(0)

    def _flush_pending_writes(self) -> None:
        """Force any debounced save timers to fire synchronously."""
        for timer, handler in (
            (self._notes_save_timer, self._save_notes),
            (self._video_notes_save_timer, self._save_video_notes),
            (self._playlist_notes_save_timer, self._save_playlist_notes),
            (self._global_notes_save_timer, self._save_global_notes),
        ):
            if timer.isActive():
                timer.stop()
                handler()

    def _on_watcher_changed(self, _path: str) -> None:
        self._watcher_debounce.start()

    def refresh(self) -> None:
        if not self.store:
            return
        cur_name = self.current_project.name if self.current_project else None
        self._flush_pending_writes()
        before = self._project_snapshot()
        self.store.load()
        after = self._project_snapshot()
        if before == after:
            return
        self._full_rebuild_list(preserve_name=cur_name)
        self._rebuild_playlists_list()
        self._update_stats()

    def _project_snapshot(self) -> tuple:
        if not self.store:
            return ()
        projects = tuple(
            (name, p.completed, p.notes, tuple(p.tags), p.pinned, p.position,
             p.tested)
            for name, p in self.store.projects.items()
        )
        playlists = tuple(
            (
                pl.id, pl.title, pl.uploader, pl.notes, tuple(pl.tags),
                pl.pinned, pl.position,
                tuple(
                    (v.id, v.title, v.completed, v.notes, v.unavailable)
                    for v in pl.videos
                ),
            )
            for pl in self.store.playlists
        )
        return (projects, playlists)

    # ─── Listing ─────────────────────────────────────────────────

    def _full_rebuild_list(self, preserve_name: str | None = None) -> None:
        """Rebuild every row from scratch. Use after store reload or folder load."""
        if not self.store:
            return
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self._row_items.clear()
        for project in self.store.sorted_projects():
            self._append_row(project)
        self.list_widget.blockSignals(False)
        self._apply_filter(preserve_name=preserve_name)

    def _append_row(self, project: Project) -> QListWidgetItem:
        row = ProjectRow(project, self.store)
        row.completion_changed.connect(
            lambda checked, p=project: self._on_row_completion(p, checked)
        )
        row.tag_right_clicked.connect(self._show_color_picker)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, project.name)
        item.setSizeHint(QSize(0, row.sizeHint().height()))
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, row)
        self._row_items[project.name] = item
        return item

    def _apply_filter(self, preserve_name: str | None = None) -> None:
        """Adjust row visibility based on filter + search. Cheap — no rebuild."""
        if not self.store:
            return
        target = preserve_name or (
            self.current_project.name if self.current_project else None
        )
        q = self.search_query.lower().strip()
        visible = 0
        target_item: QListWidgetItem | None = None

        for name, item in self._row_items.items():
            project = self.store.projects.get(name)
            if not project:
                item.setHidden(True)
                continue
            hide = False
            if self.current_filter == "active" and project.completed:
                hide = True
            elif self.current_filter == "completed" and not project.completed:
                hide = True
            elif self.tag_filter and self.tag_filter not in project.tags:
                hide = True
            elif q:
                hay = (
                    project.name.lower()
                    + " " + " ".join(project.tags).lower()
                    + " " + project.notes.lower()
                )
                if q not in hay:
                    hide = True
            item.setHidden(hide)
            if not hide:
                visible += 1
                if target and name == target:
                    target_item = item

        if target_item is not None:
            self.list_widget.setCurrentItem(target_item)
        elif preserve_name is None:
            # Filter change hid the current selection (or nothing visible).
            # Clear so the detail panel matches the list.
            self.list_widget.blockSignals(True)
            self.list_widget.setCurrentItem(None)
            self.list_widget.blockSignals(False)
            self.current_project = None
            self.detail_stack.setCurrentIndex(0)
        # else: preserve_name was explicitly passed (toggle path) — keep
        # current_project and detail panel visible so the user can keep editing
        # / untoggle, even when the new filter would hide the row.

        if visible == 0:
            self.empty_list_hint.setVisible(True)
            if not self.store.projects:
                self.empty_list_hint.setText(
                    "No subfolders found.\nDrop projects into this folder and refresh."
                )
            else:
                self.empty_list_hint.setText("Nothing matches the current filter.")
        else:
            self.empty_list_hint.setVisible(False)

    def _update_stats(self) -> None:
        if not self.store:
            self.stats_label.setText("")
            return
        done, total = self.store.stats()
        if total == 0:
            self.stats_label.setText("empty")
            return
        pct = round(done * 100 / total) if total else 0
        self.stats_label.setText(f"{done}/{total} · {pct}%")

    def _update_tag_filter_label(self) -> None:
        if self.tag_filter:
            self.tag_filter_btn.setText(f"#{self.tag_filter}")
        else:
            self.tag_filter_btn.setText("Tag")

    # ─── Filters ─────────────────────────────────────────────────

    def _on_search(self, text: str) -> None:
        self.search_query = text
        self._apply_filter()

    def _on_filter_clicked(self) -> None:
        btn = self.sender()
        self.current_filter = btn.property("filter_key")
        self._apply_filter()

    # ─── Pin & reorder ───────────────────────────────────────────

    def _project_row_context_menu(self, pos) -> None:
        if not self.store:
            return
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        project = self.store.projects.get(name)
        if project is None:
            return
        menu = QMenu(self)
        toggle = QAction("Unpin from top" if project.pinned else "Pin to top", menu)
        toggle.triggered.connect(
            partial(self._toggle_project_pin, project.name)
        )
        menu.addAction(toggle)
        menu.exec(self.list_widget.viewport().mapToGlobal(pos))

    def _toggle_project_pin(self, name: str) -> None:
        if not self.store:
            return
        p = self.store.projects.get(name)
        if p is None:
            return
        p.pinned = not p.pinned
        self.store.save()
        # Rebuild so sort order reflects the new pin state; preserve selection.
        self._full_rebuild_list(preserve_name=name)

    def _persist_project_order(self, *_args) -> None:
        if not self.store:
            return
        names = []
        for row in range(self.list_widget.count()):
            it = self.list_widget.item(row)
            if it is None:
                continue
            names.append(it.data(Qt.ItemDataRole.UserRole))
        self.store.reorder_projects(names)
        self.store.save()

    def _playlist_row_context_menu(self, pos) -> None:
        if not self.store:
            return
        item = self.playlists_list_widget.itemAt(pos)
        if item is None:
            return
        pid = item.data(Qt.ItemDataRole.UserRole)
        pl = self.store.get_playlist(pid)
        if pl is None:
            return
        menu = QMenu(self)
        toggle = QAction("Unpin from top" if pl.pinned else "Pin to top", menu)
        toggle.triggered.connect(partial(self._toggle_playlist_pin, pid))
        menu.addAction(toggle)
        menu.exec(self.playlists_list_widget.viewport().mapToGlobal(pos))

    def _toggle_playlist_pin(self, pid: str) -> None:
        if not self.store:
            return
        pl = self.store.get_playlist(pid)
        if pl is None:
            return
        pl.pinned = not pl.pinned
        self.store.save()
        # Rebuild and reselect (preserves the open detail panel).
        was_selected = (
            self.current_playlist is not None and self.current_playlist.id == pid
        )
        self._rebuild_playlists_list()
        if was_selected:
            it = self._playlist_items.get(pid)
            if it is not None:
                self.playlists_list_widget.setCurrentItem(it)

    def _persist_playlist_order(self, *_args) -> None:
        if not self.store:
            return
        ids = []
        for row in range(self.playlists_list_widget.count()):
            it = self.playlists_list_widget.item(row)
            if it is None:
                continue
            ids.append(it.data(Qt.ItemDataRole.UserRole))
        self.store.reorder_playlists(ids)
        self.store.save()

    def _show_tag_menu(self) -> None:
        if not self.store:
            return
        menu = QMenu(self)
        all_a = QAction("(All tags)", menu)
        all_a.triggered.connect(partial(self._set_tag_filter, None))
        menu.addAction(all_a)
        menu.addSeparator()
        tags = self.store.all_tags()
        if not tags:
            empty_a = QAction("No tags yet", menu)
            empty_a.setEnabled(False)
            menu.addAction(empty_a)
        else:
            for t in tags:
                a = QAction(f"#{t}", menu)
                a.triggered.connect(partial(self._set_tag_filter, t))
                menu.addAction(a)
        menu.exec(self.tag_filter_btn.mapToGlobal(
            self.tag_filter_btn.rect().bottomLeft()
        ))

    def _set_tag_filter(self, tag: str | None) -> None:
        self.tag_filter = tag
        self._update_tag_filter_label()
        self._apply_filter()

    # ─── Selection / details ─────────────────────────────────────

    def _on_select(self, current, _previous) -> None:
        if not current:
            self.current_project = None
            self.detail_stack.setCurrentIndex(0)
            return
        name = current.data(Qt.ItemDataRole.UserRole)
        if not self.store:
            return
        project = self.store.projects.get(name)
        if not project:
            return
        # Save any pending notes for the previous project
        if self._notes_save_timer.isActive():
            self._notes_save_timer.stop()
            self._save_notes()
        self.current_project = project
        self._load_detail(project)
        was_empty = self.detail_stack.currentIndex() == 0
        if was_empty:
            # Pixmap-snapshot crossfade — the destination panel has custom
            # painters (CompletionToggle, TagChip) so a direct opacity-effect
            # fade glitches; this fades only a QLabel overlay of the old
            # empty placeholder.
            cross_fade_stack(self.detail_stack, 1, duration=220)
        else:
            self.detail_stack.setCurrentIndex(1)

    def _load_detail(self, p: Project) -> None:
        self._loading_details = True
        try:
            self.title_label.setText(p.name)
            self.path_label.setText(p.path)
            self.detail_toggle.setChecked(p.completed)
            self.tested_toggle.setChecked(p.tested)
            self.notes_edit.setPlainText(p.notes)
            mod = p.last_modified()
            self.modified_box._value.setText(self._format_date(mod) if mod else "—")  # type: ignore[attr-defined]
            self.size_box._value.setText("calculating…")  # type: ignore[attr-defined]
            self._set_status_meta(p.completed)
            self._rebuild_tags()
        finally:
            self._loading_details = False
        # Kick off async size calc
        self._size_pending_for = p.name
        runnable = SizeRunnable(p.name, Path(p.path))
        runnable.signals.done.connect(self._on_size_done)
        self._size_pool.start(runnable)

    def _on_size_done(self, name: str, size: int) -> None:
        if not self.current_project or self.current_project.name != name:
            return
        self.size_box._value.setText(self._format_size(size))  # type: ignore[attr-defined]

    def _rebuild_tags(self, *, editing: bool = False) -> None:
        self._tag_editor = None
        while self.tags_layout.count():
            item = self.tags_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if not self.current_project:
            self.tag_palette_wrap.setVisible(False)
            return

        for t in self.current_project.tags:
            chip = TagChip(
                t, tag_color(t, self.store.tag_colors), removable=True,
            )
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"Click × to remove · Right-click for color · '{t}'")
            chip.remove_clicked.connect(partial(self._remove_tag, t))
            chip.right_clicked.connect(partial(self._show_color_picker, t))
            self.tags_layout.addWidget(chip)

        if editing:
            editor = TagEditor()
            existing: list[str] = []
            if self.store:
                existing = [
                    t for t in self.store.all_tags()
                    if t not in self.current_project.tags
                ]
                if existing:
                    completer = QCompleter(existing, editor)
                    completer.setCaseSensitivity(
                        Qt.CaseSensitivity.CaseInsensitive
                    )
                    completer.setFilterMode(Qt.MatchFlag.MatchContains)
                    editor.setCompleter(completer)
            editor.submitted.connect(self._on_tag_submit)
            editor.cancelled.connect(self._exit_tag_edit_mode)
            self.tags_layout.addWidget(editor)
            self._tag_editor = editor
            QTimer.singleShot(0, editor.setFocus)
            self._populate_tag_palette(
                self.tag_palette_layout, existing, self._on_tag_submit
            )
            self.tag_palette_wrap.setVisible(bool(existing))
        else:
            add_btn = QPushButton("+ add tag")
            add_btn.setObjectName("ghost")
            add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            add_btn.clicked.connect(self._enter_tag_edit_mode)
            self.tags_layout.addWidget(add_btn)
            self._clear_layout(self.tag_palette_layout)
            self.tag_palette_wrap.setVisible(False)

        self.tags_layout.addStretch()

    def _enter_tag_edit_mode(self) -> None:
        # Keep existing chips mounted — only the trailing "+ add tag" button
        # swaps to the inline editor, so the user's tags don't visibly vanish.
        self._rebuild_tags(editing=True)
        if self.tag_palette_wrap.isVisible():
            # Slide the palette open by animating maxHeight. The palette
            # contains TagChips (custom paint) so an opacity-effect fade
            # would render glitched frames; a height animation reflows the
            # parent layout cleanly with no opacity pipeline involved.
            self.tag_palette_wrap.setVisible(False)
            slide_in_height(self.tag_palette_wrap, duration=220)

    def _exit_tag_edit_mode(self) -> None:
        if self.tag_palette_wrap.isVisible():
            slide_out_height(
                self.tag_palette_wrap,
                duration=180,
                on_done=lambda: self._rebuild_tags(editing=False),
            )
        else:
            self._rebuild_tags(editing=False)

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _populate_tag_palette(self, layout, tags: list[str], on_pick) -> None:
        self._clear_layout(layout)
        if not tags or not self.store:
            return
        for t in tags:
            chip = TagChip(t, tag_color(t, self.store.tag_colors))
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"Add tag '{t}'")
            chip.clicked.connect(partial(on_pick, t))
            chip.right_clicked.connect(partial(self._show_color_picker, t))
            layout.addWidget(chip)

    def _on_tag_submit(self, tag: str) -> None:
        if not self.current_project or not self.store:
            return
        tag = tag.strip().lower().lstrip("#")
        if not tag or tag in self.current_project.tags:
            self._exit_tag_edit_mode()
            return
        self.current_project.tags.append(tag)
        self.store.save()
        self._exit_tag_edit_mode()
        self._update_row_tags(self.current_project.name)

    def _remove_tag(self, tag: str) -> None:
        if not self.current_project or not self.store:
            return
        if tag in self.current_project.tags:
            self.current_project.tags.remove(tag)
            # Drop the tag's color override if nothing references the tag
            # anywhere — keeps the palette and tag_colors free of orphans.
            self.store.prune_unused_tag_colors()
            self.store.save()
            self._rebuild_tags()
            self._update_row_tags(self.current_project.name)
            # The tag filter only affects projects; clear if no project uses
            # this tag anymore, regardless of playlist usage.
            project_tags = {
                t for p in self.store.projects.values() for t in p.tags
            }
            if self.tag_filter == tag and tag not in project_tags:
                self._set_tag_filter(None)

    # ─── Edits ───────────────────────────────────────────────────

    def _on_detail_toggle(self, checked: bool) -> None:
        if self._loading_details or not self.current_project or not self.store:
            return
        self.current_project.completed = checked
        self.store.save()
        self._update_stats()
        self._set_status_meta(checked)
        item = self._row_items.get(self.current_project.name)
        if item is not None:
            row = self.list_widget.itemWidget(item)
            if isinstance(row, ProjectRow):
                row.set_completed(checked)
        if self.current_filter in ("active", "completed"):
            self._apply_filter(preserve_name=self.current_project.name)

    def _on_tested_toggle(self, checked: bool) -> None:
        if self._loading_details or not self.current_project or not self.store:
            return
        self.current_project.tested = checked
        self.store.save()
        item = self._row_items.get(self.current_project.name)
        if item is not None:
            row = self.list_widget.itemWidget(item)
            if isinstance(row, ProjectRow):
                row.set_tested(checked)

    def _on_row_completion(self, project: Project, checked: bool) -> None:
        if not self.store:
            return
        project.completed = checked
        self.store.save()
        self._update_stats()
        if self.current_project and self.current_project.name == project.name:
            self.detail_toggle.blockSignals(True)
            self.detail_toggle.setChecked(checked)
            self.detail_toggle.blockSignals(False)
            self._set_status_meta(checked)
        if self.current_filter in ("active", "completed"):
            self._apply_filter(
                preserve_name=self.current_project.name if self.current_project else None
            )

    def _on_notes_changed(self) -> None:
        if self._loading_details or not self.current_project:
            return
        self._notes_save_timer.start()

    def _save_notes(self) -> None:
        if not self.current_project or not self.store:
            return
        new_notes = self.notes_edit.toPlainText()
        if new_notes == self.current_project.notes:
            return
        had_notes = bool(self.current_project.notes.strip())
        self.current_project.notes = new_notes
        self.store.save()
        has_notes = bool(new_notes.strip())
        if has_notes != had_notes:
            self._update_row_notes(self.current_project.name, has_notes)

    def _on_global_notes_changed(self) -> None:
        # Format changes (highlight refresh) also fire textChanged — ignore.
        if self._highlighting_notes:
            return
        if self._loading_global_notes or not self.store:
            return
        self._global_notes_save_timer.start()

    def _save_global_notes(self) -> None:
        if not self.store:
            return
        new_notes = self.global_notes_edit.toPlainText()
        if new_notes == self.store.notes:
            return
        self.store.notes = new_notes
        self.store.save()

    def _update_row_notes(self, name: str, has_notes: bool) -> None:
        item = self._row_items.get(name)
        if item is None:
            return
        row = self.list_widget.itemWidget(item)
        if isinstance(row, ProjectRow):
            row.set_has_notes(has_notes)

    def _update_row_tags(self, name: str) -> None:
        item = self._row_items.get(name)
        if item is None:
            return
        row = self.list_widget.itemWidget(item)
        if isinstance(row, ProjectRow):
            row.refresh_tags()
            item.setSizeHint(QSize(0, row.sizeHint().height()))

    # ─── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _format_date(dt: datetime) -> str:
        now = datetime.now()
        delta = now - dt
        days = delta.days
        if days < 0:
            return dt.strftime("%b %d, %Y")
        if days == 0:
            mins = int(delta.total_seconds() // 60)
            if mins < 1:
                return "just now"
            if mins < 60:
                return f"{mins}m ago"
            return f"{mins // 60}h ago"
        if days == 1:
            return "yesterday"
        if days < 7:
            return f"{days}d ago"
        if days < 30:
            return f"{days // 7}w ago"
        if dt.year == now.year:
            return dt.strftime("%b %d")
        return dt.strftime("%b %Y")

    @staticmethod
    def _format_size(b: int) -> str:
        if b < 1024:
            return f"{b} B"
        kb = b / 1024
        if kb < 1024:
            return f"{kb:.1f} KB"
        mb = kb / 1024
        if mb < 1024:
            return f"{mb:.1f} MB"
        gb = mb / 1024
        return f"{gb:.2f} GB"

    # ─── Tag color customization ────────────────────────────────

    def _show_color_picker(self, tag: str) -> None:
        if not self.store:
            return
        current = self.store.tag_colors.get(tag)
        popup = ColorPickerPopup(tag, current, parent=self)
        popup.color_chosen.connect(partial(self._set_tag_color, tag))
        popup.reset.connect(partial(self._set_tag_color, tag, None))
        popup.custom_requested.connect(partial(self._open_custom_color, tag))
        # Keep a reference so the popup isn't garbage collected.
        self._color_popup = popup
        popup.adjustSize()
        cursor_pos = QCursor.pos()
        popup.move(cursor_pos.x() + 4, cursor_pos.y() + 4)
        popup.show()
        fade_window(popup, 1.0, duration=140)

    def _open_custom_color(self, tag: str) -> None:
        if not self.store:
            return
        from .theme import tag_color as _tc
        current_hex = self.store.tag_colors.get(tag) or _tc(tag)
        initial = QColor(current_hex)
        chosen = QColorDialog.getColor(
            initial, self, f"Pick color for #{tag}",
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
        )
        if chosen.isValid():
            self._set_tag_color(tag, chosen.name())

    def _set_tag_color(self, tag: str, color: str | None) -> None:
        if not self.store:
            return
        if color is None:
            self.store.tag_colors.pop(tag, None)
        else:
            self.store.tag_colors[tag] = color
        self.store.save()
        self._refresh_all_tag_displays()

    def _refresh_all_tag_displays(self) -> None:
        # Sidebar — projects
        for name in list(self._row_items.keys()):
            self._update_row_tags(name)
        # Sidebar — playlists
        for pid in list(self._playlist_items.keys()):
            self._update_playlist_row_tags(pid)
        # Detail panels
        if self.current_project:
            self._rebuild_tags()
        if self.current_playlist:
            self._rebuild_playlist_tags()

    # ─── Playlists ──────────────────────────────────────────────

    def _rebuild_playlists_list(self) -> None:
        self.playlists_list_widget.blockSignals(True)
        self.playlists_list_widget.clear()
        self._playlist_items.clear()
        if self.store:
            for pl in self.store.sorted_playlists():
                self._append_playlist_row(pl)
        self.playlists_list_widget.blockSignals(False)
        self._update_playlists_empty_hint()

    def _append_playlist_row(self, pl: Playlist) -> QListWidgetItem:
        row = PlaylistRow(pl, self.store)
        row.tag_right_clicked.connect(self._show_color_picker)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, pl.id)
        item.setSizeHint(QSize(0, row.sizeHint().height()))
        self.playlists_list_widget.addItem(item)
        self.playlists_list_widget.setItemWidget(item, row)
        self._playlist_items[pl.id] = item
        return item

    def _update_playlists_empty_hint(self) -> None:
        empty = not self.store or not self.store.playlists
        self.playlists_empty_hint.setVisible(empty)

    def _show_playlist_url_input(self) -> None:
        if self._playlist_url_input is not None:
            self._playlist_url_input.setFocus()
            return
        self.playlist_add_btn.setVisible(False)
        self.playlist_error_label.setVisible(False)

        edit = QLineEdit()
        edit.setPlaceholderText("Paste playlist URL — Enter to add, Esc to cancel")
        edit.setClearButtonEnabled(True)
        edit.returnPressed.connect(self._submit_playlist_url)
        edit.installEventFilter(self)
        self._playlist_url_input = edit
        layout = self.playlist_add_container.layout()
        layout.insertWidget(0, edit)
        # QLineEdit has no custom-painted children, so the opacity-effect
        # fade is safe here.
        fade_in(edit, duration=180)
        edit.setFocus()

    def _hide_playlist_url_input(self) -> None:
        self.playlist_add_btn.setEnabled(True)
        self.playlist_add_btn.setText("+ Add YouTube playlist")
        if self._playlist_url_input is not None:
            edit = self._playlist_url_input
            self._playlist_url_input = None
            fade_out(edit, duration=140, on_done=edit.deleteLater)
        if not self.playlist_add_btn.isVisible():
            self.playlist_add_btn.setVisible(True)
            fade_in(self.playlist_add_btn, duration=160)

    def _set_playlist_error(self, msg: str) -> None:
        self.playlist_error_label.setText(msg)
        self.playlist_error_label.setVisible(bool(msg))

    def _submit_playlist_url(self) -> None:
        if self._playlist_url_input is None or not self.store:
            return
        url = self._playlist_url_input.text().strip()
        if not url:
            return
        for existing in self.store.playlists:
            if existing.url == url:
                self._set_playlist_error("That playlist is already in the list.")
                return
        self._playlist_url_input.setEnabled(False)
        self.playlist_add_btn.setVisible(True)
        self.playlist_add_btn.setText("Fetching…")
        self.playlist_add_btn.setEnabled(False)
        self._set_playlist_error("")
        self._kick_fetch(url, on_done=self._on_add_done)

    def _kick_fetch(self, url: str, on_done) -> None:
        runnable = PlaylistFetchRunnable(url)
        # Keep both the runnable AND the callback alive in a dict keyed by url.
        # Bound-method slots (vs lambdas) are strongly-referenced by PySide6.
        self._pending_fetches[url] = (runnable, on_done)
        runnable.signals.done.connect(self._handle_fetch_done)
        runnable.signals.failed.connect(self._handle_fetch_failed)
        self._size_pool.start(runnable)

    def _handle_fetch_done(self, url: str, data: dict) -> None:
        entry = self._pending_fetches.pop(url, None)
        if entry is None:
            return
        _runnable, callback = entry
        callback(url, data, None)

    def _handle_fetch_failed(self, url: str, err: str) -> None:
        entry = self._pending_fetches.pop(url, None)
        if entry is None:
            return
        _runnable, callback = entry
        callback(url, None, err)

    def _on_add_done(self, url: str, data: dict | None, err: str | None) -> None:
        if not self.store:
            return
        if err or not data:
            self._set_playlist_error(err or "Failed to fetch playlist.")
            self.playlist_add_btn.setText("+ Add YouTube playlist")
            self.playlist_add_btn.setEnabled(True)
            if self._playlist_url_input is not None:
                self._playlist_url_input.setEnabled(True)
                self._playlist_url_input.setFocus()
            return
        pl = self.store.add_playlist(url, data)
        self._hide_playlist_url_input()
        item = self._append_playlist_row(pl)
        self._update_playlists_empty_hint()
        self.playlists_list_widget.setCurrentItem(item)

    def _on_playlist_select(self, current, _previous) -> None:
        # Flush any pending per-video and per-playlist notes for the
        # previous selection before swapping the panel contents.
        if self._video_notes_save_timer.isActive():
            self._video_notes_save_timer.stop()
            self._save_video_notes()
        if self._playlist_notes_save_timer.isActive():
            self._playlist_notes_save_timer.stop()
            self._save_playlist_notes()
        if not current:
            self.current_playlist = None
            self.current_video = None
            self.playlist_detail_stack.setCurrentIndex(0)
            return
        pid = current.data(Qt.ItemDataRole.UserRole)
        if not self.store:
            return
        pl = self.store.get_playlist(pid)
        if not pl:
            return
        self.current_playlist = pl
        self.current_video = None
        was_empty = self.playlist_detail_stack.currentIndex() == 0
        self._load_playlist_detail(pl)
        if was_empty:
            cross_fade_stack(self.playlist_detail_stack, 1, duration=220)
        else:
            self.playlist_detail_stack.setCurrentIndex(1)

    def _load_playlist_detail(self, pl: Playlist) -> None:
        self.playlist_title_label.setText(pl.title)
        if pl.uploader:
            self.playlist_uploader_label.setText(f"by {pl.uploader}")
            self.playlist_uploader_label.setVisible(True)
        else:
            self.playlist_uploader_label.setVisible(False)

        self.pl_count_box._value.setText(str(pl.total))
        self.pl_watched_box._value.setText(f"{pl.watched}")
        self.pl_fetched_box._value.setText(
            self._format_fetched(pl.fetched_at) if pl.fetched_at else "—"
        )
        # Animate from the current bar value to the new percent so switching
        # between playlists glides rather than snapping. animate_progress is
        # a no-op when start == target, so this is free on idempotent loads.
        animate_progress(self.pl_progress, pl.percent, duration=320)
        self._rebuild_playlist_tags()
        self._rebuild_video_list(pl)
        self._set_video_notes_state(None)
        # Load playlist-level notes — guard textChanged so loading doesn't
        # trip the debounced save back into the same playlist.
        self._loading_playlist_details = True
        try:
            self.playlist_notes_edit.setPlainText(pl.notes)
        finally:
            self._loading_playlist_details = False

    def _rebuild_video_list(self, pl: Playlist) -> None:
        self.video_list_widget.blockSignals(True)
        self.video_list_widget.clear()
        self._video_items.clear()
        for v in pl.videos:
            row = VideoRow(v)
            row.completion_changed.connect(
                lambda checked, vid=v.id: self._on_video_completion(vid, checked)
            )
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, v.id)
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            self.video_list_widget.addItem(item)
            self.video_list_widget.setItemWidget(item, row)
            self._video_items[v.id] = item
        self.video_list_widget.blockSignals(False)

    def _on_video_select(self, current, _previous) -> None:
        if self._video_notes_save_timer.isActive():
            self._video_notes_save_timer.stop()
            self._save_video_notes()
        if not current or not self.current_playlist:
            self.current_video = None
            self._set_video_notes_state(None)
            return
        vid = current.data(Qt.ItemDataRole.UserRole)
        video = next((v for v in self.current_playlist.videos if v.id == vid), None)
        self.current_video = video
        self._set_video_notes_state(video)

    def _set_video_notes_state(self, video: Video | None) -> None:
        self._loading_video_details = True
        try:
            if video is None:
                self.video_notes_label.setText("NOTES")
                self.video_notes_edit.setPlainText("")
                self.video_notes_edit.setEnabled(False)
                self.video_notes_edit.setPlaceholderText(
                    "Select a video to write notes…"
                )
            else:
                truncated = video.title if len(video.title) <= 60 else video.title[:57] + "…"
                self.video_notes_label.setText(f"NOTES · {truncated}")
                self.video_notes_edit.setPlainText(video.notes)
                self.video_notes_edit.setEnabled(True)
                self.video_notes_edit.setPlaceholderText(
                    "Write notes for this video…"
                )
        finally:
            self._loading_video_details = False

    def _on_video_notes_changed(self) -> None:
        if self._loading_video_details or self.current_video is None:
            return
        self._video_notes_save_timer.start()

    def _save_video_notes(self) -> None:
        if not self.current_video or not self.store:
            return
        new_notes = self.video_notes_edit.toPlainText()
        if new_notes == self.current_video.notes:
            return
        had_notes = bool(self.current_video.notes.strip())
        self.current_video.notes = new_notes
        self.store.save()
        has_notes = bool(new_notes.strip())
        if has_notes != had_notes:
            self._update_video_row_notes(self.current_video.id, has_notes)

    def _on_playlist_notes_changed(self) -> None:
        if self._loading_playlist_details or self.current_playlist is None:
            return
        self._playlist_notes_save_timer.start()

    def _save_playlist_notes(self) -> None:
        if not self.current_playlist or not self.store:
            return
        new_notes = self.playlist_notes_edit.toPlainText()
        if new_notes == self.current_playlist.notes:
            return
        had_notes = bool(self.current_playlist.notes.strip())
        self.current_playlist.notes = new_notes
        self.store.save()
        has_notes = bool(new_notes.strip())
        if has_notes != had_notes:
            self._update_playlist_row_notes(self.current_playlist.id, has_notes)

    def _update_playlist_row_notes(self, pid: str, has_notes: bool) -> None:
        item = self._playlist_items.get(pid)
        if not item:
            return
        row = self.playlists_list_widget.itemWidget(item)
        if isinstance(row, PlaylistRow):
            row.set_has_notes(has_notes)

    def _update_video_row_notes(self, vid: str, has_notes: bool) -> None:
        item = self._video_items.get(vid)
        if not item:
            return
        row = self.video_list_widget.itemWidget(item)
        if isinstance(row, VideoRow):
            row.set_has_notes(has_notes)

    def _on_video_completion(self, vid: str, checked: bool) -> None:
        if not self.current_playlist or not self.store:
            return
        video = next(
            (v for v in self.current_playlist.videos if v.id == vid), None
        )
        if not video:
            return
        video.completed = checked
        self.store.save()
        self._refresh_playlist_progress()
        self._refresh_playlist_row_meta(self.current_playlist.id)

    def _refresh_playlist_progress(self) -> None:
        if not self.current_playlist:
            return
        pl = self.current_playlist
        self.pl_watched_box._value.setText(str(pl.watched))
        animate_progress(self.pl_progress, pl.percent, duration=280)

    def _refresh_playlist_row_meta(self, pid: str) -> None:
        item = self._playlist_items.get(pid)
        if not item:
            return
        row = self.playlists_list_widget.itemWidget(item)
        if isinstance(row, PlaylistRow):
            row.refresh()
            item.setSizeHint(QSize(0, row.sizeHint().height()))

    def _rebuild_playlist_tags(self, *, editing: bool = False) -> None:
        self._playlist_tag_editor = None
        while self.playlist_tags_layout.count():
            it = self.playlist_tags_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        if not self.current_playlist:
            self.playlist_palette_wrap.setVisible(False)
            return

        for t in self.current_playlist.tags:
            chip = TagChip(
                t, tag_color(t, self.store.tag_colors), removable=True,
            )
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"Click × to remove · Right-click for color · '{t}'")
            chip.remove_clicked.connect(partial(self._remove_playlist_tag, t))
            chip.right_clicked.connect(partial(self._show_color_picker, t))
            self.playlist_tags_layout.addWidget(chip)

        if editing:
            editor = TagEditor()
            existing: list[str] = []
            if self.store:
                existing = [
                    t for t in self.store.all_tags()
                    if t not in self.current_playlist.tags
                ]
                if existing:
                    completer = QCompleter(existing, editor)
                    completer.setCaseSensitivity(
                        Qt.CaseSensitivity.CaseInsensitive
                    )
                    completer.setFilterMode(Qt.MatchFlag.MatchContains)
                    editor.setCompleter(completer)
            editor.submitted.connect(self._on_playlist_tag_submit)
            editor.cancelled.connect(self._exit_playlist_tag_edit_mode)
            self.playlist_tags_layout.addWidget(editor)
            self._playlist_tag_editor = editor
            QTimer.singleShot(0, editor.setFocus)
            self._populate_tag_palette(
                self.playlist_palette_layout, existing,
                self._on_playlist_tag_submit,
            )
            self.playlist_palette_wrap.setVisible(bool(existing))
        else:
            add_btn = QPushButton("+ add tag")
            add_btn.setObjectName("ghost")
            add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            add_btn.clicked.connect(self._enter_playlist_tag_edit_mode)
            self.playlist_tags_layout.addWidget(add_btn)
            self._clear_layout(self.playlist_palette_layout)
            self.playlist_palette_wrap.setVisible(False)
        self.playlist_tags_layout.addStretch()

    def _enter_playlist_tag_edit_mode(self) -> None:
        self._rebuild_playlist_tags(editing=True)
        if self.playlist_palette_wrap.isVisible():
            self.playlist_palette_wrap.setVisible(False)
            slide_in_height(self.playlist_palette_wrap, duration=220)

    def _exit_playlist_tag_edit_mode(self) -> None:
        if self.playlist_palette_wrap.isVisible():
            slide_out_height(
                self.playlist_palette_wrap,
                duration=180,
                on_done=lambda: self._rebuild_playlist_tags(editing=False),
            )
        else:
            self._rebuild_playlist_tags(editing=False)

    def _on_playlist_tag_submit(self, tag: str) -> None:
        if not self.current_playlist or not self.store:
            return
        tag = tag.strip().lower().lstrip("#")
        if not tag or tag in self.current_playlist.tags:
            self._exit_playlist_tag_edit_mode()
            return
        self.current_playlist.tags.append(tag)
        self.store.save()
        self._exit_playlist_tag_edit_mode()
        self._update_playlist_row_tags(self.current_playlist.id)

    def _remove_playlist_tag(self, tag: str) -> None:
        if not self.current_playlist or not self.store:
            return
        if tag in self.current_playlist.tags:
            self.current_playlist.tags.remove(tag)
            self.store.prune_unused_tag_colors()
            self.store.save()
            self._rebuild_playlist_tags()
            self._update_playlist_row_tags(self.current_playlist.id)
            if self.tag_filter == tag and tag not in self.store.all_tags():
                self._set_tag_filter(None)

    def _update_playlist_row_tags(self, pid: str) -> None:
        item = self._playlist_items.get(pid)
        if not item:
            return
        row = self.playlists_list_widget.itemWidget(item)
        if isinstance(row, PlaylistRow):
            row.refresh_tags()
            item.setSizeHint(QSize(0, row.sizeHint().height()))

    def _refresh_current_playlist(self) -> None:
        if not self.current_playlist:
            return
        self.pl_refresh_btn.setEnabled(False)
        self.pl_refresh_btn.setText("Refreshing…")
        self._kick_fetch(self.current_playlist.url, on_done=self._on_refresh_done)

    def _on_refresh_done(self, _url: str, data: dict | None, err: str | None) -> None:
        self.pl_refresh_btn.setEnabled(True)
        self.pl_refresh_btn.setText("Refresh")
        if not self.current_playlist or not self.store:
            return
        if err or not data:
            QMessageBox.warning(
                self, "Projectum", err or "Failed to refresh playlist."
            )
            return
        # Flush pending notes so the upcoming _load_playlist_detail doesn't
        # overwrite the editor with stale persisted text.
        if self._playlist_notes_save_timer.isActive():
            self._playlist_notes_save_timer.stop()
            self._save_playlist_notes()
        self.current_playlist.merge_fetch(data)
        self.store.save()
        self._load_playlist_detail(self.current_playlist)
        self._refresh_playlist_row_meta(self.current_playlist.id)

    def _remove_current_playlist(self) -> None:
        if not self.current_playlist or not self.store:
            return
        pl = self.current_playlist
        confirm = QMessageBox.question(
            self,
            "Remove playlist",
            f"Remove '{pl.title}' and all notes?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        # Drop pending note saves — the target is about to be destroyed.
        if self._playlist_notes_save_timer.isActive():
            self._playlist_notes_save_timer.stop()
        if self._video_notes_save_timer.isActive():
            self._video_notes_save_timer.stop()
        pid = pl.id
        self.store.remove_playlist(pid)
        self.store.prune_unused_tag_colors()
        self.store.save()
        item = self._playlist_items.pop(pid, None)

        def _drop_row():
            if item is None:
                return
            self.playlists_list_widget.blockSignals(True)
            row = self.playlists_list_widget.row(item)
            if row >= 0:
                self.playlists_list_widget.takeItem(row)
            self.playlists_list_widget.setCurrentItem(None)
            self.playlists_list_widget.blockSignals(False)

        if item is not None:
            # Collapse the item height to 0 — the rows below shift up
            # smoothly. Faded opacity on the row would glitch because the
            # row contains TagChips (custom paintEvent).
            collapse_list_item(
                self.playlists_list_widget, item, duration=220, on_done=_drop_row,
            )
        else:
            _drop_row()

        # Detail panel crossfades back to the empty placeholder in parallel.
        self.current_playlist = None
        self.current_video = None
        self._video_items.clear()
        self.video_list_widget.clear()
        cross_fade_stack(self.playlist_detail_stack, 0, duration=220)
        self._update_playlists_empty_hint()

    def _open_current_playlist_url(self) -> None:
        if not self.current_playlist:
            return
        QDesktopServices.openUrl(QUrl(self.current_playlist.url))

    @staticmethod
    def _format_fetched(iso: str) -> str:
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return iso
        return MainWindow._format_date(dt)

    # ─── Settings ────────────────────────────────────────────────

    # ─── Command palette ────────────────────────────────────────

    def _open_command_palette(self) -> None:
        if self.store is None:
            return
        if self._command_palette is not None and self._command_palette.isVisible():
            self._command_palette.input.setFocus()
            self._command_palette.input.selectAll()
            return
        palette = CommandPalette(parent=self)
        palette.query_changed.connect(self._palette_search)
        palette.activated.connect(self._palette_activate)
        palette.closed.connect(self._on_palette_closed)
        self._command_palette = palette
        # Center over the main window, near the top.
        palette.adjustSize()
        wgeo = self.frameGeometry()
        palette.move(
            wgeo.center().x() - palette.width() // 2,
            wgeo.top() + 80,
        )
        # Seed with no-query results so the user sees pinned items immediately.
        self._palette_search("")
        palette.setWindowOpacity(0.0)
        palette.show()
        palette.raise_()
        palette.activateWindow()
        palette.input.setFocus()
        fade_window(palette, 1.0, duration=140)

    def _on_palette_closed(self) -> None:
        self._command_palette = None

    def _palette_search(self, query: str) -> None:
        if self._command_palette is None or self.store is None:
            return
        q = query.strip().casefold()
        results: list[dict] = []

        def score(label: str) -> int:
            lo = label.casefold()
            if q and lo.startswith(q):
                return 0
            if q and q in lo:
                return 1
            return 2

        # Projects — match name, tags, or notes.
        for p in self.store.sorted_projects():
            name_lo = p.name.casefold()
            tag_hit = any(q in t.casefold() for t in p.tags) if q else False
            notes_hit = q in p.notes.casefold() if q else False
            if not q or q in name_lo or tag_hit or notes_hit:
                sub_bits = []
                if p.tags:
                    sub_bits.append(" ".join(f"#{t}" for t in p.tags[:3]))
                if p.completed:
                    sub_bits.append("done")
                if p.pinned:
                    sub_bits.append("pinned")
                results.append({
                    "type": "project",
                    "label": p.name,
                    "sublabel": " · ".join(sub_bits),
                    "_score": score(p.name),
                    "_key": p.name,
                })

        # Playlists.
        for pl in self.store.sorted_playlists():
            title_lo = pl.title.casefold()
            uploader_lo = pl.uploader.casefold()
            tag_hit = any(q in t.casefold() for t in pl.tags) if q else False
            notes_hit = q in pl.notes.casefold() if q else False
            if not q or q in title_lo or q in uploader_lo or tag_hit or notes_hit:
                sub_bits = []
                if pl.uploader:
                    sub_bits.append(f"by {pl.uploader}")
                sub_bits.append(f"{pl.watched}/{pl.total} watched")
                if pl.pinned:
                    sub_bits.append("pinned")
                results.append({
                    "type": "playlist",
                    "label": pl.title,
                    "sublabel": " · ".join(sub_bits),
                    "_score": score(pl.title),
                    "_key": pl.id,
                })

        # Videos — only when there's a query (full library would be huge).
        if q:
            for pl in self.store.playlists:
                for v in pl.videos:
                    title_lo = v.title.casefold()
                    notes_hit = q in v.notes.casefold()
                    if q in title_lo or notes_hit:
                        results.append({
                            "type": "video",
                            "label": v.title,
                            "sublabel": f"in {pl.title}",
                            "_score": score(v.title),
                            "_key": (pl.id, v.id),
                        })

        # Tags.
        if q:
            for t in self.store.all_tags():
                if q in t.casefold():
                    results.append({
                        "type": "tag",
                        "label": f"#{t}",
                        "sublabel": "filter projects by tag",
                        "_score": score(t),
                        "_key": t,
                    })

        # Global notes — single hit summarizing first match line.
        if q and self.store.notes and q in self.store.notes.casefold():
            # First line containing the match for the sublabel.
            lo = self.store.notes.casefold()
            idx = lo.find(q)
            line_start = self.store.notes.rfind("\n", 0, idx) + 1
            line_end = self.store.notes.find("\n", idx)
            if line_end == -1:
                line_end = len(self.store.notes)
            snippet = self.store.notes[line_start:line_end].strip()
            if len(snippet) > 80:
                snippet = snippet[:77] + "…"
            results.append({
                "type": "notes",
                "label": "Open notes & jump to match",
                "sublabel": snippet,
                "_score": 0,
                "_key": query,
            })

        # Sort: score asc, then alphabetical label.
        results.sort(key=lambda r: (r["_score"], r["label"].casefold()))
        # Cap to keep the palette snappy on huge libraries.
        self._command_palette.set_results(results[:50])

    def _palette_activate(self, result: dict) -> None:
        kind = result.get("type")
        key = result.get("_key")
        # Close the palette before navigating so the next view paints uncovered.
        if self._command_palette is not None:
            self._command_palette.close()
        if kind == "project":
            self._goto_tab("projects")
            it = self._row_items.get(key)
            if it is not None:
                self.list_widget.setCurrentItem(it)
        elif kind == "playlist":
            self._goto_tab("playlists")
            it = self._playlist_items.get(key)
            if it is not None:
                self.playlists_list_widget.setCurrentItem(it)
        elif kind == "video":
            pid, vid = key
            self._goto_tab("playlists")
            pl_it = self._playlist_items.get(pid)
            if pl_it is not None:
                self.playlists_list_widget.setCurrentItem(pl_it)
                # Now select the video row (rebuilt synchronously by select).
                v_it = self._video_items.get(vid)
                if v_it is not None:
                    self.video_list_widget.setCurrentItem(v_it)
        elif kind == "tag":
            self._goto_tab("projects")
            self._set_tag_filter(key)
        elif kind == "notes":
            self._goto_tab("notes")
            self.notes_search_input.setText(key)
            self.notes_search_input.setFocus()

    def _goto_tab(self, key: str) -> None:
        for btn in self._tab_group.buttons():
            if btn.property("tab_key") == key:
                btn.setChecked(True)
                btn.click()
                return

    def _open_settings(self) -> None:
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dialog = SettingsDialog(
            theme.current_theme_name(),
            theme.current_font_family(),
            theme.current_font_size(),
            parent=self,
        )
        dialog.settings_changed.connect(self._apply_settings)
        dialog.closed.connect(self._on_settings_closed)
        self._settings_dialog = dialog
        # Center over the main window and fade in.
        dialog.adjustSize()
        center = self.frameGeometry().center()
        dialog.move(
            center.x() - dialog.width() // 2,
            center.y() - dialog.height() // 2,
        )
        dialog.setWindowOpacity(0.0)
        dialog.show()
        fade_window(dialog, 1.0, duration=160)

    def _on_settings_closed(self) -> None:
        self._settings_dialog = None

    def _apply_settings(self, settings: dict) -> None:
        new_theme = settings.get("theme", theme.DEFAULT_THEME)
        new_family = settings.get("font_family", theme.DEFAULT_FONT_FAMILY)
        new_size = max(
            theme.FONT_SIZE_MIN,
            min(theme.FONT_SIZE_MAX, int(settings.get("font_size", theme.DEFAULT_FONT_SIZE))),
        )

        theme.apply_theme(new_theme)
        theme.apply_font(family=new_family, size=new_size)

        app = QApplication.instance()
        if app is not None:
            # Family change goes through QApplication.setFont — stylesheet
            # font-family rules don't reliably propagate to every widget
            # type. Stylesheet still carries the per-widget pixel sizes.
            from PySide6.QtGui import QFont
            new_font = QFont(new_family, new_size)
            app.setFont(new_font)
            app.setStyleSheet(theme.build_stylesheet())
            # QApplication.setFont only affects widgets that haven't had
            # setFont() called on them yet — already-instantiated widgets
            # keep their cached QFont. Re-broadcast the new font to the
            # main window so children inherit it.
            self.setFont(new_font)

        # Repaint custom-painted widgets that read theme.* dynamically.
        self._brand_mark.update()
        self._settings_btn.update()

        # Row widgets and detail panels bake some colors into setStyleSheet
        # calls during their __init__; rebuild the lists and re-load the
        # active detail panel so those colors get re-applied.
        if self.store:
            cur_proj_name = (
                self.current_project.name if self.current_project else None
            )
            cur_pl_id = (
                self.current_playlist.id if self.current_playlist else None
            )
            self._full_rebuild_list(preserve_name=cur_proj_name)
            self._rebuild_playlists_list()
            if self.current_project:
                self._load_detail(self.current_project)
            if cur_pl_id:
                pl_item = self._playlist_items.get(cur_pl_id)
                if pl_item is not None:
                    self.playlists_list_widget.setCurrentItem(pl_item)
            if self.current_playlist:
                self._load_playlist_detail(self.current_playlist)

        # Persist alongside other window state.
        cur = load_state()
        cur["settings"] = {
            "theme": new_theme,
            "font_family": new_family,
            "font_size": new_size,
        }
        save_state(cur)

    # ─── Window lifecycle ────────────────────────────────────────

    def eventFilter(self, obj, event) -> bool:
        if (
            obj is self._playlist_url_input
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            self._hide_playlist_url_input()
            self._set_playlist_error("")
            return True
        return super().eventFilter(obj, event)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            is_max = self.isMaximized() or self.isFullScreen()
            self._frame_wrapper.set_resize_enabled(not is_max)
            self._max_btn.update()
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        self._flush_pending_writes()
        state = load_state()
        state["geometry"] = bytes(self.saveGeometry()).hex()
        state["maximized"] = self.isMaximized()
        save_state(state)
        super().closeEvent(event)


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Projectum")
    app.setApplicationDisplayName("Projectum")
    app.setDesktopFileName("projectum")
    app.setOrganizationName("wleeaf")
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    # Apply persisted settings BEFORE creating MainWindow so the first
    # paint already uses the right theme + font.
    state = load_state()
    settings = state.get("settings") or {}
    initial_family = settings.get("font_family", theme.DEFAULT_FONT_FAMILY)
    initial_size = int(settings.get("font_size", theme.DEFAULT_FONT_SIZE))
    theme.apply_theme(settings.get("theme", theme.DEFAULT_THEME))
    theme.apply_font(family=initial_family, size=initial_size)
    from PySide6.QtGui import QFont
    app.setFont(QFont(initial_family, initial_size))
    app.setStyleSheet(theme.build_stylesheet())

    focus_manager = FocusManager()
    app.installEventFilter(focus_manager)
    app._focus_manager = focus_manager  # keep alive

    w = MainWindow()

    geom_hex = state.get("geometry")
    if isinstance(geom_hex, str):
        try:
            w.restoreGeometry(bytes.fromhex(geom_hex))
        except ValueError:
            pass
    if state.get("maximized"):
        w.showMaximized()
    else:
        w.show()

    initial: Path | None = None
    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1]).expanduser().resolve()
        if candidate.is_dir():
            initial = candidate
    if initial is None:
        last = state.get("last_folder")
        if last and Path(last).is_dir():
            initial = Path(last)
    if initial is not None:
        w.load_folder(initial)

    return app.exec()
