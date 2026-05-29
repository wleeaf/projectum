"""Animation helpers for Projectum.

Strategy
========

PySide6's ``QGraphicsOpacityEffect`` is the obvious tool for fading a
QWidget, but it does not work on widget trees that contain custom-painted
children (anything with ``def paintEvent: p = QPainter(self)``). The
effect's offscreen-pixmap render fights the child paint events and produces
broken frames mid-fade — the classic symptom is the new page being invisible
for most of the animation and then snapping in at the end. We rule that out
by routing different transitions through different paths:

* ``cross_fade_stack`` — for ``QStackedWidget`` swaps. Snapshots the old
  page into a ``QPixmap``, switches the stack, and overlays the pixmap as a
  ``QLabel`` that fades out. The label has no custom-painted children, so
  ``QGraphicsOpacityEffect`` on it works cleanly. The new page is shown at
  full opacity from frame 1 — no render pipeline conflict.

* ``slide_in_height`` / ``slide_out_height`` — for sections that
  appear/disappear within a vertical layout (e.g. the tag palette). Animates
  ``maximumHeight``. Layout reflows as the value changes, giving a clean
  expand/collapse with no opacity effect involved.

* ``collapse_list_item`` — animates a ``QListWidgetItem``'s ``sizeHint``
  height down to 0 for a satisfying delete.

* ``fade_in`` / ``fade_out`` — kept for *simple* widgets only (QLineEdit,
  QPushButton, QLabel). Uses ``QGraphicsOpacityEffect``. Safe when there
  are no custom-painted descendants.

* ``fade_window`` / ``fade_window_close`` — for top-level frameless popups.
  Uses ``windowOpacity`` (window-compositor path), which doesn't go through
  the offscreen-pixmap render and is immune to the child-paintEvent issue.

* ``animate_progress`` — tweens ``QProgressBar.value`` via QVariantAnimation.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import (
    QEasingCurve, QEvent, QObject, QPropertyAnimation, QSize, Qt, QTimer,
    QVariantAnimation,
)
from PySide6.QtWidgets import (
    QAbstractScrollArea, QGraphicsOpacityEffect, QLabel, QListWidget,
    QListWidgetItem, QProgressBar, QStackedWidget, QWidget,
)


# QWIDGETSIZE_MAX is the value Qt uses to mean "no maximum". Restored after
# a slide so the widget can resize naturally again.
_QWIDGETSIZE_MAX = 16777215


# ──────────────────────── shared internals ────────────────────────


def _effect_for(widget: QWidget) -> QGraphicsOpacityEffect:
    eff = widget.graphicsEffect()
    if isinstance(eff, QGraphicsOpacityEffect):
        return eff
    eff = QGraphicsOpacityEffect(widget)
    eff.setOpacity(1.0)
    widget.setGraphicsEffect(eff)
    return eff


def _stop_anim(widget: QWidget) -> None:
    anim = getattr(widget, "_anim", None)
    if isinstance(anim, QPropertyAnimation) and anim.state() != QPropertyAnimation.State.Stopped:
        try:
            anim.finished.disconnect()
        except (TypeError, RuntimeError):
            pass
        anim.stop()


# ──────────────────────── fades for SIMPLE widgets ────────────────────────


def fade_in(widget: QWidget, duration: int = 180) -> QPropertyAnimation:
    """Fade a widget from 0 opacity up to 1.

    Safe only for widgets whose subtree contains no custom paintEvents
    (plain QLabel/QPushButton/QLineEdit etc). For complex widgets, use
    :func:`cross_fade_stack` or :func:`slide_in_height`.
    """
    _stop_anim(widget)
    eff = _effect_for(widget)
    eff.setOpacity(0.0)
    widget.update()
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.valueChanged.connect(lambda _v, w=widget: w.update())
    widget._anim = anim
    anim.start()
    return anim


def fade_out(
    widget: QWidget,
    duration: int = 180,
    on_done: Callable[[], None] | None = None,
) -> QPropertyAnimation:
    """Fade a SIMPLE widget down to 0. Same custom-paint caveat as fade_in."""
    _stop_anim(widget)
    eff = _effect_for(widget)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(eff.opacity())
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.InCubic)
    anim.valueChanged.connect(lambda _v, w=widget: w.update())
    if on_done is not None:
        anim.finished.connect(on_done)
    widget._anim = anim
    anim.start()
    return anim


# ──────────────────────── cross-fade for complex stacks ────────────────────────


def cross_fade_stack(
    stack: QStackedWidget,
    new_index: int,
    duration: int = 200,
) -> None:
    """Crossfade ``stack`` to ``new_index`` via a pixmap-overlay snapshot.

    The destination page becomes visible at full opacity immediately — only
    the snapshot of the old page (a plain QLabel) is faded, so the painter
    pipeline stays clean even when both pages contain custom-painted
    children.
    """
    if stack.currentIndex() == new_index:
        return
    new_widget = stack.widget(new_index)
    if new_widget is None:
        stack.setCurrentIndex(new_index)
        return

    old_widget = stack.currentWidget()
    if (
        old_widget is None
        or not old_widget.isVisible()
        or stack.width() <= 0
        or stack.height() <= 0
    ):
        stack.setCurrentIndex(new_index)
        return

    # Tear down any in-flight overlay from a previous rapid click —
    # otherwise multiple QLabel snapshots stack and fade independently.
    previous = getattr(stack, "_cross_fade_overlay", None)
    if previous is not None:
        try:
            previous.deleteLater()
        except RuntimeError:
            pass

    pixmap = old_widget.grab()
    stack.setCurrentIndex(new_index)

    overlay = QLabel(stack)
    overlay.setPixmap(pixmap)
    overlay.setGeometry(0, 0, stack.width(), stack.height())
    overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    overlay.raise_()
    overlay.show()
    stack._cross_fade_overlay = overlay

    eff = QGraphicsOpacityEffect(overlay)
    eff.setOpacity(1.0)
    overlay.setGraphicsEffect(eff)

    anim = QPropertyAnimation(eff, b"opacity", overlay)
    anim.setDuration(duration)
    anim.setStartValue(1.0)
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _cleanup():
        if getattr(stack, "_cross_fade_overlay", None) is overlay:
            stack._cross_fade_overlay = None
        overlay.deleteLater()
    anim.finished.connect(_cleanup)
    overlay._anim = anim
    anim.start()


# ──────────────────────── slide for height-collapsible sections ────────────────────────


def slide_in_height(widget: QWidget, duration: int = 200) -> QPropertyAnimation:
    """Reveal ``widget`` by animating its maxHeight from 0 to its natural height.

    No opacity effect involved, so custom-painted children render normally
    throughout the animation.
    """
    _stop_anim(widget)
    widget.setVisible(True)
    # sizeHint can underreport when the widget hasn't been laid out; ensure
    # the layout has run so target_h is meaningful.
    widget.adjustSize()
    target_h = max(widget.sizeHint().height(), widget.minimumSizeHint().height())
    if target_h <= 0:
        widget.setMaximumHeight(_QWIDGETSIZE_MAX)
        return None  # type: ignore[return-value]
    widget.setMaximumHeight(0)
    anim = QPropertyAnimation(widget, b"maximumHeight", widget)
    anim.setDuration(duration)
    anim.setStartValue(0)
    anim.setEndValue(target_h)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.finished.connect(lambda: widget.setMaximumHeight(_QWIDGETSIZE_MAX))
    widget._anim = anim
    anim.start()
    return anim


def slide_out_height(
    widget: QWidget,
    duration: int = 180,
    on_done: Callable[[], None] | None = None,
) -> QPropertyAnimation:
    """Collapse ``widget`` by animating its maxHeight to 0, then hide."""
    _stop_anim(widget)
    current_h = widget.height()
    if current_h <= 0:
        widget.setVisible(False)
        if on_done is not None:
            on_done()
        return None  # type: ignore[return-value]
    anim = QPropertyAnimation(widget, b"maximumHeight", widget)
    anim.setDuration(duration)
    anim.setStartValue(current_h)
    anim.setEndValue(0)
    anim.setEasingCurve(QEasingCurve.Type.InCubic)

    def _finish():
        widget.setVisible(False)
        widget.setMaximumHeight(_QWIDGETSIZE_MAX)
        if on_done is not None:
            on_done()
    anim.finished.connect(_finish)
    widget._anim = anim
    anim.start()
    return anim


# ──────────────────────── list-item collapse for delete ────────────────────────


def collapse_list_item(
    list_widget: QListWidget,
    item: QListWidgetItem,
    duration: int = 200,
    on_done: Callable[[], None] | None = None,
) -> QVariantAnimation | None:
    """Animate a list item's height down to 0 (other items shift up smoothly)."""
    start_h = item.sizeHint().height()
    if start_h <= 0:
        if on_done is not None:
            on_done()
        return None
    anim = QVariantAnimation(list_widget)
    anim.setDuration(duration)
    anim.setStartValue(start_h)
    anim.setEndValue(0)
    anim.setEasingCurve(QEasingCurve.Type.InCubic)

    def _tick(h, it=item):
        it.setSizeHint(QSize(0, max(1, int(h))))
    anim.valueChanged.connect(_tick)
    if on_done is not None:
        anim.finished.connect(on_done)
    list_widget._collapse_anim = anim  # keep alive — anims aren't strong-refed
    anim.start()
    return anim


