"""Custom-painted widgets for Projectum."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import (
    Qt, QRect, QRectF, QPointF, QPoint, QMimeData, Property, Signal, QObject,
    QEvent, QRunnable, QSize,
)
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QPen, QFont, QFontMetrics, QPainterPath,
    QDrag, QMouseEvent, QSyntaxHighlighter, QTextCharFormat,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel,
    QLayout, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
)

from .anims import fade_window_close
from . import calendar as cal
from . import theme
from .theme import TAG_PALETTE, tag_color


# Drag MIME marker for an unscheduled tray chip being dropped on the grid.
SCHEDULE_MIME = "application/x-projectum-schedule"

# Calendar bar color per item kind (resolved against the active theme at paint).
KIND_COLOR_KEY = {
    cal.KIND_PROJECT: "ACCENT",
    cal.KIND_PLAYLIST: "ACCENT_2",
    cal.KIND_TODO: "INFO",
}


# Folders we skip when walking a project tree to compute size.
SIZE_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", ".tox", "target",
    "dist", "build", "__pycache__", ".next", ".nuxt", ".cache",
    ".gradle", ".idea", ".vscode-test", "Pods", ".pnpm-store",
    "bower_components", ".turbo", ".parcel-cache",
}


class TagChip(QWidget):
    """Small colored pill showing a tag name.

    ``removable=True`` adds an inline × button on the right. Clicks on the ×
    emit :attr:`remove_clicked`; clicks on the chip body emit :attr:`clicked`
    (which the caller can ignore — attached-tag chips in the detail panel
    use removable mode and intentionally have no body-click action so a
    misclick can't drop a tag).
    """

    clicked = Signal()
    right_clicked = Signal()
    remove_clicked = Signal()

    PAD_X = 12
    PAD_Y = 4
    REMOVE_BOX = 16  # square hit-area for the × on removable chips

    def __init__(
        self,
        tag: str,
        color: str,
        parent: QWidget | None = None,
        *,
        removable: bool = False,
    ):
        super().__init__(parent)
        self.tag = tag
        self._color = color
        # The raw (often pastel) tag color is the chip FILL, but using it for
        # text/border too is unreadable on light themes. Derive a legible ink
        # once here (theme changes rebuild the rows, refreshing it) — never in
        # paintEvent, which runs constantly while scrolling.
        self._ink = theme.legible_ink(color, theme.SURFACE)
        self._removable = removable
        self._remove_hover = False
        if removable:
            self.setMouseTracking(True)
            self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        font = QFont()
        font.setPointSize(9)
        font.setWeight(QFont.Weight.DemiBold)
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self.tag) + self.PAD_X * 2
        if self._removable:
            w += self.REMOVE_BOX  # room for the × on the right
        h = max(22, fm.height() + self.PAD_Y * 2)
        return QSize(w, h)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _remove_rect(self) -> QRectF:
        """Hit area for the × — right-aligned square inside the chip."""
        side = self.REMOVE_BOX
        return QRectF(
            self.width() - side - 2,
            (self.height() - side) / 2,
            side,
            side,
        )

    def _point_in_remove(self, pos) -> bool:
        return self._removable and self._remove_rect().contains(QPointF(pos))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._point_in_remove(event.position()):
                self.remove_clicked.emit()
            else:
                self.clicked.emit()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._removable:
            in_remove = self._point_in_remove(event.position())
            if in_remove != self._remove_hover:
                self._remove_hover = in_remove
                self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._removable and self._remove_hover:
            self._remove_hover = False
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)

            base = QColor(self._color)
            ink = QColor(self._ink)
            fill = QColor(base)
            fill.setAlpha(46)
            border = QColor(ink)
            border.setAlpha(150)

            rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
            p.setBrush(QBrush(fill))
            p.setPen(QPen(border, 1))
            radius = rect.height() / 2
            p.drawRoundedRect(rect, radius, radius)

            p.setPen(ink)
            p.setFont(self.font())
            text_rect = QRectF(rect)
            if self._removable:
                # Reserve the right portion of the chip for the × so the
                # text stays centered in the left part.
                text_rect.setRight(text_rect.right() - self.REMOVE_BOX)
            # Elide to the available width. When the chip has room (the
            # common case) this is a no-op and the full tag shows; when a
            # QHBoxLayout shrinks the chip below its hint (narrow sidebar,
            # several tags) the text becomes e.g. "infrastr…" instead of a
            # mid-glyph clip on both sides.
            fm = self.fontMetrics()
            avail = max(0, int(text_rect.width()) - 2)
            label = fm.elidedText(self.tag, Qt.TextElideMode.ElideRight, avail)
            p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)

            if self._removable:
                self._paint_remove_glyph(p, ink)

    def _paint_remove_glyph(self, p: QPainter, base: QColor) -> None:
        box = self._remove_rect()
        cx, cy = box.center().x(), box.center().y()
        arm = 3.5  # half-length of each × stroke
        # Dim by default, full chip color on hover.
        color = QColor(base)
        color.setAlpha(220 if self._remove_hover else 140)
        pen = QPen(color, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(cx - arm, cy - arm), QPointF(cx + arm, cy + arm))
        p.drawLine(QPointF(cx - arm, cy + arm), QPointF(cx + arm, cy - arm))


class CompletionToggle(QWidget):
    """Custom-painted check toggle.

    ``color_key`` names the ``theme`` attribute used as the accent color
    (default ``"SUCCESS"`` — green — for completion). Pass ``"INFO"`` for
    the blue "tested" variant, or any other theme color name.
    """

    toggled = Signal(bool)

    def __init__(
        self,
        checked: bool = False,
        parent: QWidget | None = None,
        color_key: str = "SUCCESS",
    ):
        super().__init__(parent)
        self._checked = checked
        self._hover = False
        self._pulse = 0.0
        self._color_key = color_key
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def _accent(self) -> str:
        return getattr(theme, self._color_key, theme.SUCCESS)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, value: bool) -> None:
        if value == self._checked:
            return
        self._checked = bool(value)
        self._start_pulse()
        self.update()
        self.toggled.emit(self._checked)

    def _start_pulse(self) -> None:
        from PySide6.QtCore import QEasingCurve, QPropertyAnimation
        # Stop any in-flight pulse first — otherwise the old animation (kept
        # alive by its QObject parent) keeps driving the `pulse` property and
        # the two fight, causing jank on rapid toggles.
        prev = getattr(self, "_anim", None)
        if isinstance(prev, QPropertyAnimation):
            prev.stop()
        anim = QPropertyAnimation(self, b"pulse", self)
        anim.setDuration(280)
        anim.setStartValue(0.0)
        anim.setKeyValueAt(0.45, 1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim = anim
        anim.start()

    # Q_PROPERTY for the pulse animation
    def _get_pulse(self) -> float:
        return self._pulse

    def _set_pulse(self, value: float) -> None:
        self._pulse = float(value)
        self.update()

    pulse = Property(float, _get_pulse, _set_pulse)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            event.accept()
        else:
            super().mousePressEvent(event)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = QRectF(2, 2, self.width() - 4, self.height() - 4)

            # Scale up during the pulse animation (1.0 → 1.30 → 1.0)
            scale = 1.0 + 0.30 * self._pulse
            if scale != 1.0:
                cx = self.width() / 2
                cy = self.height() / 2
                p.translate(cx, cy)
                p.scale(scale, scale)
                p.translate(-cx, -cy)

            accent = QColor(self._accent())
            if self._checked:
                p.setBrush(accent)
                p.setPen(QPen(accent, 1.4))
                p.drawRoundedRect(r, 6, 6)
                pen = QPen(QColor(theme.BG), 2.2)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                path = QPainterPath()
                path.moveTo(r.left() + r.width() * 0.24, r.top() + r.height() * 0.54)
                path.lineTo(r.left() + r.width() * 0.44, r.top() + r.height() * 0.72)
                path.lineTo(r.left() + r.width() * 0.78, r.top() + r.height() * 0.32)
                p.drawPath(path)
            else:
                fill = QColor(theme.SURFACE_2)
                border = QColor(theme.BORDER) if not self._hover else accent
                if self._hover:
                    fill = QColor(accent)
                    fill.setAlpha(22)
                p.setBrush(fill)
                p.setPen(QPen(border, 1.4))
                p.drawRoundedRect(r, 6, 6)


class ProjectRow(QWidget):
    """A single row in the sidebar list."""

    completion_changed = Signal(bool)
    tag_right_clicked = Signal(str)

    def __init__(self, project, store, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.store = store
        self._build()

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        self.toggle = CompletionToggle(self.project.completed)
        self.toggle.toggled.connect(self.completion_changed.emit)
        self.toggle.toggled.connect(self._restyle_name)
        layout.addWidget(self.toggle, alignment=Qt.AlignmentFlag.AlignTop)

        # Stored as a member so _populate_tags can invalidate it directly —
        # the col layout caches its sizeHint independently of the row's
        # outer layout, so without this the row keeps its with-tags
        # height after the last tag is removed.
        self._col_layout = QVBoxLayout()
        col = self._col_layout
        col.setSpacing(4)
        col.setContentsMargins(0, 0, 0, 0)

        self.name_label = QLabel(self.project.name)
        nf = QFont()
        nf.setPointSize(11)
        nf.setWeight(QFont.Weight.Medium)
        self.name_label.setFont(nf)
        col.addWidget(self.name_label)

        # Always-present tag row; visibility toggled by populate_tags()
        self._tag_wrap = QWidget()
        self._tag_layout = QHBoxLayout(self._tag_wrap)
        self._tag_layout.setSpacing(4)
        self._tag_layout.setContentsMargins(0, 0, 0, 0)
        col.addWidget(self._tag_wrap)
        self._populate_tags()

        layout.addLayout(col, 1)

        # Pin indicator + notes indicator.
        self.pin_dot = QLabel("◆")
        self.pin_dot.setStyleSheet(
            f"color: {theme.ACCENT}; font-size: 10px; background: transparent;"
        )
        self.pin_dot.setToolTip("Pinned")
        self.pin_dot.setVisible(self.project.pinned)
        layout.addWidget(self.pin_dot, alignment=Qt.AlignmentFlag.AlignTop)

        self.notes_dot = QLabel("●")
        self.notes_dot.setStyleSheet(
            f"color: {theme.ACCENT_2}; font-size: 11px; background: transparent;"
        )
        self.notes_dot.setToolTip("Has notes")
        self.notes_dot.setVisible(bool(self.project.notes.strip()))
        layout.addWidget(self.notes_dot, alignment=Qt.AlignmentFlag.AlignTop)

        self._restyle_name(self.project.completed)

    def set_pinned(self, pinned: bool) -> None:
        self.project.pinned = pinned
        self.pin_dot.setVisible(pinned)

    def _populate_tags(self) -> None:
        # Detach old children synchronously (setParent(None)) before
        # deleteLater — deleteLater queues destruction, so without the
        # reparent the old chips remain children of _tag_wrap briefly and
        # can paint over (or alongside) the new chips while the layout
        # reflows. That race is the "extra space but no tags" symptom.
        while self._tag_layout.count():
            item = self._tag_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if not self.project.tags:
            self._tag_wrap.setVisible(False)
        else:
            # Cap at 2 chips: QHBoxLayout shrinks Fixed-policy children when
            # the column can't accommodate them all, which clips the chip
            # text. Two short-to-medium tag names reliably fit a ~340px
            # sidebar; remainder is summarized as "+N".
            visible_count = 2
            for t in self.project.tags[:visible_count]:
                chip = TagChip(t, tag_color(t, self.store.tag_colors))
                chip.setToolTip(f"#{t} · right-click to change color")
                chip.right_clicked.connect(
                    lambda tag=t: self.tag_right_clicked.emit(tag)
                )
                self._tag_layout.addWidget(chip)
            if len(self.project.tags) > visible_count:
                more = QLabel(f"+{len(self.project.tags) - visible_count}")
                more.setStyleSheet(
                    f"color: {theme.TEXT_MUTED}; font-size: 10px; font-weight: 600;"
                )
                self._tag_layout.addWidget(more)
            self._tag_layout.addStretch()
            self._tag_wrap.setVisible(True)
            self._tag_layout.activate()
            self._tag_wrap.updateGeometry()
        # Invalidate every layout in the row tree — col is what holds the
        # tag_wrap, so its cached sizeHint is the one that goes stale
        # when the wrap toggles visible/hidden. The outer row layout
        # then re-queries col for a fresh height.
        self._col_layout.invalidate()
        if self.layout() is not None:
            self.layout().invalidate()
        self.updateGeometry()

    def _restyle_name(self, completed: bool) -> None:
        font = self.name_label.font()
        font.setStrikeOut(completed)
        self.name_label.setFont(font)
        # Tested takes precedence — blue wins over muted, and still composes
        # with the strikethrough applied above for completed-and-tested.
        if self.project.tested:
            color = theme.INFO
        elif completed:
            color = theme.TEXT_MUTED
        else:
            color = theme.TEXT
        self.name_label.setStyleSheet(f"color: {color}; background: transparent;")

    # ── In-place state updates (used by app.py to avoid widget rebuilds) ──

    def set_completed(self, checked: bool) -> None:
        self.project.completed = checked
        self.toggle.blockSignals(True)
        self.toggle.setChecked(checked)
        self.toggle.blockSignals(False)
        self._restyle_name(checked)

    def set_has_notes(self, has: bool) -> None:
        self.notes_dot.setVisible(has)

    def set_tested(self, tested: bool) -> None:
        self.project.tested = tested
        self._restyle_name(self.project.completed)

    def refresh_tags(self) -> None:
        self._populate_tags()


class TagEditor(QLineEdit):
    """Inline tag input that appears in place of the '+ add tag' button."""

    submitted = Signal(str)
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setPlaceholderText("new tag")
        self.setFixedHeight(22)
        self.setMinimumWidth(110)
        self.setMaximumWidth(170)
        self._done = False
        self.setStyleSheet(
            f"QLineEdit {{"
            f"background-color: {theme.SURFACE_2};"
            f"border: 1px solid {theme.ACCENT};"
            f"border-radius: 11px;"
            f"padding: 3px 12px;"
            f"color: {theme.TEXT};"
            f"font-size: 11px;"
            f"font-weight: 600;"
            f"selection-background-color: {theme.ACCENT};"
            f"selection-color: {theme.BG};"
            f"}}"
        )

    def _popup_visible(self) -> bool:
        c = self.completer()
        return c is not None and c.popup() is not None and c.popup().isVisible()

    def keyPressEvent(self, event):
        popup_open = self._popup_visible()
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if popup_open:
                # Let the popup accept the highlighted completion first.
                super().keyPressEvent(event)
            self._commit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            if popup_open:
                # First Esc: close the popup, leave the input alive.
                super().keyPressEvent(event)
                event.accept()
                return
            self._cancel()
            event.accept()
            return
        if popup_open and event.key() in (
            Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Tab,
        ):
            super().keyPressEvent(event)
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        # If focus left because a popup (color picker, completer, context
        # menu) stole it, don't commit/cancel — the user is still editing,
        # they just opened an auxiliary UI.
        if event.reason() == Qt.FocusReason.PopupFocusReason:
            super().focusOutEvent(event)
            return
        super().focusOutEvent(event)
        if self._done:
            return
        if self._popup_visible():
            return
        if self.text().strip():
            self._commit()
        else:
            self._cancel()

    def _commit(self) -> None:
        if self._done:
            return
        self._done = True
        text = self.text().strip().lower().lstrip("#")
        if text:
            self.submitted.emit(text)
        else:
            self.cancelled.emit()

    def _cancel(self) -> None:
        if self._done:
            return
        self._done = True
        self.cancelled.emit()


# ──────────────────────── Async size walker ────────────────────────


class _SizeSignals(QObject):
    done = Signal(str, int)  # project name, bytes


class SizeRunnable(QRunnable):
    """Walks a project folder off-thread to compute total size on disk."""

    def __init__(self, project_name: str, root: Path):
        super().__init__()
        self.project_name = project_name
        self.root = Path(root)
        self.signals = _SizeSignals()

    def run(self) -> None:
        total = 0
        try:
            for dirpath, dirnames, filenames in os.walk(self.root):
                dirnames[:] = [
                    d for d in dirnames
                    if d not in SIZE_SKIP_DIRS and not d.startswith(".")
                ]
                for name in filenames:
                    fp = os.path.join(dirpath, name)
                    try:
                        total += os.path.getsize(fp)
                    except OSError:
                        continue
        except OSError:
            pass
        self.signals.done.emit(self.project_name, total)


# ──────────────────────── Async git probe ────────────────────────


class _GitSignals(QObject):
    done = Signal(str, object)  # project name, {"branch", "dirty"} or None


class GitRunnable(QRunnable):
    """Reads a project's git branch + dirty state off-thread in one call.

    ``git status -sb --porcelain`` prints a ``## <branch>...`` header line
    followed by one line per change, so branch + dirtiness come from a single
    invocation. Emits ``None`` when the folder isn't a git work tree (or git
    isn't installed).
    """

    def __init__(self, project_name: str, root):
        super().__init__()
        self.project_name = project_name
        self.root = str(root)
        self.signals = _GitSignals()

    def run(self) -> None:
        import subprocess

        info = None
        try:
            out = subprocess.run(
                ["git", "-C", self.root, "status", "-sb", "--porcelain"],
                capture_output=True, text=True, timeout=4,
            )
            if out.returncode == 0:
                lines = out.stdout.splitlines()
                if lines and lines[0].startswith("##"):
                    seg = lines[0][2:].strip()
                    branch = seg.split("...")[0].split(" ")[0] or "(detached)"
                    dirty = len(lines) > 1
                else:
                    branch, dirty = "(detached)", bool(lines)
                info = {"branch": branch, "dirty": dirty}
        except (OSError, ValueError, subprocess.SubprocessError):
            info = None
        self.signals.done.emit(self.project_name, info)


# ──────────────── Flow layout ────────────────


class FlowLayout(QLayout):
    """Lays out child widgets left-to-right, wrapping to the next line as needed."""

    def __init__(self, parent=None, margin: int = 0, spacing: int = 6):
        super().__init__(parent)
        if parent is None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items: list = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, *, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        spacing = self.spacing()
        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            next_x = x + w + spacing
            if next_x - spacing > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + spacing
                next_x = x + w + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, h)
        return y + line_height - rect.y() + m.bottom()


# ──────────────── Color picker for tags ────────────────


class _Swatch(QLabel):
    clicked = Signal(str)

    def __init__(self, color: str, selected: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self._color = color
        self._selected = selected
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _ev) -> None:
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
            p.setBrush(QColor(self._color))
            border = QColor(theme.ACCENT) if self._selected else QColor(self._color)
            p.setPen(QPen(border, 2))
            p.drawRoundedRect(rect, 6, 6)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._color)
            event.accept()
        else:
            super().mousePressEvent(event)


class ColorPickerPopup(QWidget):
    """Small popup with palette swatches + 'Reset to default' + 'Custom color…'."""

    color_chosen = Signal(str)
    reset = Signal()
    custom_requested = Signal()

    def __init__(
        self,
        tag: str,
        current_color: str | None,
        parent: QWidget | None = None,
    ):
        super().__init__(
            parent,
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint,
        )
        self._tag = tag
        self._current = current_color
        self.setObjectName("colorPickerPopup")
        # Styling now lives in the app-level stylesheet (theme.build_stylesheet),
        # so this popup re-styles automatically on theme change.
        # Start invisible — the caller fades us in via anims.fade_window.
        self.setWindowOpacity(0.0)
        self._build()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        header = QLabel(f"Color for #{self._tag}")
        header.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 10px; font-weight: 700; "
            f"background: transparent;"
        )
        v.addWidget(header)

        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setContentsMargins(0, 0, 0, 0)
        for i, color in enumerate(TAG_PALETTE):
            sw = _Swatch(color, color.lower() == (self._current or "").lower())
            sw.clicked.connect(self._on_pick)
            r, c = divmod(i, 5)
            grid.addWidget(sw, r, c)
        v.addLayout(grid)

        custom_btn = QPushButton("Custom color…")
        custom_btn.setObjectName("ghost")
        custom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        custom_btn.clicked.connect(self._on_custom)
        v.addWidget(custom_btn)

        reset_btn = QPushButton("Reset to default")
        reset_btn.setObjectName("ghost")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.clicked.connect(self._on_reset)
        v.addWidget(reset_btn)

    def _on_pick(self, color: str) -> None:
        self.color_chosen.emit(color)
        # Outside-click closes via Qt.Popup's internal handling and can't be
        # animated, but button-triggered closes can fade out for parity with
        # the fade-in on show.
        fade_window_close(self, duration=120)

    def _on_reset(self) -> None:
        self.reset.emit()
        fade_window_close(self, duration=120)

    def _on_custom(self) -> None:
        self.custom_requested.emit()
        fade_window_close(self, duration=120)


# ──────────────── Brand mark + icon buttons ────────────────


class BrandMark(QWidget):
    """A 'W' painted in the current theme accent.

    Subscribe via :func:`update` after a theme change — the paintEvent reads
    ``theme.ACCENT`` fresh each time, so the next repaint picks up new colors.
    """

    def __init__(self, size: int = 24, parent: QWidget | None = None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)

    def sizeHint(self) -> QSize:
        return QSize(self._size, self._size)

    def paintEvent(self, _event) -> None:
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            color = QColor(theme.ACCENT)
            # Thick rounded W constructed from four diagonal strokes.
            pen = QPen(color, max(2.0, self._size * 0.14))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            w = self.width()
            h = self.height()
            pad_x = w * 0.18
            pad_y = h * 0.22
            left = pad_x
            right = w - pad_x
            top = pad_y
            bot = h - pad_y
            third = (right - left) / 3.0
            path = QPainterPath()
            path.moveTo(left, top)
            path.lineTo(left + third * 0.6, bot)
            path.lineTo(left + third * 1.5, top + (bot - top) * 0.35)
            path.lineTo(left + third * 2.4, bot)
            path.lineTo(right, top)
            p.drawPath(path)


class IconButton(QPushButton):
    """Square icon-only button with a hover background, painted glyph inside.

    ``kind`` controls the glyph: currently only ``"gear"``. Other icon kinds
    can be added without changing call sites.
    """

    KINDS = {"gear"}

    def __init__(self, kind: str, size: int = 30, parent: QWidget | None = None):
        if kind not in self.KINDS:
            raise ValueError(kind)
        super().__init__(parent)
        self.kind = kind
        self.setObjectName("iconButton")
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._size = size

    def paintEvent(self, event) -> None:
        # Let QPushButton draw the (transparent / hover) background first.
        super().paintEvent(event)
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            color = QColor(theme.TEXT_DIM)
            if self.underMouse():
                color = QColor(theme.TEXT)
            pen = QPen(color, 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            cx = self.width() / 2
            cy = self.height() / 2
            if self.kind == "gear":
                radius = min(self.width(), self.height()) * 0.20
                # Inner circle
                p.drawEllipse(QPointF(cx, cy), radius, radius)
                # Eight short teeth around the gear
                import math
                outer = radius * 1.7
                inner = radius * 1.2
                for i in range(8):
                    a = math.radians(i * 45)
                    x1 = cx + math.cos(a) * inner
                    y1 = cy + math.sin(a) * inner
                    x2 = cx + math.cos(a) * outer
                    y2 = cy + math.sin(a) * outer
                    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


# ──────────────── Settings dialog ────────────────


class SettingsDialog(QWidget):
    """Frameless popup with theme, font family, and font size selectors.

    Emits :attr:`settings_changed` whenever any control changes value. Caller
    applies the new settings live and persists them.
    """

    settings_changed = Signal(dict)
    closed = Signal()

    # Font sizes offered in the dropdown (clamped to the allowed range; the
    # current value is inserted if it isn't one of these).
    SIZE_PRESETS = [9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24, 28]

    def __init__(
        self,
        current_theme: str,
        current_font_family: str,
        current_font_size: int,
        current_check_updates: bool = True,
        parent: QWidget | None = None,
    ):
        super().__init__(
            parent,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint,
        )
        self.setObjectName("settingsDialog")
        self.setMinimumWidth(440)
        self._build(
            current_theme, current_font_family, current_font_size,
            current_check_updates,
        )
        # Baseline for the dirty check, taken from the actual control values so
        # Qt's normalization (e.g. of a font family) doesn't read as a change.
        self._applied = self._current_selection()
        self.theme_combo.currentIndexChanged.connect(self._refresh_apply_enabled)
        self.font_combo.currentFontChanged.connect(self._refresh_apply_enabled)
        self.size_combo.currentIndexChanged.connect(self._refresh_apply_enabled)
        self.update_check.toggled.connect(self._refresh_apply_enabled)
        self._refresh_apply_enabled()

    @staticmethod
    def _theme_swatch(bg: str, accent: str, border: str):
        """A small bg + accent-dot icon previewing a theme in the dropdown."""
        from PySide6.QtGui import QIcon, QPixmap
        pm = QPixmap(30, 18)
        pm.fill(Qt.GlobalColor.transparent)
        with QPainter(pm) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor(border), 1))
            p.setBrush(QColor(bg))
            p.drawRoundedRect(QRectF(0.5, 0.5, 29, 17), 4, 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(accent))
            p.drawEllipse(QPointF(21, 9), 4.5, 4.5)
        return QIcon(pm)

    def _build(
        self,
        current_theme: str,
        current_font_family: str,
        current_font_size: int,
        current_check_updates: bool,
    ) -> None:
        from PySide6.QtWidgets import QCheckBox, QComboBox, QFontComboBox, QFrame
        from PySide6.QtGui import QFont
        from .theme import (
            DEFAULT_FONT_FAMILY, FONT_SIZE_MAX, FONT_SIZE_MIN, THEMES, THEME_LABELS,
        )

        v = QVBoxLayout(self)
        v.setContentsMargins(28, 22, 28, 22)
        v.setSpacing(16)

        # Title row
        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Settings")
        title.setObjectName("settingsTitle")
        header.addWidget(title, 1)
        close_btn = QPushButton("×")
        close_btn.setObjectName("iconButton")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self._on_close)
        header.addWidget(close_btn)
        v.addLayout(header)

        divider = QFrame()
        divider.setObjectName("settingsDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        v.addWidget(divider)

        # ── Theme — dropdown with a per-theme color swatch ──
        v.addLayout(self._field_row("Theme", "Color palette used across the app."))
        self.theme_combo = QComboBox()
        self.theme_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_combo.setIconSize(QSize(30, 18))
        self._theme_keys: list[str] = []
        for key, label in THEME_LABELS:
            t = THEMES.get(key, {})
            icon = self._theme_swatch(
                t.get("BG", "#000"), t.get("ACCENT", "#888"), t.get("BORDER", "#444")
            )
            self.theme_combo.addItem(icon, label)
            self._theme_keys.append(key)
        if current_theme in self._theme_keys:
            self.theme_combo.setCurrentIndex(self._theme_keys.index(current_theme))
        v.addWidget(self.theme_combo)

        # ── Font family — a select-only dropdown that previews each family ──
        v.addLayout(self._field_row("Font family", "Pick from your installed fonts."))
        self.font_combo = QFontComboBox()
        self.font_combo.setEditable(False)
        self.font_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.font_combo.setMaxVisibleItems(14)
        chosen = current_font_family or DEFAULT_FONT_FAMILY
        self.font_combo.setCurrentFont(QFont(chosen))
        v.addWidget(self.font_combo)

        # ── Font size — a dropdown of preset pixel sizes ──
        v.addLayout(self._field_row(
            "Font size", f"Base text size in pixels ({FONT_SIZE_MIN}–{FONT_SIZE_MAX})."
        ))
        self.size_combo = QComboBox()
        self.size_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        cur = max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, int(current_font_size)))
        sizes = sorted(
            {s for s in self.SIZE_PRESETS if FONT_SIZE_MIN <= s <= FONT_SIZE_MAX} | {cur}
        )
        for s in sizes:
            self.size_combo.addItem(f"{s} px", s)
        self.size_combo.setCurrentIndex(sizes.index(cur))
        v.addWidget(self.size_combo)

        # ── Updates ──
        v.addSpacing(4)
        self.update_check = QCheckBox("Check for updates on launch")
        self.update_check.setObjectName("settingsField")
        self.update_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_check.setChecked(bool(current_check_updates))
        v.addWidget(self.update_check)

        v.addStretch(1)

        hint = QLabel("Choose your settings, then click Apply.")
        hint.setObjectName("settingsHint")
        v.addWidget(hint)

        # ── Footer: Apply (enabled only when something changed) + Close ──
        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addStretch(1)
        self.close_action_btn = QPushButton("Close")
        self.close_action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_action_btn.clicked.connect(self._on_close)
        footer.addWidget(self.close_action_btn)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.clicked.connect(self._on_apply)
        footer.addWidget(self.apply_btn)
        v.addLayout(footer)

    def _field_row(self, title: str, subtitle: str) -> QVBoxLayout:
        wrap = QVBoxLayout()
        wrap.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("settingsField")
        s = QLabel(subtitle)
        s.setObjectName("settingsFieldSub")
        wrap.addWidget(t)
        wrap.addWidget(s)
        return wrap

    def _current_selection(self) -> dict:
        idx = self.theme_combo.currentIndex()
        theme_key = (
            self._theme_keys[idx] if 0 <= idx < len(self._theme_keys) else "dark"
        )
        return {
            "theme": theme_key,
            "font_family": self.font_combo.currentFont().family(),
            "font_size": int(self.size_combo.currentData()),
            "check_updates": self.update_check.isChecked(),
        }

    def _refresh_apply_enabled(self, *_args) -> None:
        self.apply_btn.setEnabled(self._current_selection() != self._applied)

    def _on_apply(self) -> None:
        sel = self._current_selection()
        self._applied = sel
        self._refresh_apply_enabled()  # nothing pending now
        self.settings_changed.emit(dict(sel))

    def _on_close(self) -> None:
        fade_window_close(self, duration=120, on_done=self._finish_close)

    def _finish_close(self) -> None:
        self.closed.emit()
        self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._on_close()
            event.accept()
            return
        super().keyPressEvent(event)


# ──────────────── Frameless-window chrome ────────────────


class WindowControlButton(QPushButton):
    """A minimize / maximize / close button drawn with crisp custom paint."""

    KINDS = {"min", "max", "close"}

    def __init__(self, kind: str, parent: QWidget | None = None):
        if kind not in self.KINDS:
            raise ValueError(kind)
        super().__init__(parent)
        self.kind = kind
        self._hover = False
        self.setFixedSize(46, 38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip({
            "min": "Minimize",
            "max": "Maximize / Restore",
            "close": "Close",
        }[kind])

    def enterEvent(self, ev):
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, _ev):
        with QPainter(self) as p:
            if self._hover:
                if self.kind == "close":
                    bg = QColor(theme.DANGER) if not self.isDown() else QColor(theme.DANGER_HOVER)
                else:
                    bg = QColor(theme.SURFACE_2) if not self.isDown() else QColor(theme.SURFACE_3)
                p.fillRect(self.rect(), bg)

            if self.kind == "close" and self._hover:
                color = QColor(theme.BG)
            elif self._hover:
                color = QColor(theme.TEXT)
            else:
                color = QColor(theme.TEXT_DIM)

            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(color, 1.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)

            cx, cy = self.width() / 2.0, self.height() / 2.0

            if self.kind == "min":
                p.drawLine(QPointF(cx - 5, cy + 0.5), QPointF(cx + 5, cy + 0.5))
            elif self.kind == "max":
                top = self.window()
                is_max = bool(top and top.isMaximized())
                if is_max:
                    p.drawRect(QRectF(cx - 5, cy - 2, 8, 7))
                    p.drawLine(QPointF(cx - 3, cy - 4), QPointF(cx + 5, cy - 4))
                    p.drawLine(QPointF(cx + 5, cy - 4), QPointF(cx + 5, cy + 3))
                else:
                    p.drawRect(QRectF(cx - 5, cy - 4, 10, 8))
            elif self.kind == "close":
                p.drawLine(QPointF(cx - 4.5, cy - 4.5), QPointF(cx + 4.5, cy + 4.5))
                p.drawLine(QPointF(cx - 4.5, cy + 4.5), QPointF(cx + 4.5, cy - 4.5))


class FrameWrapper(QWidget):
    """Wraps the central content with a small gutter used for window-edge resize.

    The wrapper claims the outer ``MARGIN`` pixels of the window. Mouse events
    only reach the wrapper when the cursor is inside that gutter, so the
    content widget keeps its normal cursor behavior.
    """

    MARGIN = 4

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("frameWrapper")
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(self.MARGIN, self.MARGIN, self.MARGIN, self.MARGIN)
        self._layout.setSpacing(0)

    def set_content(self, widget: QWidget) -> None:
        widget.setCursor(Qt.CursorShape.ArrowCursor)
        self._layout.addWidget(widget)

    def set_resize_enabled(self, enabled: bool) -> None:
        if enabled:
            self._layout.setContentsMargins(
                self.MARGIN, self.MARGIN, self.MARGIN, self.MARGIN
            )
        else:
            self._layout.setContentsMargins(0, 0, 0, 0)
            self.unsetCursor()

    def _edges_at(self, pos: QPoint):
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        m = self.MARGIN
        edges = Qt.Edge(0)
        if x < m:
            edges |= Qt.Edge.LeftEdge
        if x > w - m:
            edges |= Qt.Edge.RightEdge
        if y < m:
            edges |= Qt.Edge.TopEdge
        if y > h - m:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mouseMoveEvent(self, ev):
        win = self.window()
        if win.isMaximized() or win.isFullScreen():
            self.unsetCursor()
            super().mouseMoveEvent(ev)
            return
        edges = self._edges_at(ev.position().toPoint())
        if edges != Qt.Edge(0):
            self.setCursor(self._cursor_for(edges))
        else:
            self.unsetCursor()
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev):
        self.unsetCursor()
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        win = self.window()
        if (
            ev.button() == Qt.MouseButton.LeftButton
            and not (win.isMaximized() or win.isFullScreen())
        ):
            edges = self._edges_at(ev.position().toPoint())
            if edges != Qt.Edge(0):
                wh = win.windowHandle()
                if wh:
                    wh.startSystemResize(edges)
                    ev.accept()
                    return
        super().mousePressEvent(ev)

    @staticmethod
    def _cursor_for(edges) -> Qt.CursorShape:
        top = bool(edges & Qt.Edge.TopEdge)
        bot = bool(edges & Qt.Edge.BottomEdge)
        left = bool(edges & Qt.Edge.LeftEdge)
        right = bool(edges & Qt.Edge.RightEdge)
        if (top and left) or (bot and right):
            return Qt.CursorShape.SizeFDiagCursor
        if (top and right) or (bot and left):
            return Qt.CursorShape.SizeBDiagCursor
        if top or bot:
            return Qt.CursorShape.SizeVerCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        return Qt.CursorShape.ArrowCursor


class TitleBar(QWidget):
    """The custom title bar. Handles threshold-based drag and double-click maximize."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("topBar")
        self._press_pos: QPoint | None = None
        self._dragging = False

    def _on_button_area(self, pos: QPoint) -> bool:
        child = self.childAt(pos)
        while child is not None and child is not self:
            if isinstance(child, QPushButton):
                return True
            child = child.parentWidget()
        return False

    def mousePressEvent(self, ev):
        if (
            ev.button() == Qt.MouseButton.LeftButton
            and not self._on_button_area(ev.position().toPoint())
        ):
            self._press_pos = ev.globalPosition().toPoint()
            self._dragging = False
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._press_pos is None or self._dragging:
            super().mouseMoveEvent(ev)
            return
        gp = ev.globalPosition().toPoint()
        if (gp - self._press_pos).manhattanLength() > QApplication.startDragDistance():
            self._dragging = True
            self._press_pos = None
            wh = self.window().windowHandle()
            if wh:
                wh.startSystemMove()
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._press_pos = None
        self._dragging = False
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if (
            ev.button() == Qt.MouseButton.LeftButton
            and not self._on_button_area(ev.position().toPoint())
        ):
            win = self.window()
            if win.isMaximized():
                win.showNormal()
            else:
                win.showMaximized()
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)


