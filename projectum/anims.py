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

import math
import os

from PySide6.QtCore import (
    QEasingCurve, QElapsedTimer, QEvent, QObject, QPropertyAnimation, QSize, Qt,
    QTimer, QVariantAnimation,
)
from PySide6.QtWidgets import (
    QAbstractScrollArea, QApplication, QGraphicsOpacityEffect, QLabel,
    QListWidget, QListWidgetItem, QProgressBar, QStackedWidget, QWidget,
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
    if not isinstance(anim, QPropertyAnimation):
        return
    try:
        if anim.state() != QPropertyAnimation.State.Stopped:
            try:
                anim.finished.disconnect()
            except (TypeError, RuntimeError):
                pass
            anim.stop()
        # The animation is parented to the widget, so simply dropping our
        # reference wouldn't free it — without this, every fade/slide leaks a
        # QPropertyAnimation as a permanent child of the widget.
        anim.deleteLater()
    except RuntimeError:
        pass
    widget._anim = None


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


class _OverlayResizer(QObject):
    """Keeps a cross-fade overlay matched to its stack's size during the fade.

    The overlay is an absolutely-positioned child (not in a layout), so without
    this it keeps its initial geometry and misaligns if the window resizes
    mid-fade.
    """

    def __init__(self, overlay: QWidget, stack: QStackedWidget):
        super().__init__(stack)
        self._overlay = overlay

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            try:
                self._overlay.setGeometry(0, 0, obj.width(), obj.height())
            except RuntimeError:
                pass
        return False


def cross_fade_stack(
    stack: QStackedWidget,
    new_index: int,
    duration: int = 160,
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

    # Track stack resizes so the snapshot stays aligned during the fade.
    resizer = _OverlayResizer(overlay, stack)
    stack.installEventFilter(resizer)

    eff = QGraphicsOpacityEffect(overlay)
    eff.setOpacity(1.0)
    overlay.setGraphicsEffect(eff)

    anim = QPropertyAnimation(eff, b"opacity", overlay)
    anim.setDuration(duration)
    anim.setStartValue(1.0)
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _cleanup():
        try:
            stack.removeEventFilter(resizer)
        except RuntimeError:
            pass
        resizer.deleteLater()
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
    if isinstance(existing, QVariantAnimation):
        try:
            if existing.state() != QVariantAnimation.State.Stopped:
                existing.stop()
            # Parented to the bar — free it so calls don't accumulate
            # animations as permanent children.
            existing.deleteLater()
        except RuntimeError:
            pass
        bar._progress_anim = None
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
    """Glide a ``QAbstractScrollArea`` toward an accumulating target on the
    mouse wheel.

    Each wheel notch extends a single ``_target_value``; a frame timer eases
    the scrollbar toward it with **frame-rate-independent** damping. Because
    the motion is one continuous lerp (not a fresh ease-out per notch), fast
    wheel spins glide smoothly instead of stuttering as each notch restarts a
    deceleration from a standstill.

    Trackpad / high-precision wheels (which carry a ``pixelDelta``) are left to
    native pixel scrolling — already smooth, lower-latency, and momentum-aware.

    Tunables: :attr:`PIXELS_PER_NOTCH` (distance per notch) and :attr:`TAU_MS`
    (easing time constant — smaller is snappier, larger is glidier).
    """

    PIXELS_PER_NOTCH = 110
    TAU_MS = 90.0

    def __init__(self, target: QAbstractScrollArea):
        super().__init__(target)
        self._target = target
        self._sb = target.verticalScrollBar()
        self._target_value = float(self._sb.value())
        self._driving = False  # True while WE are setting the scrollbar value
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        # PreciseTimer (not the default CoarseTimer) so the cadence actually
        # tracks the display refresh instead of being snapped to coarse ticks.
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        # Interval is (re)computed from the live screen when a glide starts —
        # see eventFilter. The value here is just a sane initial guess; reading
        # the refresh rate now (in __init__, before the window is shown) tends
        # to report the primary screen's 60 Hz even on a 120 Hz panel.
        self._timer.setInterval(self._frame_interval_ms())
        self._timer.timeout.connect(self._tick)
        # Any scrollbar change WE didn't cause (keyboard, drag, programmatic,
        # model reload) re-anchors the target, so the next notch composes
        # against the real value rather than a stale one.
        self._sb.valueChanged.connect(self._on_external_change)

    def _frame_interval_ms(self) -> int:
        # Escape hatch: if Qt misreports the panel's refresh rate (some Linux
        # setups report 60 Hz for a 120 Hz display), PROJECTUM_SCROLL_FPS forces
        # the cadence, e.g. PROJECTUM_SCROLL_FPS=120.
        override = os.environ.get("PROJECTUM_SCROLL_FPS")
        if override:
            try:
                fps = float(override)
                if fps > 0:
                    return max(4, min(33, int(round(1000.0 / fps))))
            except ValueError:
                pass
        # Prefer the screen the window is ACTUALLY shown on (accurate once the
        # window is mapped), then the widget's screen, then the primary screen.
        screen = None
        win = self._target.window() if hasattr(self._target, "window") else None
        handle = win.windowHandle() if win is not None else None
        if handle is not None:
            screen = handle.screen()
        if screen is None and hasattr(self._target, "screen"):
            screen = self._target.screen()
        if screen is None:
            app = QApplication.instance()
            screen = app.primaryScreen() if app is not None else None
        rate = screen.refreshRate() if screen is not None else 0.0
        if rate <= 0:
            rate = 60.0
        return max(6, min(16, int(round(1000.0 / rate))))

    @classmethod
    def install(cls, view: QAbstractScrollArea) -> "SmoothScrollFilter":
        f = cls(view)
        view.viewport().installEventFilter(f)
        return f

    def _set_value(self, value: int) -> None:
        # Guard so the resulting valueChanged isn't mistaken for an external
        # change (which would void our target mid-glide).
        self._driving = True
        self._sb.setValue(value)
        self._driving = False

    def _on_external_change(self, v: int) -> None:
        if not self._driving:
            self._target_value = float(v)

    def _tick(self) -> None:
        sb = self._sb
        # Re-clamp every frame: a rebuild may have shrunk the range mid-glide.
        self._target_value = max(
            sb.minimum(), min(sb.maximum(), self._target_value)
        )
        cur = sb.value()
        diff = self._target_value - cur
        if abs(diff) < 0.5:
            self._set_value(int(round(self._target_value)))
            self._timer.stop()
            return
        dt = self._clock.restart()
        # Frame-rate-independent exponential damping toward the target.
        alpha = 1.0 - math.exp(-dt / self.TAU_MS) if dt > 0 else 0.5
        step = diff * alpha
        if -1.0 < step < 1.0:  # guarantee ≥1px progress so it never crawls
            step = 1.0 if diff > 0 else -1.0
        self._set_value(int(round(cur + step)))

    def eventFilter(self, _obj, event):
        if event.type() != QEvent.Type.Wheel:
            return False
        # Don't take over modifier-wheel (Ctrl = zoom in most apps).
        if event.modifiers() != Qt.KeyboardModifier.NoModifier:
            return False
        # Trackpad / high-precision wheels → native pixel scrolling.
        if not event.pixelDelta().isNull():
            return False
        delta_y = event.angleDelta().y()
        if delta_y == 0:
            return False

        sb = self._sb
        if not self._timer.isActive():
            self._target_value = float(sb.value())  # re-anchor when idle
        notches = delta_y / 120.0
        new_target = max(
            sb.minimum(),
            min(sb.maximum(), self._target_value - notches * self.PIXELS_PER_NOTCH),
        )
        if int(round(new_target)) == sb.value() and not self._timer.isActive():
            # Already at the bound — let the event propagate (e.g. a parent
            # scroll area can take over).
            return False
        self._target_value = new_target
        if not self._timer.isActive():
            # Recompute the cadence from the screen the window is currently on
            # (now that it's shown) so a 120 Hz panel actually gets ~8 ms ticks
            # rather than the 60 Hz value read before the window existed.
            self._timer.setInterval(self._frame_interval_ms())
            self._clock.restart()
            self._timer.start()
        return True