# ──────────────────────── window-opacity for top-level popups ────────────────────────


def fade_window(window: QWidget, target: float, duration: int = 140) -> QPropertyAnimation:
    """Animate ``setWindowOpacity`` — for frameless popups (Qt.Popup etc).

    Uses the window-compositor path, not the offscreen-pixmap render, so it
    is safe regardless of the popup's painted contents.
    """
    _stop_anim(window)
    anim = QPropertyAnimation(window, b"windowOpacity", window)
    anim.setDuration(duration)
    anim.setStartValue(window.windowOpacity())
    anim.setEndValue(target)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    window._anim = anim
    anim.start()
    return anim


def fade_window_close(
    window: QWidget,
    duration: int = 120,
    on_done: Callable[[], None] | None = None,
) -> QPropertyAnimation:
    """Fade a window's opacity to 0 and then close it (or run ``on_done``)."""
    _stop_anim(window)
    anim = QPropertyAnimation(window, b"windowOpacity", window)
    anim.setDuration(duration)
    anim.setStartValue(window.windowOpacity())
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.InCubic)

    def _finish():
        if on_done is not None:
            on_done()
        else:
            window.close()
    anim.finished.connect(_finish)
    window._anim = anim
    anim.start()
    return anim


# ──────────────────────── progress bar ────────────────────────