# ──────────────── Command palette ────────────────


class CommandPalette(QWidget):
    """Frameless Ctrl+K palette: text input on top, ranked result list below.

    The palette is decoupled from the data layer — it emits
    :attr:`query_changed` with each keystroke and expects the caller to
    populate results via :meth:`set_results`. Activating a result emits
    :attr:`activated` with the original result dict.
    """

    query_changed = Signal(str)
    activated = Signal(dict)
    closed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(
            parent,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint,
        )
        self.setObjectName("commandPalette")
        self.setMinimumSize(560, 420)
        self._results: list[dict] = []
        self._build()

    def _build(self) -> None:
        from PySide6.QtWidgets import QListWidget
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        self.input = QLineEdit()
        self.input.setObjectName("commandPaletteInput")
        self.input.setPlaceholderText(
            "Search projects, playlists, videos, tags, notes…"
        )
        self.input.setClearButtonEnabled(True)
        self.input.textChanged.connect(self.query_changed.emit)
        self.input.returnPressed.connect(self._activate_current)
        self.input.installEventFilter(self)
        v.addWidget(self.input)

        self.list = QListWidget()
        self.list.setObjectName("commandPaletteList")
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.itemActivated.connect(lambda _it: self._activate_current())
        self.list.itemDoubleClicked.connect(lambda _it: self._activate_current())
        v.addWidget(self.list, 1)

        self.hint = QLabel("↑↓ navigate · ↵ open · Esc to close")
        self.hint.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px; background: transparent;"
        )
        v.addWidget(self.hint)

    def set_results(self, results: list[dict]) -> None:
        from PySide6.QtWidgets import QListWidgetItem
        self._results = results
        self.list.clear()
        for r in results:
            it = QListWidgetItem(self._format(r))
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    @staticmethod
    def _format(r: dict) -> str:
        type_label = {
            "project": "Project",
            "playlist": "Playlist",
            "video": "Video",
            "tag": "Tag",
            "todo": "Todo",
            "notes": "Notes",
        }.get(r.get("type", ""), "?")
        label = r.get("label", "")
        sub = r.get("sublabel", "")
        # Wide spacing between type and label for scanability.
        return f"  {type_label:<10}    {label}" + (f"     ·  {sub}" if sub else "")

    def _activate_current(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self._results):
            self.activated.emit(self._results[row])

    def _move_selection(self, delta: int) -> None:
        if self.list.count() == 0:
            return
        new_row = max(0, min(self.list.count() - 1, self.list.currentRow() + delta))
        self.list.setCurrentRow(new_row)

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._move_selection(1)
                return True
            if key == Qt.Key.Key_Up:
                self._move_selection(-1)
                return True
            if key == Qt.Key.Key_PageDown:
                self._move_selection(8)
                return True
            if key == Qt.Key.Key_PageUp:
                self._move_selection(-8)
                return True
            if key == Qt.Key.Key_Escape:
                self.closed.emit()
                self.close()
                return True
        return super().eventFilter(obj, event)

    def focusOutEvent(self, event) -> None:
        # If focus left the palette tree entirely (not to a child input or
        # the list), close it. Otherwise normal focus behavior.
        super().focusOutEvent(event)


