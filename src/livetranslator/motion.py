"""Animation helpers: ripples, entrances, and pressable surfaces."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QPushButton,
    QWidget,
)

EASE_OUT = QEasingCurve.Type.OutCubic
EASE_IN_OUT = QEasingCurve.Type.InOutCubic


class Ripple(QObject):
    """A circle that grows and fades from the point of a click."""

    changed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._radius = 0.0
        self._opacity = 0.0
        self._origin = QPoint()
        self._host = parent

        self._grow = QPropertyAnimation(self, b"radius", self)
        self._grow.setEasingCurve(EASE_OUT)
        self._fade = QPropertyAnimation(self, b"opacity", self)
        self._fade.setEasingCurve(QEasingCurve.Type.OutQuad)

    def _get_radius(self) -> float:
        return self._radius

    def _set_radius(self, value: float) -> None:
        self._radius = value
        self._host.update()

    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, value: float) -> None:
        self._opacity = value
        self._host.update()

    radius = Property(float, _get_radius, _set_radius)
    opacity = Property(float, _get_opacity, _set_opacity)

    @property
    def origin(self) -> QPoint:
        return self._origin

    def start(self, origin: QPoint, *, peak: float = 0.22,
              duration: int = 420) -> None:
        self._origin = origin
        rect = self._host.rect()
        # Reach the furthest corner so the ripple always covers the whole surface
        far = max(
            (origin - rect.topLeft()).manhattanLength(),
            (origin - rect.topRight()).manhattanLength(),
            (origin - rect.bottomLeft()).manhattanLength(),
            (origin - rect.bottomRight()).manhattanLength(),
        )

        self._grow.stop()
        self._fade.stop()
        self._grow.setDuration(duration)
        self._grow.setStartValue(6.0)
        self._grow.setEndValue(float(far))
        self._fade.setDuration(duration + 40)
        self._fade.setStartValue(peak)
        self._fade.setEndValue(0.0)
        self._grow.start()
        self._fade.start()

    def paint(self, painter: QPainter, radius: int) -> None:
        if self._opacity <= 0.001 or self._radius <= 0.0:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        clip = QPainterPath()
        clip.addRoundedRect(self._host.rect(), radius, radius)
        painter.setClipPath(clip)
        colour = QColor(255, 255, 255)
        colour.setAlphaF(self._opacity)
        painter.setBrush(colour)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(self._origin, int(self._radius), int(self._radius))
        painter.restore()


class _RippleMixin:
    """Shared click/hover behaviour for anything the user can press."""

    def _init_ripple(self, radius: int, sounds: SoundCues | None) -> None:
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        self._radius = radius
        self._cues = sounds
        self._ripple = Ripple(self)
        self._hover_ripple = Ripple(self)

    def _press_ripple(self, event: QMouseEvent) -> None:
        self._ripple.start(event.position().toPoint())
        if self._cues is not None:
            self._cues.click()

    def enterEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        self._hover_ripple.start(self.rect().center(), peak=0.09, duration=320)
        if self._cues is not None:
            self._cues.hover()
        super().enterEvent(event)

    def _paint_ripples(self) -> None:
        painter = QPainter(self)
        self._hover_ripple.paint(painter, self._radius)
        self._ripple.paint(painter, self._radius)


class SoundCues:
    """The two cues a pressable surface needs, so widgets never import the board."""

    def __init__(self, hover: Callable[[], None], click: Callable[[], None]) -> None:
        self.hover = hover
        self.click = click


class PressableFrame(_RippleMixin, QFrame):
    """A frame that ripples on click, reports the click, and plays cues."""

    clicked = Signal()

    def __init__(self, *, radius: int = 18, sounds: SoundCues | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_ripple(radius, sounds)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._press_ripple(event)
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        super().paintEvent(event)
        self._paint_ripples()


class PressableButton(_RippleMixin, QPushButton):
    """A QPushButton with the same ripple and cue behaviour as PressableFrame."""

    def __init__(self, text: str = "", *, radius: int = 11,
                 sounds: SoundCues | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._init_ripple(radius, sounds)
        self.setFlat(True)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._press_ripple(event)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        super().paintEvent(event)
        self._paint_ripples()


class PixelSplash(QObject):
    """A blocky splash that grows on press and *holds* until release."""

    CELL = 16          # px per block - large enough to read as deliberate pixels
    MAX_RADIUS = 104   # the splash is a patch under the cursor, not a full-window wash

    def __init__(self, host: QWidget, *, colour: QColor | None = None) -> None:
        super().__init__(host)
        self._host = host
        self._colour = colour or QColor(10, 132, 255)
        self._origin = QPoint()
        self._radius = 0.0
        self._opacity = 0.0
        self._held = False

        self._grow = QPropertyAnimation(self, b"radius", self)
        self._grow.setEasingCurve(QEasingCurve.Type.OutQuint)
        self._fade = QPropertyAnimation(self, b"opacity", self)
        self._fade.setEasingCurve(EASE_OUT)

    def _get_radius(self) -> float:
        return self._radius

    def _set_radius(self, value: float) -> None:
        self._radius = value
        self._host.update()

    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, value: float) -> None:
        self._opacity = value
        self._host.update()

    radius = Property(float, _get_radius, _set_radius)
    opacity = Property(float, _get_opacity, _set_opacity)

    def press(self, origin: QPoint, *, peak: float = 0.55) -> None:
        self._origin = origin
        self._held = True

        self._grow.stop()
        self._fade.stop()
        self._grow.setDuration(260)
        self._grow.setStartValue(max(10.0, self._radius * 0.4))
        self._grow.setEndValue(float(self.MAX_RADIUS))
        self._fade.setDuration(140)
        self._fade.setStartValue(self._opacity)
        self._fade.setEndValue(peak)
        self._grow.start()
        self._fade.start()

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        self._fade.stop()
        self._fade.setDuration(420)
        self._fade.setStartValue(self._opacity)
        self._fade.setEndValue(0.0)
        self._fade.start()

    def paint(self, painter: QPainter, corner_radius: int) -> None:
        if self._opacity <= 0.002 or self._radius <= 0.0:
            return

        painter.save()
        # No antialiasing: crisp cell edges are the whole point
        painter.setRenderHint(QPainter.Antialiasing, False)
        clip = QPainterPath()
        clip.addRoundedRect(self._host.rect(), corner_radius, corner_radius)
        painter.setClipPath(clip)
        painter.setPen(Qt.NoPen)

        cell = self.CELL
        radius = self._radius
        rect = self._host.rect()
        left = max(rect.left(), int(self._origin.x() - radius))
        right = min(rect.right(), int(self._origin.x() + radius))
        top = max(rect.top(), int(self._origin.y() - radius))
        bottom = min(rect.bottom(), int(self._origin.y() + radius))

        # Snap to a global grid so blocks do not shimmer as the radius grows
        left -= left % cell
        top -= top % cell

        for y in range(top, bottom + 1, cell):
            for x in range(left, right + 1, cell):
                cx = x + cell * 0.5 - self._origin.x()
                cy = y + cell * 0.5 - self._origin.y()
                distance = (cx * cx + cy * cy) ** 0.5
                if distance > radius:
                    continue

                t = distance / radius if radius else 0.0
                weight = (1.0 - t) ** 1.25
                # Deterministic per-cell jitter gives the ragged, dithered edge
                jitter = (((x * 73856093) ^ (y * 19349663)) >> 4) & 0xFF
                weight *= 0.62 + (jitter / 255.0) * 0.38
                # Drop cells towards the rim so the boundary breaks up into pixels instead
                if t > 0.55 and jitter < int((t - 0.55) * 460):
                    continue

                alpha = self._opacity * weight
                if alpha <= 0.004:
                    continue
                colour = QColor(self._colour)
                colour.setAlphaF(min(1.0, alpha))
                painter.fillRect(x, y, cell - 1, cell - 1, colour)

        painter.restore()


class SplashSurface(QWidget):
    """A transparent layer that splashes where the window is grabbed."""

    def __init__(self, radius: int, parent: QWidget | None = None,
                 colour: QColor | None = None) -> None:
        super().__init__(parent)
        self._radius = radius
        self._splash = PixelSplash(self, colour=colour)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def splash_press(self, origin: QPoint, *, peak: float = 0.55) -> None:
        self.raise_()
        self._splash.press(origin, peak=peak)

    def splash_release(self) -> None:
        self._splash.release()

    def paintEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        painter = QPainter(self)
        self._splash.paint(painter, self._radius)


def fade_in(widget: QWidget, *, duration: int = 220,
            start: float = 0.0) -> QPropertyAnimation:
    """Fade a widget in. Returns the running animation."""
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(start)
    widget.setGraphicsEffect(effect)

    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(start)
    animation.setEndValue(1.0)
    animation.setEasingCurve(EASE_OUT)
    # Dropping the effect afterwards restores native painting
    animation.finished.connect(lambda: widget.setGraphicsEffect(None))
    animation.start()
    return animation


# Qt's "no maximum" sentinel
_UNCONSTRAINED = 16_777_215


def reveal(widget: QWidget, *, duration: int = 260) -> None:
    """Expand a widget into place while fading it in."""
    target = widget.height() or widget.sizeHint().height()
    if target <= 0:
        fade_in(widget, duration=duration)
        return

    fade_in(widget, duration=duration)

    animation = QPropertyAnimation(widget, b"maximumHeight", widget)
    animation.setDuration(duration)
    animation.setEasingCurve(EASE_OUT)
    animation.setStartValue(0)
    animation.setEndValue(target)
    # Release the constraint afterwards, or the card can never grow when its replies stream
    animation.finished.connect(lambda: widget.setMaximumHeight(_UNCONSTRAINED))
    animation.start()
    widget._entrance = animation  # keep a reference alive for the duration

    QTimer.singleShot(duration + 500,
                      lambda: widget.setMaximumHeight(_UNCONSTRAINED))


class Pulse:
    """A slow breathing fade, used to show the app is actively hearing speech."""

    def __init__(self, widget: QWidget) -> None:
        self._widget = widget
        self._animation: QPropertyAnimation | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        effect = QGraphicsOpacityEffect(self._widget)
        effect.setOpacity(1.0)
        self._widget.setGraphicsEffect(effect)

        animation = QPropertyAnimation(effect, b"opacity", effect)
        animation.setDuration(900)
        animation.setStartValue(1.0)
        animation.setKeyValueAt(0.5, 0.35)
        animation.setEndValue(1.0)
        animation.setEasingCurve(EASE_IN_OUT)
        animation.setLoopCount(-1)
        animation.start()
        self._animation = animation

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._animation is not None:
            self._animation.stop()
            self._animation = None
        # Detaching destroys the effect, which is why nothing here is cached
        try:
            self._widget.setGraphicsEffect(None)
        except RuntimeError:
            pass


def flash(widget: QWidget, *, duration: int = 420) -> None:
    """A quick dip-and-recover, for confirming a button did something."""
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(1.0)
    widget.setGraphicsEffect(effect)

    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(1.0)
    animation.setKeyValueAt(0.25, 0.35)
    animation.setKeyValueAt(0.6, 1.0)
    animation.setKeyValueAt(0.8, 0.75)
    animation.setEndValue(1.0)
    animation.setEasingCurve(EASE_IN_OUT)
    animation.finished.connect(lambda: widget.setGraphicsEffect(None))
    animation.start()
    widget._flash = animation


def collapse(widget: QWidget, *, duration: int = 200,
             on_done: Callable[[], None] | None = None) -> None:
    """The inverse of :func:`reveal` - shrink and fade out, then hide."""
    if not widget.isVisible():
        if on_done is not None:
            on_done()
        return

    height = widget.height()
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(1.0)
    widget.setGraphicsEffect(effect)

    fade = QPropertyAnimation(effect, b"opacity", widget)
    fade.setDuration(duration)
    fade.setEasingCurve(EASE_IN_OUT)
    fade.setStartValue(1.0)
    fade.setEndValue(0.0)

    shrink = QPropertyAnimation(widget, b"maximumHeight", widget)
    shrink.setDuration(duration)
    shrink.setEasingCurve(EASE_IN_OUT)
    shrink.setStartValue(height)
    shrink.setEndValue(0)

    def finish() -> None:
        widget.hide()
        widget.setGraphicsEffect(None)
        widget.setMaximumHeight(_UNCONSTRAINED)
        if on_done is not None:
            on_done()

    shrink.finished.connect(finish)
    fade.start()
    shrink.start()
    widget._collapse = (fade, shrink)
    # never leave a widget clamped to zero height
    QTimer.singleShot(duration + 500, lambda: widget.setMaximumHeight(_UNCONSTRAINED))


def animate_opacity(widget: QWidget, target: float, *, duration: int = 160) -> None:
    """Animate a window's overall opacity (not a graphics effect)."""
    animation = QPropertyAnimation(widget, b"windowOpacity", widget)
    animation.setDuration(duration)
    animation.setEasingCurve(EASE_OUT)
    animation.setStartValue(widget.windowOpacity())
    animation.setEndValue(target)
    animation.start()
    widget._opacity_animation = animation