def animate_progress(
    bar: QProgressBar, target: int, duration: int = 280
) -> QVariantAnimation | None:
    """Smoothly tween a QProgressBar's value to ``target``."""
    existing = getattr(bar, "_progress_anim", None)
    if isinstance(existing, QVariantAnimation) and existing.state() != QVariantAnimation.State.Stopped:
        existing.stop()
    start = bar.value()
    if start == target:
        return None
    anim = QVariantAnimation(bar)
    anim.setDuration(duration)
    anim.setStartValue(start)
    anim.setEndValue(target)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.valueChanged.connect(lambda v: bar.setValue(int(v)))
    bar._progress_anim = anim
    anim.start()
    return anim


# ──────────────────────── smooth wheel scrolling ────────────────────────


class SmoothScrollFilter(QObject):
    """Animate ``QAbstractScrollArea`` wheel scrolling.

    Tracks an explicit target value so successive wheel notches compose
    (each one extends the target), and resets that target whenever the
    scrollbar's value changes for any reason other than our own animation
    — this is what prevents the "scroll stops working after a while"
    failure mode where the cached target drifts out of sync with the
    actual scrollbar (e.g. after `setCurrentRow`, model reload, or
    the user grabs the scrollbar by hand) and we end up animating to a
    no-op delta on every event.
    """

    DURATION_MS = 220
    PIXELS_PER_NOTCH = 120

    def __init__(self, target: QAbstractScrollArea):
        super().__init__(target)
        self._target = target
        self._sb = target.verticalScrollBar()
        self._anim = QPropertyAnimation(self._sb, b"value", self)
        self._anim.setDuration(self.DURATION_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._end_target: int | None = None
        self._animating = False
        self._anim.finished.connect(self._on_finished)
        # External scroll-bar changes (keyboard, drag, programmatic) must
        # void our cached end target — otherwise the next wheel event
        # tries to compose against a stale value and may resolve to a
        # no-op delta.
        self._sb.valueChanged.connect(self._on_sb_value_changed)
        # 1.5s of wheel idleness clears any state too, as a backstop.
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(1500)
        self._idle.timeout.connect(self._reset)

    @classmethod
    def install(cls, view: QAbstractScrollArea) -> "SmoothScrollFilter":
        f = cls(view)
        view.viewport().installEventFilter(f)
        return f

    def _on_finished(self) -> None:
        self._animating = False
        self._end_target = None

    def _on_sb_value_changed(self, _v: int) -> None:
        if not self._animating:
            self._end_target = None

    def _reset(self) -> None:
        if self._anim.state() == QPropertyAnimation.State.Running:
            self._anim.stop()
        self._animating = False
        self._end_target = None

    def eventFilter(self, _obj, event):
        if event.type() != QEvent.Type.Wheel:
            return False
        # Don't take over modifier-wheel (Ctrl=zoom in most apps).
        if event.modifiers() != Qt.KeyboardModifier.NoModifier:
            return False
        delta_y = event.angleDelta().y()
        if delta_y == 0:
            return False

        notches = delta_y / 120.0
        delta_px = -int(notches * self.PIXELS_PER_NOTCH)

        # Compose against the in-flight end target if we're still animating,
        # otherwise re-anchor on the scrollbar's current value.
        if (self._animating
                and self._end_target is not None
                and self._anim.state() == QPropertyAnimation.State.Running):
            base = self._end_target
        else:
            base = self._sb.value()

        new_target = max(
            self._sb.minimum(),
            min(self._sb.maximum(), base + delta_px),
        )
        if new_target == self._sb.value():
            # Nothing to scroll to — let Qt handle the event (e.g. parent
            # area might take over) so we don't silently swallow it.
            return False

        self._end_target = new_target
        self._animating = True
        self._anim.stop()
        self._anim.setStartValue(self._sb.value())
        self._anim.setEndValue(new_target)
        self._anim.start()
        self._idle.start()
        return True