# ──────────────── WYSIWYG markdown highlighter ────────────────


import re as _re


class MarkdownHighlighter(QSyntaxHighlighter):
    """Live markdown formatting inside any QTextEdit's document.

    Headings get larger and bold, ``**bold**`` renders bold, ``*italic*``
    italic, fenced code blocks get a monospace background, etc. The
    syntax markers (``#``, ``**``, backticks…) stay in the text — just
    dimmed — so the document is still raw editable Markdown.

    Formats are rebuilt lazily when the active theme or font size
    changes so the appearance always matches the rest of the app.
    """

    HEADING_RE = _re.compile(r"^(\s*)(#{1,6})\s+")
    BOLD_RE = _re.compile(r"\*\*([^*\n][^\n]*?)\*\*")
    UNDERSCORE_BOLD_RE = _re.compile(r"__([^_\n][^\n]*?)__")
    ITALIC_RE = _re.compile(r"(?<![\*\w])\*([^*\n]+?)\*(?!\*)")
    UNDERSCORE_ITALIC_RE = _re.compile(r"(?<![_\w])_([^_\n]+?)_(?!_)")
    INLINE_CODE_RE = _re.compile(r"`([^`\n]+)`")
    STRIKE_RE = _re.compile(r"~~([^~\n]+?)~~")
    LINK_RE = _re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
    LIST_RE = _re.compile(r"^(\s*)([-*+]|\d+\.)\s")
    QUOTE_RE = _re.compile(r"^(\s*>+\s?)")
    HR_RE = _re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
    FENCE_RE = _re.compile(r"^\s*```")

    STATE_NONE = 0
    STATE_CODE = 1

    def __init__(self, document):
        super().__init__(document)
        self._cached_theme = None
        self._cached_size = None
        self._build_formats()

    def _build_formats(self) -> None:
        # Imported lazily so this module stays import-safe at startup.
        from .theme import (
            ACCENT, ACCENT_2, SURFACE_2, TEXT, TEXT_DIM, TEXT_MUTED,
            current_font_size, current_theme_name,
        )
        self._cached_theme = current_theme_name()
        self._cached_size = current_font_size()
        # Editor's #notes rule uses base_size+1; match that as our root.
        base = max(self._cached_size + 1, 12)

        def fmt() -> QTextCharFormat:
            return QTextCharFormat()

        # Headings — relative scale from base.
        scales = [1.85, 1.55, 1.30, 1.15, 1.05, 1.0]
        self._headings: list[QTextCharFormat] = []
        for s in scales:
            f = fmt()
            f.setFontPointSize(round(base * s))
            f.setFontWeight(QFont.Weight.Bold)
            f.setForeground(QColor(TEXT))
            self._headings.append(f)

        self._bold = fmt()
        self._bold.setFontWeight(QFont.Weight.Bold)

        self._italic = fmt()
        self._italic.setFontItalic(True)

        self._bold_italic = fmt()
        self._bold_italic.setFontWeight(QFont.Weight.Bold)
        self._bold_italic.setFontItalic(True)

        self._inline_code = fmt()
        self._inline_code.setFontFamilies(
            ["JetBrains Mono", "Fira Code", "DejaVu Sans Mono", "monospace"]
        )
        self._inline_code.setBackground(QColor(SURFACE_2))
        self._inline_code.setForeground(QColor(ACCENT_2))

        self._code_block = fmt()
        self._code_block.setFontFamilies(
            ["JetBrains Mono", "Fira Code", "DejaVu Sans Mono", "monospace"]
        )
        self._code_block.setBackground(QColor(SURFACE_2))
        self._code_block.setForeground(QColor(TEXT_DIM))

        self._strike = fmt()
        self._strike.setFontStrikeOut(True)
        self._strike.setForeground(QColor(TEXT_MUTED))

        self._link = fmt()
        self._link.setForeground(QColor(ACCENT))
        self._link.setFontUnderline(True)

        self._quote = fmt()
        self._quote.setForeground(QColor(TEXT_DIM))
        self._quote.setFontItalic(True)

        # Dim color for the markers themselves (the `#`, `**`, `[]()`…).
        self._marker = fmt()
        self._marker.setForeground(QColor(TEXT_MUTED))

        self._hr = fmt()
        self._hr.setForeground(QColor(TEXT_MUTED))

    def _maybe_rebuild(self) -> None:
        from .theme import current_font_size, current_theme_name
        if (self._cached_theme != current_theme_name()
                or self._cached_size != current_font_size()):
            self._build_formats()

    def refresh(self) -> None:
        """Rebuild formats and re-highlight every block.

        Call after a theme or font change so colors and heading sizes
        match the new settings.
        """
        self._build_formats()
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        self._maybe_rebuild()

        # ───── Fenced code blocks (multi-line) ─────
        prev = self.previousBlockState()
        in_code = (prev == self.STATE_CODE)
        if self.FENCE_RE.match(text):
            self.setFormat(0, len(text), self._code_block)
            # Toggle the state for the next block.
            self.setCurrentBlockState(
                self.STATE_NONE if in_code else self.STATE_CODE
            )
            return
        if in_code:
            self.setFormat(0, len(text), self._code_block)
            self.setCurrentBlockState(self.STATE_CODE)
            return
        self.setCurrentBlockState(self.STATE_NONE)

        # ───── Horizontal rule ─────
        if self.HR_RE.match(text):
            self.setFormat(0, len(text), self._hr)
            return

        # ───── Heading ─────
        m = self.HEADING_RE.match(text)
        if m:
            level = len(m.group(2))
            heading_fmt = self._headings[level - 1]
            self.setFormat(0, len(text), heading_fmt)
            # Dim the leading `#`s + the space after them.
            self.setFormat(m.start(2), len(m.group(2)) + 1, self._marker)
            # Inline patterns can still apply inside the heading body — pass the
            # heading's point size so bold/code/etc don't reset the glyph size
            # (setFormat REPLACES the char format, it doesn't merge).
            self._apply_inline(
                text, skip_marker_dimming=True,
                heading_size=heading_fmt.fontPointSize(),
            )
            return

        # ───── Blockquote ─────
        m = self.QUOTE_RE.match(text)
        if m:
            self.setFormat(0, len(text), self._quote)
            self.setFormat(0, len(m.group(0)), self._marker)
            # Fall through so inline patterns still work inside the quote.

        # ───── List marker ─────
        m = self.LIST_RE.match(text)
        if m:
            self.setFormat(
                len(m.group(1)), len(m.group(2)) + 1, self._marker
            )

        self._apply_inline(text)

    def _apply_inline(
        self,
        text: str,
        *,
        skip_marker_dimming: bool = False,
        heading_size: float | None = None,
    ) -> None:
        def sized(fmt: QTextCharFormat) -> QTextCharFormat:
            # Inside a heading, carry the heading point size onto each inline
            # format so it doesn't shrink back to the base size.
            if not heading_size or heading_size <= 0:
                return fmt
            f = QTextCharFormat(fmt)
            f.setFontPointSize(heading_size)
            return f

        # Inline code first so its background/foreground claim its range; record
        # the spans so later inline formats don't overwrite the code styling
        # when their markers happen to fall inside the backticks.
        code_spans: list[tuple[int, int]] = []
        for m in self.INLINE_CODE_RE.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), sized(self._inline_code))
            code_spans.append((m.start(), m.end()))

        def in_code(start: int, end: int) -> bool:
            return any(s <= start and end <= e for s, e in code_spans)

        def apply(regex, fmt, marker_len: int) -> None:
            for m in regex.finditer(text):
                if in_code(m.start(), m.end()):
                    continue
                self.setFormat(m.start(), m.end() - m.start(), sized(fmt))
                if not skip_marker_dimming:
                    self.setFormat(m.start(), marker_len, self._marker)
                    self.setFormat(m.end() - marker_len, marker_len, self._marker)

        apply(self.BOLD_RE, self._bold, 2)
        apply(self.UNDERSCORE_BOLD_RE, self._bold, 2)
        apply(self.ITALIC_RE, self._italic, 1)
        apply(self.UNDERSCORE_ITALIC_RE, self._italic, 1)
        apply(self.STRIKE_RE, self._strike, 2)

        for m in self.LINK_RE.finditer(text):
            if in_code(m.start(), m.end()):
                continue
            # `[label]`
            label_end = m.start() + 1 + len(m.group(1))  # past the closing ]
            self.setFormat(m.start() + 1, len(m.group(1)), sized(self._link))
            if not skip_marker_dimming:
                # Dim everything except the visible label text.
                self.setFormat(m.start(), 1, self._marker)            # [
                self.setFormat(label_end, m.end() - label_end, self._marker)  # ](url)


