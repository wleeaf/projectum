"""Main window for Projectum."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
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
from . import calendar as cal
from . import links as links_mod
from .links import LinkStore, make_ref
from . import theme
from .theme import tag_color
from .anims import (
    animate_progress, collapse_list_item, cross_fade_stack, cross_fade_swap,
    fade_in, fade_out, fade_window, slide_in_height, slide_out_height,
    SmoothScrollFilter,
)
from .widgets import (
    BrandMark, CalendarScanRunnable, CalendarView, ColorPickerPopup,
    CommandPalette, CompletionToggle, FlowLayout, FrameWrapper, GitRunnable,
    IconButton, LinksDialog, MarkdownHighlighter, PlaylistRow, ProjectRow,
    ScheduleDialog, SettingsDialog, SizeRunnable, TagChip, TagEditor, TitleBar,
    TodoRow, UpdateBanner, VideoRow, WindowControlButton,
)
from .youtube import PlaylistFetchRunnable
from .update import UpdateCheckRunnable
from . import __version__ as APP_VERSION


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

# How many recently-opened folders to persist. The global Calendar scans all of
# them, so we keep a long history; the Recent ▾ menu shows only the newest few.
RECENT_FOLDERS_CAP = 60
RECENT_MENU_LIMIT = 12


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
        self._calendar_items: list = []
        self._calendar_scan_gen = 0
        # Strong refs to in-flight scan runnables. Without this the local
        # runnable (and its signals QObject) can be GC'd before the queued
        # cross-thread `done` is delivered, dropping the result.
        self._calendar_runnables: set = set()
        self._schedule_dialog: ScheduleDialog | None = None
        # Global relation graph (cross-folder) + a cached entity index used to
        # resolve/search links. The store is one file we own, written on the UI
        # thread; the index is rebuilt from a read-only cross-folder scan.
        self._link_store = LinkStore(state_dir() / "links.json")
        self._links_dialog: LinksDialog | None = None
        self._entity_index: dict = {}

        # Playlists state
        self.current_tab: str = "projects"
        self.current_playlist: Playlist | None = None
        self.current_video: Video | None = None
        self._loading_video_details = False
        self._playlist_items: dict[str, QListWidgetItem] = {}
        self._video_items: dict[str, QListWidgetItem] = {}
        self._todo_items: dict[str, QListWidgetItem] = {}
        self._playlist_url_input: QLineEdit | None = None
        self._playlist_tag_editor: TagEditor | None = None
        self._pending_fetches: dict[str, tuple[object, object]] = {}
        # Playlist ids with an in-flight Refresh fetch. The Refresh button is
        # a single shared widget in the detail panel, so its enabled/label
        # state is derived from this set + the current selection rather than
        # toggled imperatively (which left it stuck when the selection changed
        # mid-fetch).
        self._refreshing_playlist_ids: set[str] = set()
        # Name of a selection hidden by the active search/filter, so it can be
        # reselected when it becomes visible again.
        self._hidden_selection: str | None = None
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

        self._notes_save_timer = QTimer(self)
        self._notes_save_timer.setSingleShot(True)
        self._notes_save_timer.setInterval(450)
        self._notes_save_timer.timeout.connect(self._save_notes)

        # Debounce search filtering so each keystroke doesn't re-scan every
        # row (and reselect/reload the detail panel) synchronously — that
        # adds up on large folders and makes typing feel laggy.
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(90)
        self._search_debounce.timeout.connect(self._apply_filter)

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
        # Smooth-scroll filters live as long as the window — held in a list
        # so they aren't garbage collected.
        self._scroll_filters: list[SmoothScrollFilter] = []

        self._build_ui()
        # Install smooth-scroll on every list and notes editor.
        for view in (
            self.list_widget,
            self.playlists_list_widget,
            self.video_list_widget,
            self.todo_list_widget,
            self.notes_edit,
            self.playlist_notes_edit,
            self.video_notes_edit,
            self.global_notes_edit,
        ):
            self._scroll_filters.append(SmoothScrollFilter.install(view))
        # Stylesheet applied at QApplication level in run() so popups and
        # the settings dialog inherit. Theme changes call apply_app_styling.
        self._bind_shortcuts()

        # Check for a newer release shortly after launch (off-thread, opt-out).
        QTimer.singleShot(2500, self._maybe_check_updates)

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

        # Update banner (hidden until a newer release is found).
        self._update_banner = UpdateBanner()
        self._update_banner.download_clicked.connect(self._open_update_url)
        self._update_banner.dismissed.connect(self._dismiss_update)
        self._update_url = ""
        self._update_version = ""
        layout.addWidget(self._update_banner)

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

        self.recent_btn = QPushButton("Recent ▾")
        self.recent_btn.setToolTip("Recently opened folders")
        self.recent_btn.clicked.connect(self._show_recent_menu)
        h.addWidget(self.recent_btn)

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
        self.todo_view = self._build_todo_view()
        self.calendar_view = self._build_calendar_view()
        self.notes_view = self._build_notes_view()
        self.content_stack.addWidget(self.projects_view)
        self.content_stack.addWidget(self.playlists_view)
        self.content_stack.addWidget(self.todo_view)
        self.content_stack.addWidget(self.calendar_view)
        self.content_stack.addWidget(self.notes_view)
        v.addWidget(self.content_stack, 1)

        return container

    def _build_calendar_view(self) -> QWidget:
        view = CalendarView()
        view.item_activated.connect(self._open_schedule_dialog)
        view.item_context.connect(self._on_calendar_item_context)
        view.item_rescheduled.connect(self._apply_schedule)  # drag move/resize/drop
        return view

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
            ("todos", "Todo"),
            ("calendar", "Calendar"),
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
            "todos": self.todo_view,
            "calendar": self.calendar_view,
            "notes": self.notes_view,
        }.get(key, self.projects_view)
        cross_fade_stack(
            self.content_stack, self.content_stack.indexOf(target), duration=160,
        )
        if key == "calendar":
            self._rescan_calendar()

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
        msg.setObjectName("emptyHint")  # recolors with the theme stylesheet
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        self.git_box = self._make_meta("Git")
        self.status_box = self._make_meta("Status")
        self.tested_box = self._make_toggle_meta("Tested", color_key="INFO")
        # The inner toggle is the actionable bit; wire its signal here so
        # the rest of the meta-box helper stays generic.
        self.tested_toggle = self.tested_box._toggle  # type: ignore[attr-defined]
        self.tested_toggle.setToolTip("Mark this project as tested")
        self.tested_toggle.toggled.connect(self._on_tested_toggle)
        for w in (self.modified_box, self.size_box, self.git_box,
                  self.status_box, self.tested_box):
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

        notes_label = QLabel("NOTES")
        notes_label.setObjectName("sectionLabel")
        dv.addWidget(notes_label)
        dv.addSpacing(8)

        self.notes_edit = QTextEdit()
        self.notes_edit.setObjectName("notes")
        self.notes_edit.setPlaceholderText("Write something about this project…")
        self.notes_edit.textChanged.connect(self._on_notes_changed)
        self.notes_highlighter = MarkdownHighlighter(self.notes_edit.document())
        dv.addWidget(self.notes_edit, 1)

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
        msg.setObjectName("emptyHint")  # recolors with the theme stylesheet
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        pl_notes_label = QLabel("PLAYLIST NOTES")
        pl_notes_label.setObjectName("sectionLabel")
        right_col.addWidget(pl_notes_label)

        self.playlist_notes_edit = QTextEdit()
        self.playlist_notes_edit.setObjectName("notes")
        self.playlist_notes_edit.setPlaceholderText(
            "Write something about this playlist…"
        )
        self.playlist_notes_edit.textChanged.connect(
            self._on_playlist_notes_changed
        )
        self.playlist_notes_highlighter = MarkdownHighlighter(
            self.playlist_notes_edit.document()
        )
        right_col.addWidget(self.playlist_notes_edit, 1)
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
        self.video_notes_label = QLabel("NOTES")
        self.video_notes_label.setObjectName("sectionLabel")
        nw.addWidget(self.video_notes_label)

        self.video_notes_edit = QTextEdit()
        self.video_notes_edit.setObjectName("notes")
        self.video_notes_edit.setPlaceholderText("Select a video to write notes…")
        self.video_notes_edit.setEnabled(False)
        self.video_notes_edit.textChanged.connect(self._on_video_notes_changed)
        self.video_notes_highlighter = MarkdownHighlighter(
            self.video_notes_edit.document()
        )
        nw.addWidget(self.video_notes_edit, 1)
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
        v.addLayout(search_row)

        self.global_notes_edit = QTextEdit()
        self.global_notes_edit.setObjectName("notes")
        self.global_notes_edit.setPlaceholderText("Write anything here…")
        self.global_notes_edit.textChanged.connect(self._on_global_notes_changed)
        # Re-highlight matches after the user types so highlights stay current.
        self.global_notes_edit.textChanged.connect(self._refresh_notes_highlights)
        self.global_notes_highlighter = MarkdownHighlighter(
            self.global_notes_edit.document()
        )
        v.addWidget(self.global_notes_edit, 1)

        # Cached search state.
        self._notes_match_count = 0

        # Shift+Enter for previous match while focus is in the search box.
        prev_sc = QShortcut(
            QKeySequence("Shift+Return"), self.notes_search_input,
            activated=self._notes_search_prev,
        )
        prev_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        return container

    # ─── Todo view ───────────────────────────────────────────────

    def _build_todo_view(self) -> QWidget:
        container = QWidget()
        container.setObjectName("detailPanel")
        v = QVBoxLayout(container)
        v.setContentsMargins(36, 26, 36, 24)
        v.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("TODO")
        title.setObjectName("sectionLabel")
        header.addWidget(title)
        header.addStretch()
        self.todo_counter = QLabel("")
        self.todo_counter.setObjectName("subtitle")
        header.addWidget(self.todo_counter)
        v.addLayout(header)

        self.todo_input = QLineEdit()
        self.todo_input.setPlaceholderText("Add a task — press Enter")
        self.todo_input.setClearButtonEnabled(True)
        self.todo_input.returnPressed.connect(self._add_todo)
        v.addWidget(self.todo_input)

        self.todo_list_widget = QListWidget()
        self.todo_list_widget.setVerticalScrollMode(
            QListWidget.ScrollMode.ScrollPerPixel
        )
        self.todo_list_widget.setUniformItemSizes(False)
        self.todo_list_widget.setFrameShape(QListWidget.Shape.NoFrame)
        self.todo_list_widget.setDragDropMode(
            QListWidget.DragDropMode.InternalMove
        )
        self.todo_list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.todo_list_widget.model().rowsMoved.connect(self._persist_todo_order)
        self.todo_list_widget.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.todo_list_widget.customContextMenuRequested.connect(
            self._show_todo_context_menu
        )
        v.addWidget(self.todo_list_widget, 1)

        self.todo_empty_hint = QLabel(
            "No tasks yet.\nAdd one above to get started."
        )
        self.todo_empty_hint.setObjectName("emptyHint")
        self.todo_empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.todo_empty_hint, 1)
        return container

    def _rebuild_todo_list(self) -> None:
        self.todo_list_widget.blockSignals(True)
        self.todo_list_widget.clear()
        self._todo_items.clear()
        if self.store:
            for todo in self.store.sorted_todos():
                self._append_todo_row(todo)
        self.todo_list_widget.blockSignals(False)
        self._update_todo_counter()
        self._update_todo_empty_hint()

    def _make_todo_row(self, todo) -> TodoRow:
        row = TodoRow(todo)
        row.toggled.connect(
            lambda checked, tid=todo.id: self._on_todo_toggled(tid, checked)
        )
        row.remove_clicked.connect(lambda tid=todo.id: self._remove_todo(tid))
        row.edited.connect(
            lambda text, tid=todo.id: self._on_todo_edited(tid, text)
        )
        return row

    def _append_todo_row(self, todo) -> QListWidgetItem:
        row = self._make_todo_row(todo)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, todo.id)
        item.setSizeHint(QSize(0, row.sizeHint().height()))
        self.todo_list_widget.addItem(item)
        self.todo_list_widget.setItemWidget(item, row)
        self._todo_items[todo.id] = item
        return item

    def _add_todo(self) -> None:
        if not self.store:
            return
        text = self.todo_input.text().strip()
        if not text:
            return
        todo = self.store.add_todo(text)
        self.todo_input.clear()
        self._append_todo_row(todo)
        self._update_todo_counter()
        self._update_todo_empty_hint()
        item = self._todo_items.get(todo.id)
        if item is not None:
            self.todo_list_widget.scrollToItem(item)

    def _on_todo_toggled(self, todo_id: str, checked: bool) -> None:
        if not self.store:
            return
        todo = self.store.get_todo(todo_id)
        if todo is None:
            return
        todo.done = checked
        self.store.save()
        self._update_todo_counter()

    def _remove_todo(self, todo_id: str) -> None:
        if not self.store:
            return
        self.store.remove_todo(todo_id)
        self._prune_links_for("todo", todo_id)
        item = self._todo_items.pop(todo_id, None)

        def _drop():
            if item is None:
                return
            row = self.todo_list_widget.row(item)
            if row >= 0:
                self.todo_list_widget.takeItem(row)
            self._update_todo_empty_hint()

        if item is not None:
            collapse_list_item(
                self.todo_list_widget, item, duration=200, on_done=_drop
            )
        else:
            self._update_todo_empty_hint()
        self._update_todo_counter()

    def _on_todo_edited(self, todo_id: str, text: str) -> None:
        if not self.store:
            return
        todo = self.store.get_todo(todo_id)
        if todo is None:
            return
        todo.text = text
        self.store.save()
        # Word-wrap may have changed the row height.
        item = self._todo_items.get(todo_id)
        if item is not None:
            row = self.todo_list_widget.itemWidget(item)
            if isinstance(row, TodoRow):
                self._resize_row_deferred(item, row)

    def _persist_todo_order(self, *_args) -> None:
        if not self.store:
            return
        # InternalMove drops the moved row's widget and invalidates cached
        # item pointers — reconcile (deferred) like projects/playlists.
        QTimer.singleShot(0, self._reconcile_todo_rows_after_move)

    def _reconcile_todo_rows_after_move(self) -> None:
        if not self.store:
            return
        self._todo_items.clear()
        ids: list[str] = []
        for row in range(self.todo_list_widget.count()):
            it = self.todo_list_widget.item(row)
            if it is None:
                continue
            tid = it.data(Qt.ItemDataRole.UserRole)
            ids.append(tid)
            self._todo_items[tid] = it
            if self.todo_list_widget.itemWidget(it) is None:
                todo = self.store.get_todo(tid)
                if todo is not None:
                    row_w = self._make_todo_row(todo)
                    it.setSizeHint(QSize(0, row_w.sizeHint().height()))
                    self.todo_list_widget.setItemWidget(it, row_w)
        self.store.reorder_todos(ids)
        self.store.save()

    def _update_todo_counter(self) -> None:
        if not self.store:
            self.todo_counter.setText("")
            return
        done, total = self.store.todo_stats()
        self.todo_counter.setText(f"{done} of {total} done" if total else "")

    def _update_todo_empty_hint(self) -> None:
        empty = not self.store or not self.store.todos
        self.todo_empty_hint.setVisible(empty)
        self.todo_list_widget.setVisible(not empty)

    def _on_notes_search_changed(self, _text: str) -> None:
        self._refresh_notes_highlights()
        # Jump to the first match so the user sees something immediately.
        if self._notes_match_count and self.notes_search_input.text().strip():
            self._notes_search_seek(forward=True, from_start=True)

    def _refresh_notes_highlights(self) -> None:
        """Highlight all matches of the search query inside the notes editor.

        Uses ``QTextEdit.setExtraSelections`` rather than modifying the
        document's char formats — that way the search overlay coexists
        with the live Markdown highlighter instead of fighting it for
        ownership of each character's format.
        """
        from PySide6.QtGui import QTextCharFormat, QColor, QTextCursor
        from PySide6.QtWidgets import QTextEdit
        if not hasattr(self, "notes_search_input") or not hasattr(self, "global_notes_edit"):
            return
        query = self.notes_search_input.text()
        selections: list[QTextEdit.ExtraSelection] = []
        count = 0
        if query.strip():
            hl_fmt = QTextCharFormat()
            bg = QColor(theme.ACCENT)
            bg.setAlpha(95)
            hl_fmt.setBackground(bg)
            doc = self.global_notes_edit.document()
            cursor = QTextCursor(doc)
            while True:
                cursor = doc.find(query, cursor)
                if cursor.isNull():
                    break
                sel = QTextEdit.ExtraSelection()
                sel.cursor = cursor
                sel.format = hl_fmt
                selections.append(sel)
                count += 1
        self.global_notes_edit.setExtraSelections(selections)
        self._notes_match_count = count
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
        # Tab switching: Ctrl+1..5.
        for i, key in enumerate(
            ("projects", "playlists", "todos", "calendar", "notes"), start=1
        ):
            QShortcut(QKeySequence(f"Ctrl+{i}"), self,
                      activated=partial(self._goto_tab, key))
        # Toggle "done" on the selected project.
        QShortcut(QKeySequence("Ctrl+D"), self, activated=self._toggle_current_done)
        # Jump to the Todo tab and start a new task.
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self._focus_new_todo)

    def _toggle_current_done(self) -> None:
        if self.current_tab != "projects" or not self.current_project:
            return
        self.detail_toggle.setChecked(not self.detail_toggle.isChecked())

    def _focus_new_todo(self) -> None:
        self._goto_tab("todos")
        self.todo_input.setFocus()

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

    def _show_recent_menu(self) -> None:
        recents = load_state().get("recent_folders")
        if not isinstance(recents, list):
            recents = []
        current = str(self.store.root) if self.store else None
        home = str(Path.home())
        menu = QMenu(self)
        shown = 0
        seen: set[str] = set()
        for r in recents:
            if not isinstance(r, str) or r in seen:
                continue
            seen.add(r)
            # Filter dead paths at display time (folders get moved/deleted).
            if r == current or not Path(r).is_dir():
                continue
            label = ("~" + r[len(home):]) if r.startswith(home) else r
            act = QAction(label, menu)
            act.setToolTip(r)
            act.triggered.connect(partial(self.load_folder, Path(r)))
            menu.addAction(act)
            shown += 1
            if shown >= RECENT_MENU_LIMIT:
                break
        if shown == 0:
            empty = QAction("No recent folders", menu)
            empty.setEnabled(False)
            menu.addAction(empty)
        menu.addSeparator()
        choose = QAction("Choose folder…", menu)
        choose.triggered.connect(self.choose_folder)
        menu.addAction(choose)
        menu.exec(self.recent_btn.mapToGlobal(self.recent_btn.rect().bottomLeft()))

    def load_folder(self, path: Path) -> None:
        if not path.is_dir():
            QMessageBox.warning(self, "Projectum", f"Not a folder:\n{path}")
            return
        # Flush pending writes to the *previous* folder before swapping
        # stores — otherwise typed-but-unsaved notes get lost.
        self._flush_pending_writes()
        # Drop any in-flight playlist fetches from the previous folder so a
        # late result can't add/modify a playlist in the new folder. The
        # runnables keep running but _handle_fetch_done/_failed no-op when
        # the url is no longer in the dict.
        self._pending_fetches.clear()
        # Those dropped callbacks never restore the Refresh button / add-input,
        # so reset that UI here (otherwise it stays stuck "Refreshing…" /
        # "Fetching…" against the new folder).
        self._refreshing_playlist_ids.clear()
        self._reset_playlist_add_ui()
        try:
            self.store = ProjectStore(path)
        except Exception as e:
            QMessageBox.critical(self, "Projectum", f"Could not open folder:\n{e}")
            return

        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())
        self._watcher.addPath(str(path))

        # Merge into existing state — save_state writes the whole file, so a
        # bare write here would clobber persisted settings (theme/font) and
        # window geometry.
        st = load_state()
        st["last_folder"] = str(path)
        # Maintain a most-recent-first, de-duplicated recent-folders list.
        recents = st.get("recent_folders")
        if not isinstance(recents, list):
            recents = []
        p_str = str(path)
        recents = [r for r in recents if isinstance(r, str) and r != p_str]
        recents.insert(0, p_str)
        # The global Calendar scans this whole list, so keep a generous history
        # (the Recent ▾ menu still shows only the top RECENT_MENU_LIMIT). This
        # avoids silently dropping a folder's scheduled items off the calendar.
        st["recent_folders"] = recents[:RECENT_FOLDERS_CAP]
        save_state(st)

        if self.stack.currentWidget() is not self.main_view:
            cross_fade_stack(
                self.stack,
                self.stack.indexOf(self.main_view),
                duration=200,
            )
        self.folder_label.setText(str(path))
        self.folder_label.setToolTip(str(path))
        self.current_project = None
        self.current_playlist = None
        self.current_video = None
        self.tag_filter = None
        # Reset search + filter so they don't leak across folders.
        self.current_filter = "all"
        self.search_query = ""
        self._hidden_selection = None
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        for b in self.filter_group.buttons():
            if b.property("filter_key") == "all":
                b.setChecked(True)
        self._update_tag_filter_label()
        self._full_rebuild_list()
        self._update_stats()
        self._rebuild_playlists_list()
        self.todo_input.clear()
        self._rebuild_todo_list()
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
        self._rebuild_todo_list()
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
        todos = tuple(
            (t.id, t.text, t.done, t.position) for t in self.store.todos
        )
        return (projects, playlists, todos)

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

    def _make_project_row(self, project: Project) -> ProjectRow:
        row = ProjectRow(project, self.store)
        row.completion_changed.connect(
            lambda checked, p=project: self._on_row_completion(p, checked)
        )
        row.tag_right_clicked.connect(self._show_color_picker)
        return row

    def _append_row(self, project: Project) -> QListWidgetItem:
        row = self._make_project_row(project)
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
        ) or self._hidden_selection
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
            # The selection is visible again — restore it (setCurrentItem fires
            # _on_select, which reloads the detail panel) and forget the
            # remembered name.
            self._hidden_selection = None
            self.list_widget.setCurrentItem(target_item)
        elif preserve_name is None:
            # Filter/search hid the current selection (or nothing visible).
            # Flush any pending notes for it FIRST — clearing current_project
            # with signals blocked bypasses _on_select's flush, so the
            # debounced save would otherwise no-op and silently lose the edit.
            if self._notes_save_timer.isActive():
                self._notes_save_timer.stop()
                self._save_notes()
            # Remember the hidden selection so it can be reselected when it
            # becomes visible again (e.g. the search is cleared).
            if self.current_project is not None:
                self._hidden_selection = self.current_project.name
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
        self._search_debounce.start()

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
        folder = project.path
        menu = QMenu(self)
        toggle = QAction("Unpin from top" if project.pinned else "Pin to top", menu)
        toggle.triggered.connect(
            partial(self._toggle_project_pin, project.name)
        )
        menu.addAction(toggle)
        menu.addSeparator()
        relate = QAction("Links…", menu)
        relate.triggered.connect(partial(
            self._open_links_dialog,
            make_ref("project", str(self.store.root), project.name), project.name,
        ))
        menu.addAction(relate)
        menu.addSeparator()
        # Bulletproof actions first.
        reveal = QAction("Open folder", menu)
        reveal.triggered.connect(partial(self._reveal_in_file_manager, folder))
        menu.addAction(reveal)
        copy = QAction("Copy path", menu)
        copy.triggered.connect(partial(self._copy_to_clipboard, folder))
        menu.addAction(copy)
        term = QAction("Open in terminal", menu)
        term.triggered.connect(partial(self._open_in_terminal, folder))
        menu.addAction(term)
        # Editor action only when a known launcher is on PATH.
        editor = self._editor_launcher()
        if editor is not None:
            name_label, _exe = editor
            act = QAction(f"Open in {name_label}", menu)
            act.triggered.connect(partial(self._open_in_editor, folder))
            menu.addAction(act)
        menu.exec(self.list_widget.viewport().mapToGlobal(pos))

    # ─── Project quick actions ───────────────────────────────────

    def _reveal_in_file_manager(self, folder: str) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _copy_to_clipboard(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    @staticmethod
    def _editor_launcher() -> tuple[str, str] | None:
        """First available GUI editor on PATH, as (display name, executable)."""
        for label, exe in (
            ("VS Code", "code"), ("Cursor", "cursor"), ("Zed", "zed"),
            ("Sublime Text", "subl"),
        ):
            found = shutil.which(exe)
            if found:
                return label, found
        return None

    def _open_in_editor(self, folder: str) -> None:
        editor = self._editor_launcher()
        if editor is None:
            return
        try:
            subprocess.Popen([editor[1], folder])
        except OSError as e:
            QMessageBox.warning(self, "Projectum", f"Couldn't open the editor:\n{e}")

    def _open_in_terminal(self, folder: str) -> None:
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-a", "Terminal", folder])
                return
            if sys.platform.startswith("win"):
                # Prefer Windows Terminal, fall back to a cmd window.
                if shutil.which("wt"):
                    subprocess.Popen(["wt", "-d", folder])
                else:
                    subprocess.Popen("start cmd", cwd=folder, shell=True)
                return
            for term in (
                "x-terminal-emulator", "gnome-terminal", "konsole",
                "xfce4-terminal", "alacritty", "kitty", "xterm",
            ):
                exe = shutil.which(term)
                if exe:
                    subprocess.Popen([exe], cwd=folder)
                    return
            QMessageBox.information(
                self, "Projectum", "No terminal emulator was found on PATH."
            )
        except OSError as e:
            QMessageBox.warning(self, "Projectum", f"Couldn't open a terminal:\n{e}")

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
        # Qt's InternalMove drag-drop recreates the moved QListWidgetItem
        # WITHOUT its row widget and invalidates the pointers cached in
        # _row_items. Re-sync the cache and re-attach a row widget ONLY to the
        # item(s) that lost one — a full rebuild here would freeze for a few
        # hundred ms on large folders. Deferred so the drop fully unwinds.
        QTimer.singleShot(0, self._reconcile_project_rows_after_move)

    def _reconcile_project_rows_after_move(self) -> None:
        if not self.store:
            return
        self._row_items.clear()
        names: list[str] = []
        for row in range(self.list_widget.count()):
            it = self.list_widget.item(row)
            if it is None:
                continue
            name = it.data(Qt.ItemDataRole.UserRole)
            names.append(name)
            self._row_items[name] = it
            if self.list_widget.itemWidget(it) is None:
                project = self.store.projects.get(name)
                if project is not None:
                    row = self._make_project_row(project)
                    it.setSizeHint(QSize(0, row.sizeHint().height()))
                    self.list_widget.setItemWidget(it, row)
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
        relate = QAction("Links…", menu)
        relate.triggered.connect(partial(
            self._open_links_dialog,
            make_ref("playlist", str(self.store.root), pid), pl.title,
        ))
        menu.addAction(relate)
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
        # See _persist_project_order: re-attach only the moved row's widget
        # and re-sync the cache instead of a costly full rebuild.
        QTimer.singleShot(0, self._reconcile_playlist_rows_after_move)

    def _reconcile_playlist_rows_after_move(self) -> None:
        if not self.store:
            return
        self._playlist_items.clear()
        ids: list[str] = []
        for row in range(self.playlists_list_widget.count()):
            it = self.playlists_list_widget.item(row)
            if it is None:
                continue
            pid = it.data(Qt.ItemDataRole.UserRole)
            ids.append(pid)
            self._playlist_items[pid] = it
            if self.playlists_list_widget.itemWidget(it) is None:
                pl = self.store.get_playlist(pid)
                if pl is not None:
                    row_w = self._make_playlist_row(pl)
                    it.setSizeHint(QSize(0, row_w.sizeHint().height()))
                    self.playlists_list_widget.setItemWidget(it, row_w)
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
        # The tag filter only affects the projects list, so only offer tags
        # that some project actually uses — a playlist-only tag would hide
        # every project when selected.
        tags = sorted({t for p in self.store.projects.values() for t in p.tags})
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
            cross_fade_stack(self.detail_stack, 1, duration=160)
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
            self.git_box._value.setText("…")  # type: ignore[attr-defined]
            self._set_status_meta(p.completed)
            self._rebuild_tags()
        finally:
            self._loading_details = False
        # Kick off async size + git probes off the UI thread.
        self._size_pending_for = p.name
        runnable = SizeRunnable(p.name, Path(p.path))
        runnable.signals.done.connect(self._on_size_done)
        self._size_pool.start(runnable)
        git = GitRunnable(p.name, Path(p.path))
        git.signals.done.connect(self._on_git_done)
        self._size_pool.start(git)

    def _on_size_done(self, name: str, size: int) -> None:
        if not self.current_project or self.current_project.name != name:
            return
        self.size_box._value.setText(self._format_size(size))  # type: ignore[attr-defined]

    def _on_git_done(self, name: str, info) -> None:
        if not self.current_project or self.current_project.name != name:
            return
        val = self.git_box._value  # type: ignore[attr-defined]
        if not info:
            val.setText("—")
            val.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 13px;")
            val.setToolTip("Not a git repository")
            return
        dirty = info.get("dirty")
        branch = info.get("branch", "")
        val.setText(f"{branch} ●" if dirty else branch)
        color = theme.WARNING if dirty else theme.SUCCESS
        val.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: 600;")
        val.setToolTip("Uncommitted changes" if dirty else "Working tree clean")

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

    @staticmethod
    def _resize_row_deferred(item, row) -> None:
        """Commit a list row's height AFTER its layout settles.

        refresh_tags()/refresh() deleteLater() the old tag chips; the row's
        true height only resolves once that deletion is processed on the next
        event-loop tick. Reading sizeHint() synchronously here returns the
        no-tags height, which squeezes a row that still has a remaining tag.
        """
        def _apply():
            try:
                item.setSizeHint(QSize(0, row.sizeHint().height()))
            except RuntimeError:
                pass  # row/item gone (folder reloaded) before the tick fired
        QTimer.singleShot(0, _apply)

    def _update_row_tags(self, name: str) -> None:
        item = self._row_items.get(name)
        if item is None:
            return
        row = self.list_widget.itemWidget(item)
        if isinstance(row, ProjectRow):
            row.refresh_tags()
            self._resize_row_deferred(item, row)

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
        # Destroy on close (outside-click or button-triggered fade) so popups
        # don't accumulate as live children of the window on every right-click.
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        popup.color_chosen.connect(partial(self._set_tag_color, tag))
        popup.reset.connect(partial(self._set_tag_color, tag, None))
        popup.custom_requested.connect(partial(self._open_custom_color, tag))
        # Keep a reference so the popup isn't garbage collected before shown.
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
        # Detail panels. Recoloring happens via a full tag rebuild, which
        # would normally tear down an open inline editor (the color picker is
        # explicitly usable WHILE editing) — so preserve edit mode and the
        # in-progress text/caret when an editor is open.
        if self.current_project:
            self._rebuild_tags_preserving_editor("_tag_editor", self._rebuild_tags)
        if self.current_playlist:
            self._rebuild_tags_preserving_editor(
                "_playlist_tag_editor", self._rebuild_playlist_tags
            )

    def _rebuild_tags_preserving_editor(self, editor_attr: str, rebuild) -> None:
        """Re-run a tag rebuild, restoring an open inline editor's text/caret.

        ``rebuild(editing=True)`` creates a fresh editor and stores it back on
        ``self.<editor_attr>``, so we capture the old text/caret, rebuild, then
        push them onto the new editor.
        """
        editor = getattr(self, editor_attr, None)
        if editor is None:
            rebuild()
            return
        try:
            text, cursor = editor.text(), editor.cursorPosition()
        except RuntimeError:
            rebuild()
            return
        rebuild(editing=True)
        new_editor = getattr(self, editor_attr, None)
        if new_editor is not None:
            try:
                new_editor.setText(text)
                new_editor.setCursorPosition(min(cursor, len(text)))
            except RuntimeError:
                pass

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

    def _make_playlist_row(self, pl: Playlist) -> PlaylistRow:
        row = PlaylistRow(pl, self.store)
        row.tag_right_clicked.connect(self._show_color_picker)
        return row

    def _append_playlist_row(self, pl: Playlist) -> QListWidgetItem:
        row = self._make_playlist_row(pl)
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

    def _reset_playlist_add_ui(self) -> None:
        """Synchronously tear down the add-URL input and restore the button.

        Used on folder change: the in-flight add fetch's callback is dropped
        (``_pending_fetches.clear()``), so nothing else would un-stick the
        'Fetching…' button or remove the stale URL field.
        """
        if self._playlist_url_input is not None:
            edit = self._playlist_url_input
            self._playlist_url_input = None
            edit.deleteLater()
        self.playlist_add_btn.setEnabled(True)
        self.playlist_add_btn.setVisible(True)
        self.playlist_add_btn.setText("+ Add YouTube playlist")
        self._set_playlist_error("")

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
        if url in self._pending_fetches:
            self._set_playlist_error("That playlist is already being added.")
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
        # Rebuild so the new row lands in its sorted position (a bare append
        # would drop it at the bottom regardless of sort order), then select.
        self._rebuild_playlists_list()
        self._update_playlists_empty_hint()
        item = self._playlist_items.get(pl.id)
        if item is not None:
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
            cross_fade_stack(self.playlist_detail_stack, 1, duration=160)
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
        # Derive the Refresh button state from whether THIS playlist has a
        # fetch in flight, so the shared button reflects the shown playlist.
        self._sync_refresh_button()
        # Load playlist-level notes — guard textChanged so loading doesn't
        # trip the debounced save back into the same playlist.
        self._loading_playlist_details = True
        try:
            self.playlist_notes_edit.setPlainText(pl.notes)
        finally:
            self._loading_playlist_details = False

    def _sync_refresh_button(self) -> None:
        """Refresh button reflects whether the current playlist is refreshing."""
        pl = self.current_playlist
        refreshing = pl is not None and pl.id in self._refreshing_playlist_ids
        self.pl_refresh_btn.setEnabled(not refreshing)
        self.pl_refresh_btn.setText("Refreshing…" if refreshing else "Refresh")

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
            self._resize_row_deferred(item, row)

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
            # The tag filter only affects the projects list, so clear it only
            # when no PROJECT still uses the tag (mirrors _remove_tag) — a
            # remaining playlist use is irrelevant to the project filter.
            project_tags = {
                t for p in self.store.projects.values() for t in p.tags
            }
            if self.tag_filter == tag and tag not in project_tags:
                self._set_tag_filter(None)

    def _update_playlist_row_tags(self, pid: str) -> None:
        item = self._playlist_items.get(pid)
        if not item:
            return
        row = self.playlists_list_widget.itemWidget(item)
        if isinstance(row, PlaylistRow):
            row.refresh_tags()
            self._resize_row_deferred(item, row)

    def _refresh_current_playlist(self) -> None:
        if not self.current_playlist:
            return
        pid = self.current_playlist.id
        self._refreshing_playlist_ids.add(pid)
        self._sync_refresh_button()
        # Bind the refreshed id so the in-flight marker is cleared by the SAME
        # id it was added with (resolving by URL alone would leak the marker if
        # two playlists ever shared a URL).
        self._kick_fetch(
            self.current_playlist.url,
            on_done=partial(self._on_refresh_done, refreshed_id=pid),
        )

    def _on_refresh_done(
        self, url: str, data: dict | None, err: str | None,
        *, refreshed_id: str | None = None,
    ) -> None:
        if not self.store:
            return
        if refreshed_id is not None:
            self._refreshing_playlist_ids.discard(refreshed_id)
        # Reflect the current selection's refresh state on the shared button.
        self._sync_refresh_button()
        # Resolve the playlist that was ACTUALLY refreshed by its URL. The user
        # may have selected a different playlist while the fetch was in flight;
        # merging into self.current_playlist would corrupt the wrong one.
        target = next((pl for pl in self.store.playlists if pl.url == url), None)
        is_current = target is not None and self.current_playlist is target
        if err or not data:
            # Only surface the error if the refreshed playlist is still shown.
            if is_current:
                QMessageBox.warning(
                    self, "Projectum", err or "Failed to refresh playlist."
                )
            return
        if target is None:
            # Playlist was removed while the fetch was in flight — discard.
            return
        # Flush pending notes BEFORE merge_fetch so a subsequent
        # _load_playlist_detail doesn't overwrite the editor with stale text,
        # and so the flush lands on the right instance. Only meaningful when
        # the refreshed playlist is the one currently being edited.
        if is_current:
            if self._playlist_notes_save_timer.isActive():
                self._playlist_notes_save_timer.stop()
                self._save_playlist_notes()
            if self._video_notes_save_timer.isActive():
                self._video_notes_save_timer.stop()
                self._save_video_notes()
        target.merge_fetch(data)
        self.store.save()
        if is_current:
            self._load_playlist_detail(target)
        self._refresh_playlist_row_meta(target.id)

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
        self._refreshing_playlist_ids.discard(pid)
        self.store.remove_playlist(pid)
        self._prune_links_for("playlist", pid)
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
        cross_fade_stack(self.playlist_detail_stack, 0, duration=160)
        self._update_playlists_empty_hint()

    def _open_current_playlist_url(self) -> None:
        if not self.current_playlist:
            return
        url = (self.current_playlist.url or "").strip()
        if not url:
            QMessageBox.information(
                self, "Projectum", "This playlist has no saved URL to open."
            )
            return
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(
                self, "Projectum", f"Couldn't open the playlist URL:\n{url}"
            )

    @staticmethod
    def _format_fetched(iso: str) -> str:
        try:
            dt = datetime.fromisoformat(iso)
        except (ValueError, TypeError):
            return str(iso)
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
        # Destroy on close so an activated/closed palette doesn't linger as a
        # live child of the window (one leak per open otherwise).
        palette.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        palette.query_changed.connect(self._palette_search)
        palette.activated.connect(self._palette_activate)
        palette.closed.connect(self._on_palette_closed)
        # destroyed fires on EVERY close (incl. Alt+F4 / WM close, which don't
        # emit `closed`); identity-guarded so it can't null a newer palette.
        palette.destroyed.connect(
            lambda *_a, p=palette: self._forget_if_current("_command_palette", p)
        )
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

    def _forget_if_current(self, attr: str, obj) -> None:
        """Null ``self.<attr>`` when ``obj`` is destroyed, but only if it's
        still the tracked one — a stale ``destroyed`` from a previous instance
        must not clobber a newer one.
        """
        if getattr(self, attr, None) is obj:
            setattr(self, attr, None)

    def _on_palette_closed(self, *_args) -> None:
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

        # Todos — only when there's a query.
        if q:
            for todo in self.store.sorted_todos():
                text = todo.text.strip()
                if text and q in text.casefold():
                    results.append({
                        "type": "todo",
                        "label": text,
                        "sublabel": "done" if todo.done else "task",
                        "_score": score(text),
                        "_key": todo.id,
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
        notes_match = (
            re.search(re.escape(query.strip()), self.store.notes, re.IGNORECASE)
            if q and self.store.notes else None
        )
        if notes_match is not None:
            # Locate the match in the ORIGINAL text (case-insensitive search on
            # the original, not index-mapping from a casefolded copy, since
            # casefold can change length and skew the offsets).
            idx = notes_match.start()
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
            self._command_palette = None
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
        elif kind == "todo":
            self._goto_tab("todos")
            it = self._todo_items.get(key)
            if it is not None:
                self.todo_list_widget.scrollToItem(it)
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
        settings = load_state().get("settings")
        check_updates = (
            settings.get("check_updates", True) if isinstance(settings, dict) else True
        )
        dialog = SettingsDialog(
            theme.current_theme_name(),
            theme.current_font_family(),
            theme.current_font_size(),
            current_check_updates=check_updates,
            parent=self,
        )
        # Destroy on close so dialogs don't pile up as live children.
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.settings_changed.connect(self._apply_settings)
        dialog.closed.connect(self._on_settings_closed)
        # destroyed fires on EVERY close (incl. Alt+F4 / WM close); without it
        # the WA_DeleteOnClose deletion leaves _settings_dialog dangling and the
        # next open crashes on .isVisible(). Identity-guarded against clobber.
        dialog.destroyed.connect(
            lambda *_a, d=dialog: self._forget_if_current("_settings_dialog", d)
        )
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

    def _on_settings_closed(self, *_args) -> None:
        self._settings_dialog = None

    # ─── Update check ────────────────────────────────────────────

    def _maybe_check_updates(self) -> None:
        settings = load_state().get("settings")
        if isinstance(settings, dict) and not settings.get("check_updates", True):
            return  # opted out
        runnable = UpdateCheckRunnable(APP_VERSION)
        runnable.signals.update_available.connect(self._on_update_available)
        self._size_pool.start(runnable)

    def _on_update_available(self, version: str, url: str) -> None:
        # Respect a per-version dismissal so we don't nag about the same one.
        if load_state().get("update_dismissed") == version:
            return
        self._update_version = version
        self._update_url = url
        self._update_banner.show_update(version)

    def _open_update_url(self) -> None:
        url = self._update_url or "https://github.com/wleeaf/projectum/releases/latest"
        QDesktopServices.openUrl(QUrl(url))

    def _dismiss_update(self) -> None:
        self._update_banner.setVisible(False)
        if self._update_version:
            st = load_state()
            st["update_dismissed"] = self._update_version
            save_state(st)

    # ─── Calendar ────────────────────────────────────────────────

    def _rescan_calendar(self) -> None:
        """Refresh the global calendar. The open folder is read on the UI
        thread (in :meth:`_on_calendar_scanned`); every other tracked folder is
        scanned off-thread so a large history never stalls the UI."""
        self.calendar_view.set_today(date.today())
        folders = load_state().get("recent_folders")
        if not isinstance(folders, list):
            folders = []
        exclude = cal.resolved_path(str(self.store.root)) if self.store else None
        # Generation guard: rapid rescans (drop, then tab re-enter) can finish
        # out of order; only the latest result is allowed to update the view.
        self._calendar_scan_gen += 1
        gen = self._calendar_scan_gen
        runnable = CalendarScanRunnable(folders, exclude)
        self._calendar_runnables.add(runnable)
        runnable.signals.done.connect(
            lambda items, skipped, g=gen, r=runnable:
            self._on_calendar_scanned(items, skipped, g, r)
        )
        self._size_pool.start(runnable)

    def _on_calendar_scanned(self, disk_items: list, skipped: list, gen: int,
                             runnable=None) -> None:
        self._calendar_runnables.discard(runnable)
        if gen != self._calendar_scan_gen:
            return  # a newer scan superseded this one
        # Prepend the open folder's items, read fresh on the UI thread (its
        # in-memory objects are never touched off-thread). scan_disk excluded
        # the open folder, so there's no double-count.
        live_items = cal.items_from_store(self.store) if self.store else []
        self._calendar_items = live_items + list(disk_items)
        self._refresh_calendar_view()

    def _refresh_calendar_view(self) -> None:
        # Grid is global; the tray is scoped to the open folder's undated items.
        tray_home = cal.resolved_path(str(self.store.root)) if self.store else None
        self.calendar_view.set_items(self._calendar_items, tray_home)

    def _open_schedule_dialog(self, item) -> None:
        """Open the date-range picker for a scheduled bar or unscheduled chip."""
        if self._schedule_dialog is not None and self._schedule_dialog.isVisible():
            self._schedule_dialog.raise_()
            return
        dlg = ScheduleDialog(item.title or "(untitled)", item.start, item.end, parent=self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dlg.scheduled.connect(lambda s, e, it=item: self._apply_schedule(it, s, e))
        dlg.destroyed.connect(
            lambda *_a, d=dlg: self._forget_if_current("_schedule_dialog", d)
        )
        self._schedule_dialog = dlg
        dlg.adjustSize()
        center = self.frameGeometry().center()
        dlg.move(center.x() - dlg.width() // 2, center.y() - dlg.height() // 2)
        dlg.setWindowOpacity(0.0)
        dlg.show()
        fade_window(dlg, 1.0, duration=140)

    def _on_calendar_item_context(self, item, global_pos) -> None:
        menu = QMenu(self)
        edit = QAction("Edit dates…", menu)
        edit.triggered.connect(lambda: self._open_schedule_dialog(item))
        menu.addAction(edit)
        unschedule = QAction("Unschedule", menu)
        unschedule.triggered.connect(lambda: self._apply_schedule(item, "", ""))
        menu.addAction(unschedule)
        menu.exec(global_pos)

    def _apply_schedule(self, item, start: str, end: str) -> None:
        """Persist a date change for ``item`` and refresh the calendar.

        ``apply_dates`` mutates the passed ScheduledItem in place and routes the
        write to the right store, so re-feeding the current list gives instant
        feedback (the item hops between grid and tray immediately); the async
        rescan then reconciles persisted truth across all folders."""
        if cal.apply_dates(self.store, item, start, end):
            self._refresh_calendar_view()
            self._rescan_calendar()

    # ─── Relations (links) ───────────────────────────────────────

    def _build_entity_index(self) -> dict:
        """Resolve every linkable entity across tracked folders to {ref: info}.
        Read-only cross-folder scan; rebuilt when a Links dialog opens (a
        discrete action) — not per keystroke (the dialog filters in memory)."""
        folders = load_state().get("recent_folders")
        if not isinstance(folders, list):
            folders = []
        items, _skipped = cal.collect_items(self.store, folders)
        triples = [(it.home, it.kind, it.key, it.title) for it in items]
        self._entity_index = links_mod.index_entities(triples)
        return self._entity_index

    def _open_links_dialog(self, ref, title: str) -> None:
        if self._links_dialog is not None and self._links_dialog.isVisible():
            self._links_dialog.raise_()
            self._links_dialog.activateWindow()
            return
        index = self._build_entity_index()
        dlg = LinksDialog(ref, title, self._link_store, index, parent=self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dlg.changed.connect(self._on_links_changed)
        dlg.destroyed.connect(
            lambda *_a, d=dlg: self._forget_if_current("_links_dialog", d)
        )
        self._links_dialog = dlg
        dlg.adjustSize()
        center = self.frameGeometry().center()
        dlg.move(center.x() - dlg.width() // 2, center.y() - dlg.height() // 2)
        dlg.setWindowOpacity(0.0)
        dlg.show()
        fade_window(dlg, 1.0, duration=140)

    def _on_links_changed(self) -> None:
        # Date-link awareness in the calendar arrives in a later pass; nothing
        # else to refresh for now.
        pass

    def _prune_links_for(self, kind: str, key: str) -> None:
        """Drop a deleted entity's edges from the graph (explicit deletion)."""
        if self.store is not None:
            self._link_store.remove_entity(make_ref(kind, str(self.store.root), key))

    def _show_todo_context_menu(self, pos) -> None:
        if not self.store:
            return
        item = self.todo_list_widget.itemAt(pos)
        if item is None:
            return
        tid = item.data(Qt.ItemDataRole.UserRole)
        todo = self.store.get_todo(tid)
        if todo is None:
            return
        menu = QMenu(self)
        relate = QAction("Links…", menu)
        relate.triggered.connect(partial(
            self._open_links_dialog,
            make_ref("todo", str(self.store.root), tid), todo.text,
        ))
        menu.addAction(relate)
        menu.exec(self.todo_list_widget.viewport().mapToGlobal(pos))

    def _apply_settings(self, settings: dict) -> None:
        new_theme = settings.get("theme", theme.DEFAULT_THEME)
        new_family = settings.get("font_family", theme.DEFAULT_FONT_FAMILY)
        try:
            raw_size = int(settings.get("font_size", theme.DEFAULT_FONT_SIZE))
        except (TypeError, ValueError):
            raw_size = theme.DEFAULT_FONT_SIZE
        new_size = max(theme.FONT_SIZE_MIN, min(theme.FONT_SIZE_MAX, raw_size))
        check_updates = bool(settings.get("check_updates", True))
        theme_changed = new_theme != theme.current_theme_name()

        # A theme swap restyles the whole window at once; crossfade the old
        # appearance into the new one so it doesn't jarringly snap. Font-only
        # changes apply instantly (no fade).
        if theme_changed:
            cross_fade_swap(
                self._frame_wrapper,
                lambda: self._apply_settings_now(
                    new_theme, new_family, new_size, check_updates
                ),
                duration=260,
            )
        else:
            self._apply_settings_now(new_theme, new_family, new_size, check_updates)

    def _apply_settings_now(
        self, new_theme: str, new_family: str, new_size: int, check_updates: bool = True
    ) -> None:
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

        # Refresh every markdown highlighter so heading sizes and colors
        # follow the new theme / font.
        for attr in (
            "notes_highlighter", "playlist_notes_highlighter",
            "video_notes_highlighter", "global_notes_highlighter",
        ):
            hl = getattr(self, attr, None)
            if hl is not None:
                hl.refresh()

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
            self._rebuild_todo_list()  # rows bake theme colors inline
            # _full_rebuild_list reselects the visible row, which fires
            # _on_select -> _load_detail; only reload here if the row is
            # filtered out (so reselection didn't happen), to avoid a
            # redundant detail load + SizeRunnable.
            if self.current_project:
                item = self._row_items.get(self.current_project.name)
                if item is None or item.isHidden():
                    self._load_detail(self.current_project)
            # Reselecting the playlist row fires _on_playlist_select ->
            # _load_playlist_detail, so no explicit reload needed here.
            if cur_pl_id:
                pl_item = self._playlist_items.get(cur_pl_id)
                if pl_item is not None:
                    self.playlists_list_widget.setCurrentItem(pl_item)

        # Persist alongside other window state.
        cur = load_state()
        cur["settings"] = {
            "theme": new_theme,
            "font_family": new_family,
            "font_size": new_size,
            "check_updates": check_updates,
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
    # paint already uses the right theme + font. Guard against a corrupt or
    # hand-edited state.json (wrong types) so the app can still launch.
    state = load_state()
    settings = state.get("settings")
    if not isinstance(settings, dict):
        settings = {}
    family = settings.get("font_family")
    initial_family = family if isinstance(family, str) and family else theme.DEFAULT_FONT_FAMILY
    try:
        initial_size = int(settings.get("font_size", theme.DEFAULT_FONT_SIZE))
    except (TypeError, ValueError):
        initial_size = theme.DEFAULT_FONT_SIZE
    initial_size = max(theme.FONT_SIZE_MIN, min(theme.FONT_SIZE_MAX, initial_size))
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
