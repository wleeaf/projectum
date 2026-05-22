"""Custom-painted widgets for Projectum."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import (
    Qt, QRect, QRectF, QPointF, QPoint, Property, Signal, QObject,
    QRunnable, QSize,
)
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QPen, QFont, QPainterPath, QMouseEvent,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel,
    QLayout, QLineEdit, QPushButton, QSizePolicy,
)

from .anims import fade_window_close
from . import theme
from .theme import TAG_PALETTE, tag_color


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
            fill = QColor(base)
            fill.setAlpha(46)
            border = QColor(base)
            border.setAlpha(140)

            rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
            p.setBrush(QBrush(fill))
            p.setPen(QPen(border, 1))
            radius = rect.height() / 2
            p.drawRoundedRect(rect, radius, radius)

            p.setPen(base)
            p.setFont(self.font())
            text_rect = QRectF(rect)
            if self._removable:
                # Reserve the right portion of the chip for the × so the
                # text stays centered in the left part.
                text_rect.setRight(text_rect.right() - self.REMOVE_BOX)
            p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.tag)

            if self._removable:
                self._paint_remove_glyph(p, base)

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
            for t in self.project.tags[:3]:
                chip = TagChip(t, tag_color(t, self.store.tag_colors))
                chip.setToolTip(f"#{t} · right-click to change color")
                chip.right_clicked.connect(
                    lambda tag=t: self.tag_right_clicked.emit(tag)
                )
                self._tag_layout.addWidget(chip)
            if len(self.project.tags) > 3:
                more = QLabel(f"+{len(self.project.tags) - 3}")
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

    def __init__(
        self,
        current_theme: str,
        current_font_family: str,
        current_font_size: int,
        parent: QWidget | None = None,
    ):
        super().__init__(
            parent,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint,
        )
        self.setObjectName("settingsDialog")
        self.setMinimumWidth(420)
        self._suppress = True
        self._build(current_theme, current_font_family, current_font_size)
        self._suppress = False

    def _build(
        self,
        current_theme: str,
        current_font_family: str,
        current_font_size: int,
    ) -> None:
        from PySide6.QtWidgets import QComboBox, QFrame, QSpinBox
        from PySide6.QtGui import QFontDatabase
        from .theme import (
            DEFAULT_FONT_FAMILY, FONT_SIZE_MAX, FONT_SIZE_MIN, THEME_LABELS,
        )

        v = QVBoxLayout(self)
        v.setContentsMargins(28, 22, 28, 22)
        v.setSpacing(18)

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

        # Theme
        v.addLayout(self._field_row(
            "Theme",
            "Color palette used across the app.",
        ))
        self.theme_combo = QComboBox()
        self.theme_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_keys: list[str] = []
        for key, label in THEME_LABELS:
            self.theme_combo.addItem(label)
            self._theme_keys.append(key)
        if current_theme in self._theme_keys:
            self.theme_combo.setCurrentIndex(self._theme_keys.index(current_theme))
        self.theme_combo.currentIndexChanged.connect(self._emit_change)
        v.addWidget(self.theme_combo)

        # Font family — populated from QFontDatabase so any installed family works.
        v.addLayout(self._field_row(
            "Font family",
            "Choose any installed font. Type to filter.",
        ))
        self.font_combo = QComboBox()
        self.font_combo.setEditable(True)
        self.font_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.font_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        families = sorted(set(QFontDatabase.families()), key=str.casefold)
        self.font_combo.addItems(families)
        # Prefer the user's previous choice; otherwise the default if present.
        for candidate in (current_font_family, DEFAULT_FONT_FAMILY):
            idx = self.font_combo.findText(candidate, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self.font_combo.setCurrentIndex(idx)
                break
        self.font_combo.currentIndexChanged.connect(self._emit_change)
        # editingFinished fires when user types a new family name and tabs/Enters.
        self.font_combo.lineEdit().editingFinished.connect(self._emit_change)
        v.addWidget(self.font_combo)

        # Font size — free-form spin box.
        v.addLayout(self._field_row(
            "Font size",
            f"Base text size in pixels ({FONT_SIZE_MIN}–{FONT_SIZE_MAX}).",
        ))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(FONT_SIZE_MIN, FONT_SIZE_MAX)
        self.size_spin.setSuffix(" px")
        self.size_spin.setValue(int(current_font_size))
        self.size_spin.setCursor(Qt.CursorShape.PointingHandCursor)
        self.size_spin.valueChanged.connect(self._emit_change)
        v.addWidget(self.size_spin)

        v.addStretch(1)

        hint = QLabel("Changes apply immediately and persist across launches.")
        hint.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px; background: transparent;"
        )
        v.addWidget(hint)

    def _field_row(self, title: str, subtitle: str) -> QVBoxLayout:
        wrap = QVBoxLayout()
        wrap.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet(
            f"color: {theme.TEXT}; font-size: 13px; font-weight: 600; "
            f"background: transparent;"
        )
        s = QLabel(subtitle)
        s.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px; background: transparent;"
        )
        wrap.addWidget(t)
        wrap.addWidget(s)
        return wrap

    def _emit_change(self, *_args) -> None:
        if self._suppress:
            return
        theme_idx = self.theme_combo.currentIndex()
        theme_key = (
            self._theme_keys[theme_idx]
            if 0 <= theme_idx < len(self._theme_keys)
            else "dark"
        )
        family = self.font_combo.currentText().strip() or "Inter"
        size = int(self.size_spin.value())
        self.settings_changed.emit({
            "theme": theme_key,
            "font_family": family,
            "font_size": size,
        })

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


# ──────────────── Markdown preview helper ────────────────


def make_markdown_pane(editor):
    """Wrap a notes editor with an Edit/Preview toggle button + stacked preview.

    Returns ``(toggle_button, stack, preview)``. The caller places the toggle
    in a header row and adds the stack to the main layout in the editor's
    former position. Preview uses Qt's built-in ``QTextDocument.setMarkdown``
    and re-syncs on every editor text change while preview mode is active.
    """
    from PySide6.QtWidgets import QStackedWidget, QTextBrowser
    preview = QTextBrowser()
    preview.setObjectName("notes")
    preview.setOpenExternalLinks(True)
    stack = QStackedWidget()
    stack.addWidget(editor)
    stack.addWidget(preview)
    toggle = QPushButton("Preview")
    toggle.setObjectName("ghost")
    toggle.setCheckable(True)
    toggle.setCursor(Qt.CursorShape.PointingHandCursor)
    toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _sync_preview():
        if toggle.isChecked():
            preview.setMarkdown(editor.toPlainText())

    def _on_toggle(checked):
        if checked:
            preview.setMarkdown(editor.toPlainText())
            stack.setCurrentWidget(preview)
            toggle.setText("Edit")
        else:
            stack.setCurrentWidget(editor)
            toggle.setText("Preview")

    toggle.clicked.connect(_on_toggle)
    editor.textChanged.connect(_sync_preview)
    return toggle, stack, preview


# ──────────────── Playlists ────────────────


def _format_duration(seconds: int | None) -> str:
    if not seconds or seconds <= 0:
        return ""
    seconds = int(seconds)
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
            for t in self.playlist.tags[:3]:
                chip = TagChip(t, tag_color(t, self.store.tag_colors))
                chip.setToolTip(f"#{t} · right-click to change color")
                chip.right_clicked.connect(
                    lambda tag=t: self.tag_right_clicked.emit(tag)
                )
                self._tag_layout.addWidget(chip)
            if len(self.playlist.tags) > 3:
                more = QLabel(f"+{len(self.playlist.tags) - 3}")
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