# ──────────────── Playlists ────────────────


def _format_duration(seconds: int | None) -> str:
    # Coerce first — a persisted/non-int value must not raise on comparison.
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class PlaylistRow(QWidget):
    """One row in the playlists sidebar."""

    tag_right_clicked = Signal(str)

    def __init__(self, playlist, store, parent: QWidget | None = None):
        super().__init__(parent)
        self.playlist = playlist
        self.store = store
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(5)

        # Title row holds the title and a notes indicator dot (mirrors
        # ProjectRow), so users can see at a glance which playlists carry
        # general notes.
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.title_label = QLabel(self.playlist.title)
        tf = QFont()
        tf.setPointSize(11)
        tf.setWeight(QFont.Weight.DemiBold)
        self.title_label.setFont(tf)
        self.title_label.setStyleSheet(f"color: {theme.TEXT}; background: transparent;")
        self.title_label.setWordWrap(False)
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        title_row.addWidget(self.title_label, 1)

        self.pin_dot = QLabel("◆")
        self.pin_dot.setStyleSheet(
            f"color: {theme.ACCENT}; font-size: 10px; background: transparent;"
        )
        self.pin_dot.setToolTip("Pinned")
        self.pin_dot.setVisible(self.playlist.pinned)
        title_row.addWidget(self.pin_dot, alignment=Qt.AlignmentFlag.AlignTop)

        self.notes_dot = QLabel("●")
        self.notes_dot.setStyleSheet(
            f"color: {theme.ACCENT_2}; font-size: 11px; background: transparent;"
        )
        self.notes_dot.setToolTip("Has notes")
        self.notes_dot.setVisible(bool(self.playlist.notes.strip()))
        title_row.addWidget(
            self.notes_dot, alignment=Qt.AlignmentFlag.AlignTop
        )
        layout.addLayout(title_row)

        self.meta_label = QLabel("")
        self.meta_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 10px; background: transparent;"
        )
        layout.addWidget(self.meta_label)

        self._tag_wrap = QWidget()
        self._tag_layout = QHBoxLayout(self._tag_wrap)
        self._tag_layout.setSpacing(4)
        self._tag_layout.setContentsMargins(0, 2, 0, 0)
        layout.addWidget(self._tag_wrap)

        self._refresh_meta()
        self._populate_tags()

    def _refresh_meta(self) -> None:
        pl = self.playlist
        bits: list[str] = []
        if pl.uploader:
            bits.append(pl.uploader)
        bits.append(f"{pl.watched}/{pl.total} watched")
        self.meta_label.setText("  ·  ".join(bits))

    def _populate_tags(self) -> None:
        # Detach synchronously so deleteLater'd chips don't paint over the
        # new layout — see ProjectRow._populate_tags for context.
        while self._tag_layout.count():
            item = self._tag_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if not self.playlist.tags:
            self._tag_wrap.setVisible(False)
        else:
            visible_count = 2  # see ProjectRow._populate_tags
            for t in self.playlist.tags[:visible_count]:
                chip = TagChip(t, tag_color(t, self.store.tag_colors))
                chip.setToolTip(f"#{t} · right-click to change color")
                chip.right_clicked.connect(
                    lambda tag=t: self.tag_right_clicked.emit(tag)
                )
                self._tag_layout.addWidget(chip)
            if len(self.playlist.tags) > visible_count:
                more = QLabel(f"+{len(self.playlist.tags) - visible_count}")
                more.setStyleSheet(
                    f"color: {theme.TEXT_MUTED}; font-size: 10px; font-weight: 600;"
                )
                self._tag_layout.addWidget(more)
            self._tag_layout.addStretch()
            self._tag_wrap.setVisible(True)
            self._tag_layout.activate()
            self._tag_wrap.updateGeometry()
        # Invalidate the row's outer layout so the next sizeHint() picks
        # up the new (possibly shorter) total height — see the matching
        # comment in ProjectRow._populate_tags.
        if self.layout() is not None:
            self.layout().invalidate()
        self.updateGeometry()

    def refresh(self) -> None:
        self.title_label.setText(self.playlist.title)
        self.notes_dot.setVisible(bool(self.playlist.notes.strip()))
        self.pin_dot.setVisible(self.playlist.pinned)
        self._refresh_meta()
        self._populate_tags()

    def refresh_tags(self) -> None:
        self._populate_tags()

    def set_has_notes(self, has: bool) -> None:
        self.notes_dot.setVisible(has)

    def set_pinned(self, pinned: bool) -> None:
        self.playlist.pinned = pinned
        self.pin_dot.setVisible(pinned)


class VideoRow(QWidget):
    """One row in the video list inside a playlist detail panel."""

    completion_changed = Signal(bool)

    def __init__(self, video, parent: QWidget | None = None):
        super().__init__(parent)
        self.video = video
        self._build()

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 12, 7)
        layout.setSpacing(12)

        self.toggle = CompletionToggle(self.video.completed)
        self.toggle.toggled.connect(self._on_toggle)
        layout.addWidget(self.toggle, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.title_label = QLabel(self.video.title)
        tf = QFont()
        tf.setPointSize(10)
        self.title_label.setFont(tf)
        self.title_label.setMinimumWidth(120)
        layout.addWidget(self.title_label, 1)

        self.notes_dot = QLabel("●")
        self.notes_dot.setStyleSheet(
            f"color: {theme.ACCENT_2}; font-size: 10px; background: transparent;"
        )
        self.notes_dot.setToolTip("Has notes")
        self.notes_dot.setVisible(bool(self.video.notes.strip()))
        layout.addWidget(self.notes_dot, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.duration_label = QLabel(_format_duration(self.video.duration))
        self.duration_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 10px; font-family: 'JetBrains Mono', monospace; background: transparent;"
        )
        self.duration_label.setMinimumWidth(54)
        self.duration_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self.duration_label)

        self._restyle()

    def _on_toggle(self, checked: bool) -> None:
        self.video.completed = checked
        self._restyle()
        self.completion_changed.emit(checked)

    def _restyle(self) -> None:
        font = self.title_label.font()
        strike = self.video.completed or self.video.unavailable
        font.setStrikeOut(strike)
        self.title_label.setFont(font)
        if self.video.unavailable:
            color = theme.TEXT_MUTED
        elif self.video.completed:
            color = theme.TEXT_MUTED
        else:
            color = theme.TEXT
        self.title_label.setStyleSheet(f"color: {color}; background: transparent;")
        if self.video.unavailable:
            self.title_label.setToolTip("This video is no longer available")
        else:
            self.title_label.setToolTip(self.video.title)

    # ── in-place updates ──

    def set_completed(self, checked: bool) -> None:
        self.video.completed = checked
        self.toggle.blockSignals(True)
        self.toggle.setChecked(checked)
        self.toggle.blockSignals(False)
        self._restyle()

    def set_has_notes(self, has: bool) -> None:
        self.notes_dot.setVisible(has)


# ──────────────── Todo row ────────────────


class TodoRow(QWidget):
    """One task in the Todo tab: a completion toggle, the text (double-click to
    edit inline), and a delete button."""

    toggled = Signal(bool)
    remove_clicked = Signal()
    edited = Signal(str)

    def __init__(self, todo, parent: QWidget | None = None):
        super().__init__(parent)
        self.todo = todo
        self._editing = False
        self._build()

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 8, 9)
        layout.setSpacing(12)

        self.toggle = CompletionToggle(self.todo.done)
        self.toggle.toggled.connect(self._on_toggle)
        layout.addWidget(self.toggle, alignment=Qt.AlignmentFlag.AlignTop)

        self.label = QLabel()
        lf = QFont()
        lf.setPointSize(11)
        self.label.setFont(lf)
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)

        # Inline editor — hidden until the row is double-clicked. Lives in the
        # same layout slot; only one of label/editor is visible at a time.
        self.editor = QLineEdit(self.todo.text)
        self.editor.setVisible(False)
        self.editor.setStyleSheet(
            f"QLineEdit {{ background-color: {theme.SURFACE_2};"
            f" border: 1px solid {theme.ACCENT}; border-radius: 6px;"
            f" padding: 4px 8px; color: {theme.TEXT}; font-size: 11px;"
            f" selection-background-color: {theme.ACCENT};"
            f" selection-color: {theme.BG}; }}"
        )
        self.editor.returnPressed.connect(self._commit_edit)
        self.editor.installEventFilter(self)
        layout.addWidget(self.editor, 1)

        self.remove_btn = QPushButton("×")
        self.remove_btn.setObjectName("ghost")
        self.remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_btn.setFixedSize(24, 24)
        self.remove_btn.setToolTip("Delete task")
        self.remove_btn.clicked.connect(self.remove_clicked.emit)
        layout.addWidget(self.remove_btn, alignment=Qt.AlignmentFlag.AlignTop)

        self._restyle()

    def _on_toggle(self, checked: bool) -> None:
        self.todo.done = checked
        self._restyle()
        self.toggled.emit(checked)

    def _restyle(self) -> None:
        font = self.label.font()
        font.setStrikeOut(self.todo.done)
        self.label.setFont(font)
        text = self.todo.text.strip()
        if text:
            self.label.setText(text)
            color = theme.TEXT_MUTED if self.todo.done else theme.TEXT
            self.label.setToolTip("")
        else:
            self.label.setText("(empty — double-click to edit)")
            color = theme.TEXT_MUTED
        self.label.setStyleSheet(f"color: {color}; background: transparent;")

    # ── inline edit ──

    def mouseDoubleClickEvent(self, event) -> None:
        try:
            pos = event.position().toPoint()
        except AttributeError:
            pos = event.pos()
        child = self.childAt(pos)
        if child is self.toggle or child is self.remove_btn:
            super().mouseDoubleClickEvent(event)
            return
        self.begin_edit()
        event.accept()

    def begin_edit(self) -> None:
        if self._editing:
            return
        self._editing = True
        self.editor.setText(self.todo.text)
        self.label.setVisible(False)
        self.editor.setVisible(True)
        self.editor.setFocus()
        self.editor.selectAll()

    def _commit_edit(self) -> None:
        if not self._editing:
            return
        self._editing = False
        text = self.editor.text().strip()
        self.editor.setVisible(False)
        self.label.setVisible(True)
        if text != self.todo.text:
            self.todo.text = text
            self._restyle()
            self.edited.emit(text)
        else:
            self._restyle()

    def _cancel_edit(self) -> None:
        if not self._editing:
            return
        self._editing = False
        self.editor.setVisible(False)
        self.label.setVisible(True)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.editor:
            if (event.type() == QEvent.Type.KeyPress
                    and event.key() == Qt.Key.Key_Escape):
                self._cancel_edit()
                return True
            if event.type() == QEvent.Type.FocusOut and self._editing:
                self._commit_edit()
        return super().eventFilter(obj, event)

    # ── in-place updates ──

    def set_done(self, done: bool) -> None:
        self.todo.done = done
        self.toggle.blockSignals(True)
        self.toggle.setChecked(done)
        self.toggle.blockSignals(False)
        self._restyle()


# ──────────────── Update banner ────────────────


class UpdateBanner(QWidget):
    """A thin, dismissible bar shown when a newer release is available."""

    download_clicked = Signal()
    dismissed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("updateBanner")
        self.setVisible(False)
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 7, 10, 7)
        h.setSpacing(10)

        self.label = QLabel("")
        self.label.setObjectName("updateBannerText")
        h.addWidget(self.label, 1)

        self.download_btn = QPushButton("Download")
        self.download_btn.setObjectName("updateBannerButton")
        self.download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download_btn.clicked.connect(self.download_clicked.emit)
        h.addWidget(self.download_btn)

        self.dismiss_btn = QPushButton("×")
        self.dismiss_btn.setObjectName("iconButton")
        self.dismiss_btn.setFixedSize(26, 26)
        self.dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dismiss_btn.setToolTip("Dismiss")
        self.dismiss_btn.clicked.connect(self.dismissed.emit)
        h.addWidget(self.dismiss_btn)

    def show_update(self, version: str) -> None:
        self.label.setText(f"Projectum {version} is available.")
        self.setVisible(True)


# ──────────────────────────── Calendar ──────────────────────────────────────

WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]  # WEEK_START=0
# Fixed English month names — the rest of the UI is English, so we don't want
# strftime("%B") pulling localized names that clash with the weekday headers.
MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


def _ink_on(fill_hex: str) -> str:
    """Black or white — whichever reads better on ``fill_hex`` (WCAG contrast)."""
    return ("#ffffff" if theme.contrast_ratio("#ffffff", fill_hex)
            >= theme.contrast_ratio("#0b0b12", fill_hex) else "#0b0b12")


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


class _CalendarScanSignals(QObject):
    done = Signal(list, list)  # (items, skipped_folder_paths)


class CalendarScanRunnable(QRunnable):
    """Off-thread disk scan of the tracked folders (excludes the open folder,
    which the UI thread reads from the live store). Fail-soft: never raises."""

    def __init__(self, folder_paths: list[str], exclude_resolved: str | None = None):
        super().__init__()
        self._folders = list(folder_paths)
        self._exclude = exclude_resolved
        self.signals = _CalendarScanSignals()

    def run(self) -> None:
        try:
            items, skipped = cal.scan_disk(self._folders, self._exclude)
        except Exception:
            items, skipped = [], []
        self.signals.done.emit(items, skipped)


class _KindDot(QWidget):
    """A small color dot for the calendar legend (theme-reactive)."""

    def __init__(self, kind: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._kind = kind
        self.setFixedSize(12, 12)

    def paintEvent(self, _event) -> None:
        color = getattr(theme, KIND_COLOR_KEY.get(self._kind, "ACCENT"), theme.ACCENT)
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(QColor(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(1, 1, 10, 10))


class MonthGrid(QWidget):
    """Custom-painted month grid: 6 weeks × 7 days with scheduled-item bars.

    Read-only here (paints what's scheduled); drag/resize is layered on in a
    later pass. Bars come from the pure ``calendar.layout_month`` helper, so the
    week-splitting and lane-packing logic stays testable without Qt.
    """

    day_clicked = Signal(object)       # a datetime.date
    item_activated = Signal(object)    # a ScheduledItem
    item_context = Signal(object, QPoint)  # (ScheduledItem, global pos) on right-click
    item_rescheduled = Signal(object, str, str)  # (item, start_iso, end_iso) after drag
    external_drop = Signal(object)     # a date — a tray chip was dropped here

    PAD = 8
    HEADER_H = 26
    DAY_NUM_H = 24
    LANE_H = 18
    LANE_GAP = 3
    BAR_PAD_X = 3
    EDGE = 7  # px hot-zone at a bar's end for resize vs. move

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        today = date.today()
        self._year = today.year
        self._month = today.month
        self._today = today
        self._items: list = []
        self._grid_start = cal.month_grid_start(self._year, self._month)
        self._bars: list = []
        self._drag: dict | None = None   # in-progress move/resize of a bar
        self._press_day = None           # tentative day click
        self._drop_day = None            # day highlighted under an external drop
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(380)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)

    # ── public API ──
    def set_month(self, year: int, month: int) -> None:
        self._year, self._month = year, month
        self._recompute()

    def set_items(self, items: list) -> None:
        self._items = items or []
        self._recompute()

    def set_today(self, day: date) -> None:
        self._today = day
        self.update()

    def year_month(self) -> tuple[int, int]:
        return self._year, self._month

    def _recompute(self) -> None:
        self._grid_start = cal.month_grid_start(self._year, self._month)
        self._bars = cal.layout_month(self._items, self._grid_start)
        self.update()

    # ── geometry ──
    def _geom(self) -> tuple[float, float, float, float]:
        ox = float(self.PAD)
        oy = float(self.PAD + self.HEADER_H)
        cw = (self.width() - 2 * self.PAD) / 7.0
        ch = (self.height() - self.PAD - oy) / 6.0
        return ox, oy, cw, ch

    def _cell_rect(self, week: int, col: int) -> QRectF:
        ox, oy, cw, ch = self._geom()
        return QRectF(ox + col * cw, oy + week * ch, cw, ch)

    def _lane_pitch(self) -> int:
        return self.LANE_H + self.LANE_GAP

    def _max_lanes(self, ch: float) -> int:
        usable = ch - self.DAY_NUM_H - 4
        return max(0, int(usable // self._lane_pitch()))

    def _draw_limit(self, ch: float) -> int:
        """Number of bar lanes actually drawn (one fewer than fits if anything
        overflows, to leave room for the '+N more' marker). Shared by paint and
        hit-testing so a click on the marker doesn't activate a hidden bar."""
        max_fit = self._max_lanes(ch)
        overflow = (any(b.lane >= max_fit for b in self._bars) if max_fit > 0
                    else bool(self._bars))
        return max(0, (max_fit - 1) if overflow else max_fit)

    def _cell_date(self, week: int, col: int) -> date:
        return self._grid_start + timedelta(days=week * 7 + col)

    def _bar_rect(self, bar) -> QRectF:
        left_cell = self._cell_rect(bar.week, bar.col_start)
        right_cell = self._cell_rect(bar.week, bar.col_end)
        x0 = left_cell.left() + self.BAR_PAD_X
        x1 = right_cell.right() - self.BAR_PAD_X
        y = left_cell.top() + self.DAY_NUM_H + bar.lane * self._lane_pitch()
        return QRectF(x0, y, max(6.0, x1 - x0), self.LANE_H)

    def _bar_at(self, pos: QPointF):
        """The drawn bar whose rect contains ``pos`` (or None)."""
        draw_limit = self._draw_limit(self._geom()[3])
        for bar in self._bars:
            if bar.lane >= draw_limit or bar.item_index >= len(self._items):
                continue
            if self._bar_rect(bar).contains(pos):
                return bar
        return None

    def _day_at(self, pos: QPointF):
        """The date of the grid cell under ``pos`` (or None if outside)."""
        ox, oy, cw, ch = self._geom()
        if pos.x() < ox or pos.y() < oy or cw <= 0 or ch <= 0:
            return None
        col = int((pos.x() - ox) // cw)
        week = int((pos.y() - oy) // ch)
        if not (0 <= col <= 6 and 0 <= week <= 5):
            return None
        return self._cell_date(week, col)

    # ── painting ──
    def paintEvent(self, _event) -> None:
        ox, oy, cw, ch = self._geom()
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.fillRect(self.rect(), QColor(theme.BG))
            self._paint_weekday_header(p, ox, cw)
            self._paint_cells(p, cw, ch)
            self._paint_bars(p, cw, ch)
            if self._drag is not None and self._drag.get("preview") is not None:
                self._paint_day_span(p, *self._drag["preview"])
            elif self._drop_day is not None:
                self._paint_day_span(p, self._drop_day, self._drop_day)

    def _paint_day_span(self, p: QPainter, start: date, end: date) -> None:
        """Translucent accent highlight over a [start, end] day range — the
        live preview while dragging a bar or hovering a tray-chip drop."""
        if start > end:
            start, end = end, start
        fill = QColor(theme.ACCENT)
        fill.setAlpha(55)
        p.setBrush(fill)
        p.setPen(QPen(QColor(theme.ACCENT), 1.5))
        d = start
        while d <= end:
            off = (d - self._grid_start).days
            if 0 <= off < cal.GRID_DAYS:
                week, col = divmod(off, 7)
                cell = self._cell_rect(week, col).adjusted(1.5, 1.5, -1.5, -1.5)
                p.drawRoundedRect(cell, 7, 7)
            d += timedelta(days=1)

    def _paint_weekday_header(self, p: QPainter, ox: float, cw: float) -> None:
        f = QFont(self.font())
        f.setPointSizeF(max(8.0, self.font().pointSizeF() - 1))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT_MUTED))
        for col, label in enumerate(WEEKDAY_LABELS):
            r = QRectF(ox + col * cw, self.PAD, cw, self.HEADER_H)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)

    def _paint_cells(self, p: QPainter, cw: float, ch: float) -> None:
        num_font = QFont(self.font())
        num_font.setPointSizeF(max(8.5, self.font().pointSizeF() - 0.5))
        for week in range(6):
            for col in range(7):
                d = self._cell_date(week, col)
                in_month = d.month == self._month
                is_today = d == self._today
                is_weekend = col >= 5
                cell = self._cell_rect(week, col).adjusted(1.5, 1.5, -1.5, -1.5)

                if not in_month:
                    bg = QColor(theme.BG)
                elif is_weekend:
                    bg = QColor(theme.SURFACE_2)
                else:
                    bg = QColor(theme.SURFACE)
                p.setBrush(bg)
                p.setPen(QPen(QColor(theme.BORDER), 1.0))
                p.drawRoundedRect(cell, 7, 7)

                # Day number (today gets an accent badge).
                p.setFont(num_font)
                num_box = QRectF(cell.left() + 6, cell.top() + 3,
                                 cell.width() - 12, self.DAY_NUM_H - 4)
                if is_today:
                    badge_d = min(self.DAY_NUM_H - 6, 22)
                    badge = QRectF(num_box.left() - 2, num_box.top(), badge_d, badge_d)
                    p.setBrush(QColor(theme.ACCENT))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawRoundedRect(badge, badge_d / 2, badge_d / 2)
                    p.setPen(QColor(_ink_on(theme.ACCENT)))
                    p.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(d.day))
                else:
                    p.setPen(QColor(theme.TEXT if in_month else theme.TEXT_MUTED))
                    p.drawText(num_box, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                               str(d.day))

    def _paint_bars(self, p: QPainter, cw: float, ch: float) -> None:
        pitch = self._lane_pitch()
        draw_limit = self._draw_limit(ch)
        hidden: dict[tuple[int, int], int] = {}
        bar_font = QFont(self.font())
        bar_font.setPointSizeF(max(8.0, self.font().pointSizeF() - 1.5))
        fm = QFontMetrics(bar_font)

        for bar in self._bars:
            if bar.item_index >= len(self._items):
                continue
            if bar.lane >= draw_limit:
                for c in range(bar.col_start, bar.col_end + 1):
                    hidden[(bar.week, c)] = hidden.get((bar.week, c), 0) + 1
                continue
            item = self._items[bar.item_index]
            rect = self._bar_rect(bar)

            fill = QColor(getattr(theme, KIND_COLOR_KEY.get(item.kind, "ACCENT"), theme.ACCENT))
            if item.done:
                fill.setAlpha(110)
            self._draw_bar_shape(p, rect, fill, bar.continues_left, bar.continues_right)

            # Title text.
            p.setFont(bar_font)
            ink = QColor(_ink_on(fill.name()))
            if item.done:
                ink.setAlpha(200)
            p.setPen(ink)
            tf = QFont(bar_font)
            tf.setStrikeOut(item.done)
            p.setFont(tf)
            text_rect = rect.adjusted(7, 0, -6, 0)
            label = fm.elidedText(item.title or "(untitled)", Qt.TextElideMode.ElideRight,
                                  int(text_rect.width()))
            p.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)

        # "+N more" overflow markers, in the reserved slot just below the last
        # drawn bar lane (so they never overlap a bar).
        if hidden:
            of = QFont(self.font())
            of.setPointSizeF(max(7.5, self.font().pointSizeF() - 2))
            of.setBold(True)
            p.setFont(of)
            p.setPen(QColor(theme.TEXT_MUTED))
            for (week, col), n in hidden.items():
                cell = self._cell_rect(week, col)
                y = cell.top() + self.DAY_NUM_H + draw_limit * pitch
                r = QRectF(cell.left() + 7, y, cell.width() - 12, self.LANE_H)
                p.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           f"+{n} more")

    def _draw_bar_shape(self, p: QPainter, rect: QRectF, fill: QColor,
                        cont_left: bool, cont_right: bool) -> None:
        """Rounded bar, with a pointed wing on any side that continues."""
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        r = 5.0
        path = QPainterPath()
        if not cont_left and not cont_right:
            path.addRoundedRect(rect, r, r)
        else:
            # Body as a plain rect; add a triangle wing on each continuing side.
            path.addRoundedRect(rect, r, r)
            mid = rect.center().y()
            wing = min(7.0, rect.height() * 0.5)
            if cont_left:
                tri = QPainterPath()
                tri.moveTo(rect.left(), rect.top())
                tri.lineTo(rect.left() - wing, mid)
                tri.lineTo(rect.left(), rect.bottom())
                tri.closeSubpath()
                path = path.united(tri)
            if cont_right:
                tri = QPainterPath()
                tri.moveTo(rect.right(), rect.top())
                tri.lineTo(rect.right() + wing, mid)
                tri.lineTo(rect.right(), rect.bottom())
                tri.closeSubpath()
                path = path.united(tri)
        p.drawPath(path)

    # ── interaction (click, plus drag to move / resize bars) ──
    def _compute_preview(self, drag: dict, day: date) -> tuple[date, date]:
        mode, s, e = drag["mode"], drag["orig_start"], drag["orig_end"]
        if mode == "resize_start":
            return (min(day, e), e)
        if mode == "resize_end":
            return (s, max(day, s))
        delta = (day - drag["press_day"]).days  # move: shift, keep duration
        return (s + timedelta(days=delta), e + timedelta(days=delta))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if event.button() == Qt.MouseButton.LeftButton:
            bar = self._bar_at(pos)
            if bar is not None:
                item = self._items[bar.item_index]
                rect = self._bar_rect(bar)
                mode = "move"
                if not bar.continues_left and pos.x() - rect.left() <= self.EDGE:
                    mode = "resize_start"
                elif not bar.continues_right and rect.right() - pos.x() <= self.EDGE:
                    mode = "resize_end"
                s = cal.parse_date(item.start)
                if s is not None:
                    self._drag = {
                        "item": item, "mode": mode, "press": pos,
                        "press_day": self._day_at(pos) or s,
                        "orig_start": s, "orig_end": cal.parse_date(item.end) or s,
                        "preview": None, "started": False,
                    }
                    if mode != "move":
                        self.setCursor(Qt.CursorShape.SizeHorCursor)
                    event.accept()
                    return
            day = self._day_at(pos)
            if day is not None:
                self._press_day = day
                event.accept()
                return
        elif event.button() == Qt.MouseButton.RightButton:
            bar = self._bar_at(pos)
            if bar is not None:
                self.item_context.emit(self._items[bar.item_index],
                                       event.globalPosition().toPoint())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            pos = event.position()
            if not self._drag["started"]:
                if (pos - self._drag["press"]).manhattanLength() < \
                        QApplication.startDragDistance():
                    return
                self._drag["started"] = True
            day = self._day_at(pos)
            if day is not None:
                self._drag["preview"] = self._compute_preview(self._drag, day)
                self.update()
            event.accept()
            return
        # Hover feedback: resize cursor near a bar's draggable end.
        if not self._drag:
            bar = self._bar_at(event.position())
            cur = Qt.CursorShape.ArrowCursor
            if bar is not None:
                rect = self._bar_rect(bar)
                x = event.position().x()
                if (not bar.continues_left and x - rect.left() <= self.EDGE) or \
                        (not bar.continues_right and rect.right() - x <= self.EDGE):
                    cur = Qt.CursorShape.SizeHorCursor
                else:
                    cur = Qt.CursorShape.PointingHandCursor
            self.setCursor(cur)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.unsetCursor()
        if self._drag is not None:
            drag, self._drag = self._drag, None
            if drag["started"] and drag["preview"] is not None:
                ns, ne = drag["preview"]
                self.item_rescheduled.emit(drag["item"], ns.isoformat(), ne.isoformat())
            else:
                self.item_activated.emit(drag["item"])  # no movement -> click
            self.update()
            event.accept()
            return
        if self._press_day is not None:
            day, self._press_day = self._press_day, None
            self.day_clicked.emit(day)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ── drag-and-drop from the Unscheduled tray ──
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(SCHEDULE_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(SCHEDULE_MIME):
            self._drop_day = self._day_at(event.position())
            self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._drop_day = None
        self.update()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(SCHEDULE_MIME):
            day = self._day_at(event.position())
            self._drop_day = None
            self.update()
            if day is not None:
                self.external_drop.emit(day)
            event.acceptProposedAction()


class _TrayChip(QWidget):
    """A draggable chip for one unscheduled item in the calendar's tray."""

    activated = Signal(object)            # left-click -> schedule it
    context = Signal(object, QPoint)      # right-click -> context menu

    def __init__(self, item, parent: QWidget | None = None):
        super().__init__(parent)
        self.item = item
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(28)
        # Fixed width (from sizeHint) so the row scrolls horizontally instead of
        # squeezing every chip into an unreadable sliver when there are many.
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip(f"{item.title}  ·  {item.home_name}  —  drag onto a day, or click")
        self._press_pos: QPoint | None = None
        self._view = None  # set by CalendarView so a drag can stash the item

    def _color(self) -> str:
        return getattr(theme, KIND_COLOR_KEY.get(self.item.kind, "ACCENT"), theme.ACCENT)

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self.item.title or "(untitled)")
        return QSize(min(220, w + 26), 28)

    def paintEvent(self, _event) -> None:
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            fill = QColor(self._color())
            if self.item.done:
                fill.setAlpha(110)
            r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, 6, 6)
            # color dot + title
            p.setBrush(QColor(_ink_on(fill.name())))
            ink = QColor(_ink_on(fill.name()))
            p.setPen(ink)
            f = QFont(self.font())
            f.setStrikeOut(self.item.done)
            p.setFont(f)
            fm = QFontMetrics(f)
            text_rect = r.adjusted(9, 0, -7, 0)
            label = fm.elidedText(self.item.title or "(untitled)",
                                  Qt.TextElideMode.ElideRight, int(text_rect.width()))
            p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       label)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self.context.emit(self.item, event.globalPosition().toPoint())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not (event.buttons() & Qt.MouseButton.LeftButton) or self._press_pos is None:
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < \
                QApplication.startDragDistance():
            return
        if self._view is not None:
            self._view._drag_item = self.item
        self._press_pos = None
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(SCHEDULE_MIME, b"1")
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(event.position().toPoint())
        drag.exec(Qt.DropAction.MoveAction)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            self._press_pos = None
            self.activated.emit(self.item)  # click without a drag -> open picker
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class CalendarView(QWidget):
    """The Calendar tab: month navigation + legend over a :class:`MonthGrid`,
    with an Unscheduled tray of items that don't yet have a date."""

    day_clicked = Signal(object)
    item_activated = Signal(object)
    item_context = Signal(object, QPoint)
    item_rescheduled = Signal(object, str, str)  # (item, start_iso, end_iso)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("calendarView")
        self._drag_item = None  # the tray item currently being dragged onto the grid
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(10)

        # Header: ‹  Month YYYY  ›   [Today]        • Projects • Playlists • Todos
        header = QHBoxLayout()
        header.setSpacing(8)
        self.prev_btn = QPushButton("‹")
        self.next_btn = QPushButton("›")
        for b in (self.prev_btn, self.next_btn):
            b.setObjectName("calNav")
            b.setFixedSize(32, 30)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_btn.clicked.connect(self._prev_month)
        self.next_btn.clicked.connect(self._next_month)

        self.title_label = QLabel("")
        self.title_label.setObjectName("calMonthTitle")
        self.title_label.setMinimumWidth(190)

        self.today_btn = QPushButton("Today")
        self.today_btn.setObjectName("calToday")
        self.today_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.today_btn.clicked.connect(self._go_today)

        header.addWidget(self.prev_btn)
        header.addWidget(self.title_label)
        header.addWidget(self.next_btn)
        header.addSpacing(8)
        header.addWidget(self.today_btn)
        header.addStretch(1)
        for kind, text in ((cal.KIND_PROJECT, "Projects"),
                           (cal.KIND_PLAYLIST, "Playlists"),
                           (cal.KIND_TODO, "Todos")):
            header.addWidget(_KindDot(kind))
            lbl = QLabel(text)
            lbl.setObjectName("calLegend")
            header.addWidget(lbl)
            header.addSpacing(6)
        root.addLayout(header)

        self.grid = MonthGrid()
        self.grid.day_clicked.connect(self.day_clicked.emit)
        self.grid.item_activated.connect(self.item_activated.emit)
        self.grid.item_context.connect(self.item_context.emit)
        self.grid.item_rescheduled.connect(self.item_rescheduled.emit)
        self.grid.external_drop.connect(self._on_external_drop)
        root.addWidget(self.grid, 1)

        self._build_tray(root)
        self._refresh_title()

    def _build_tray(self, root: QVBoxLayout) -> None:
        self._tray = QWidget()
        self._tray.setObjectName("calTray")
        # Guarantee the QSS panel background paints (plain QWidget otherwise
        # ignores background-color unless it's a styled-background widget).
        self._tray.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tv = QVBoxLayout(self._tray)
        tv.setContentsMargins(12, 8, 12, 10)
        tv.setSpacing(6)
        self._tray_label = QLabel("Unscheduled")
        self._tray_label.setObjectName("calTrayLabel")
        tv.addWidget(self._tray_label)

        self._tray_scroll = QScrollArea()
        self._tray_scroll.setObjectName("calTrayScroll")
        self._tray_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._tray_scroll.setWidgetResizable(True)
        self._tray_scroll.setFixedHeight(34)
        self._tray_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._tray_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The scroll area's viewport ignores the global sheet reliably; force it
        # transparent in code so the #calTray panel shows through.
        self._tray_scroll.viewport().setStyleSheet("background: transparent;")
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self._tray_row = QHBoxLayout(inner)
        self._tray_row.setContentsMargins(0, 0, 0, 0)
        self._tray_row.setSpacing(6)
        self._tray_row.addStretch(1)
        self._tray_scroll.setWidget(inner)
        tv.addWidget(self._tray_scroll)

        root.addWidget(self._tray)
        self._tray.setVisible(False)

    def set_items(self, items: list) -> None:
        scheduled = [it for it in items if it.scheduled]
        unscheduled = [it for it in items if not it.scheduled]
        self.grid.set_items(scheduled)
        self._populate_tray(unscheduled)

    # The tray aggregates unscheduled items across every tracked folder, so it
    # can be large on first use; render a bounded number and label the rest
    # rather than building hundreds of chips on every rescan.
    TRAY_LIMIT = 40

    def _populate_tray(self, unscheduled: list) -> None:
        # Drop existing chips/labels (everything before the trailing stretch).
        while self._tray_row.count() > 1:
            taken = self._tray_row.takeAt(0)
            w = taken.widget()
            if w is not None:
                w.deleteLater()
        for it in unscheduled[:self.TRAY_LIMIT]:
            chip = _TrayChip(it)
            chip._view = self
            chip.activated.connect(self.item_activated.emit)
            chip.context.connect(self.item_context.emit)
            self._tray_row.insertWidget(self._tray_row.count() - 1, chip)
        overflow = len(unscheduled) - self.TRAY_LIMIT
        if overflow > 0:
            more = QLabel(f"+{overflow} more")
            more.setObjectName("calTrayMore")
            self._tray_row.insertWidget(self._tray_row.count() - 1, more)
        self._tray_label.setText(f"Unscheduled · {len(unscheduled)}")
        self._tray.setVisible(bool(unscheduled))

    def _on_external_drop(self, day) -> None:
        item, self._drag_item = self._drag_item, None
        if item is not None:
            iso = day.isoformat()
            self.item_rescheduled.emit(item, iso, iso)

    def set_month(self, year: int, month: int) -> None:
        self.grid.set_month(year, month)
        self._refresh_title()

    def set_today(self, day: date) -> None:
        self.grid.set_today(day)

    def _refresh_title(self) -> None:
        y, m = self.grid.year_month()
        self.title_label.setText(f"{MONTH_NAMES[m]} {y}")

    def _prev_month(self) -> None:
        y, m = _shift_month(*self.grid.year_month(), -1)
        self.grid.set_month(y, m)
        self._refresh_title()

    def _next_month(self) -> None:
        y, m = _shift_month(*self.grid.year_month(), 1)
        self.grid.set_month(y, m)
        self._refresh_title()

    def _go_today(self) -> None:
        today = date.today()
        self.grid.set_today(today)
        self.grid.set_month(today.year, today.month)
        self._refresh_title()


class ScheduleDialog(QWidget):
    """Frameless popup to set or clear an item's inclusive start/end dates.

    Emits :attr:`scheduled` with ISO ``(start, end)`` on Apply (end clamped to
    ≥ start), or ``("", "")`` on Unschedule. Cancel/Esc dismiss without emitting.
    """

    scheduled = Signal(str, str)

    def __init__(self, title: str, start_iso: str, end_iso: str,
                 parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        from PySide6.QtWidgets import QDateEdit
        from PySide6.QtCore import QDate, QLocale
        # Keep dates English to match the rest of the UI (weekday/month names).
        en_locale = QLocale(QLocale.Language.English)
        self.setObjectName("scheduleDialog")
        self.setMinimumWidth(300)
        self._was_scheduled = bool(start_iso)

        def to_qdate(iso: str) -> "QDate":
            d = QDate.fromString(iso, Qt.DateFormat.ISODate)
            return d if d.isValid() else QDate.currentDate()

        start_q = to_qdate(start_iso)
        end_q = to_qdate(end_iso or start_iso)

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(14)

        head = QLabel("Schedule")
        head.setObjectName("scheduleTitle")
        v.addWidget(head)
        sub = QLabel(title)
        sub.setObjectName("scheduleSub")
        sub.setWordWrap(True)
        v.addWidget(sub)

        def field(label_text: str, qdate: "QDate") -> "QDateEdit":
            row = QHBoxLayout()
            row.setSpacing(10)
            lbl = QLabel(label_text)
            lbl.setObjectName("scheduleFieldLabel")
            lbl.setFixedWidth(48)
            row.addWidget(lbl)
            edit = QDateEdit()
            edit.setObjectName("scheduleDate")
            edit.setLocale(en_locale)
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("ddd, MMM d yyyy")
            edit.setDate(qdate)
            cal_popup = edit.calendarWidget()
            if cal_popup is not None:
                cal_popup.setLocale(en_locale)
            row.addWidget(edit, 1)
            v.addLayout(row)
            return edit

        self.start_edit = field("Start", start_q)
        self.end_edit = field("End", end_q)

        hint = QLabel("End date is inclusive.")
        hint.setObjectName("scheduleHint")
        v.addWidget(hint)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.unschedule_btn = QPushButton("Unschedule")
        self.unschedule_btn.setObjectName("scheduleClear")
        self.unschedule_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unschedule_btn.setEnabled(self._was_scheduled)
        self.unschedule_btn.clicked.connect(self._unschedule)
        btns.addWidget(self.unschedule_btn)
        btns.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("scheduleCancel")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.close)
        btns.addWidget(cancel)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.setDefault(True)
        self.apply_btn.clicked.connect(self._apply)
        btns.addWidget(self.apply_btn)
        v.addLayout(btns)

    def _apply(self) -> None:
        s = self.start_edit.date()
        e = self.end_edit.date()
        if e < s:
            e = s
        fmt = Qt.DateFormat.ISODate
        self.scheduled.emit(s.toString(fmt), e.toString(fmt))
        self.close()

    def _unschedule(self) -> None:
        self.scheduled.emit("", "")
        self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
