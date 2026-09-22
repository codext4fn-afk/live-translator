"""Always-on-top overlay window, built with Qt."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from . import glass, motion, theme
from .motion import PressableButton, PressableFrame, SoundCues, SplashSurface
from .settings_panel import SettingsPanel
from .sounds import SoundBoard, silent
from .theme import Icon
from .languages import NAMES as LANGUAGE_NAMES
from .languages import name as language_name
from .suggest import Outgoing, Reply

log = logging.getLogger(__name__)


def timestamp() -> str:
    return datetime.now().strftime("%H:%M")


class _Bridge(QObject):
    """Carries callables from worker threads onto the GUI thread."""

    invoke = Signal(object)


class ClickableFrame(QFrame):
    """A frame that reports clicks and participates in :hover styling."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(QCursor(Qt.PointingHandCursor))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def _label(text: str, object_name: str, *, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(wrap)
    label.setTextInteractionFlags(Qt.NoTextInteraction)
    return label


def _restyle(widget: QWidget) -> None:
    """Re-evaluate the stylesheet after an objectName or property change."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class Card(QFrame):
    """One utterance: what was heard, what it means, what to say back."""

    def __init__(self, overlay: Overlay, *, original: str, language: str,
                 confidence: float, meta: str) -> None:
        super().__init__()
        self._overlay = overlay
        self.setObjectName("card")
        self.setAttribute(Qt.WA_Hover, True)

        body = QVBoxLayout(self)
        body.setContentsMargins(16, 13, 16, 14)
        body.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(7)
        badge_text = (language or "??").upper()
        if confidence:
            badge_text += f"  {confidence:.0%}"
        header.addWidget(_label(badge_text, "langBadge", wrap=False))
        header.addWidget(_label(language_name(language), "langName", wrap=False))
        header.addStretch(1)
        self._meta = _label(meta, "meta", wrap=False)
        header.addWidget(self._meta)
        body.addLayout(header)

        self._original = _label(original, "original")
        body.addWidget(self._original)

        self._translation = _label("translating…", "pending")
        body.addWidget(self._translation)

        self._replies = QVBoxLayout()
        self._replies.setSpacing(6)
        self._replies.setContentsMargins(0, 3, 0, 0)
        body.addLayout(self._replies)

        self._reply_count = 0
        self._placeholder: QWidget | None = None


    def set_translation(self, text: str) -> None:
        self._translation.setObjectName("translation")
        self._translation.setText(text)
        _restyle(self._translation)

    def hide_translation(self) -> None:
        """Used when the speaker's language needs no translation."""
        self._translation.hide()
        self._original.setObjectName("originalPrimary")
        _restyle(self._original)

    def set_meta(self, text: str) -> None:
        self._meta.setText(text)

    def set_error(self, message: str) -> None:
        if self._translation.objectName() == "pending":
            self._translation.setObjectName("errorText")
            self._translation.setText(message)
            _restyle(self._translation)
        else:
            self._clear_placeholder()
            self._replies.addWidget(_label(message, "errorText"))

    def add_reply(self, reply: Reply) -> None:
        self._clear_placeholder()
        self._reply_count += 1

        row = PressableFrame(radius=theme.R_CONTROL, sounds=self._overlay.cues)
        row.setObjectName("replyRow")
        layout = QVBoxLayout(row)
        layout.setContentsMargins(13, 10, 12, 11)
        layout.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(9)
        top.addWidget(_label(str(self._reply_count), "replyIndex", wrap=False),
                      0, Qt.AlignTop)
        top.addWidget(_label(reply.reply, "replyText"), 1)
        top.addWidget(_label(Icon.COPY, "copyIcon", wrap=False), 0, Qt.AlignTop)
        layout.addLayout(top)

        if reply.say:
            pronunciation = _label(reply.say, "replySay")
            pronunciation.setContentsMargins(24, 0, 0, 0)
            layout.addWidget(pronunciation)

        if reply.gloss:
            gloss = _label(reply.gloss, "replyGloss")
            gloss.setContentsMargins(24, 0, 0, 0)
            layout.addWidget(gloss)

        row.setToolTip("Click to copy")
        row.clicked.connect(lambda text=reply.reply: self._overlay.copy_to_clipboard(text))
        self._replies.addWidget(row)
        motion.fade_in(row, duration=200)

    def awaiting_replies(self) -> None:
        """Show a hint while the model is still writing suggestions."""
        if self._reply_count or self._placeholder is not None:
            return
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(7)
        row.addWidget(_label(Icon.SPARKLE, "copyIcon", wrap=False))
        row.addWidget(_label("thinking of replies…", "pending"), 1)
        self._placeholder = holder
        self._replies.addWidget(holder)

    def finish(self) -> None:
        had_placeholder = self._placeholder is not None
        self._clear_placeholder()
        if had_placeholder and not self._reply_count:
            self._replies.addWidget(_label("no replies suggested", "pending"))

    def _clear_placeholder(self) -> None:
        if self._placeholder is not None:
            self._placeholder.setParent(None)
            self._placeholder.deleteLater()
            self._placeholder = None


class ComposeBar(QFrame):
    """Type in your own language, get it back in theirs."""

    submitted = Signal(str, str)  # text, target language code

    def __init__(self, overlay: Overlay, my_language: str) -> None:
        super().__init__()
        self._overlay = overlay
        self.setObjectName("composeBar")
        self._auto_code = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        self._panel = QFrame()
        self._panel.setObjectName("outgoingPanel")
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(11, 9, 11, 10)
        panel_layout.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        self._echo = _label("", "outgoingEcho")
        head.addWidget(self._echo, 1)
        self._dismiss = PressableButton(Icon.CLOSE, radius=9, sounds=overlay.cues)
        self._dismiss.setObjectName("dismiss")
        self._dismiss.setToolTip("Dismiss this translation")
        self._dismiss.clicked.connect(self.dismiss)
        head.addWidget(self._dismiss, 0, Qt.AlignTop)

        self._primary_row, self._primary, self._primary_say = self._copy_row(
            "outgoingPrimary")
        self._note = _label("", "outgoingNote")

        panel_layout.addLayout(head)
        panel_layout.addWidget(self._primary_row)
        panel_layout.addWidget(self._note)
        self._panel.hide()
        outer.addWidget(self._panel)

        row = QHBoxLayout()
        row.setSpacing(8)

        self._picker = QComboBox()
        self._picker.setObjectName("langPicker")
        self._picker.setCursor(QCursor(Qt.PointingHandCursor))
        self._picker.addItem("Auto", "")
        for code, name in sorted(LANGUAGE_NAMES.items(), key=lambda kv: kv[1]):
            self._picker.addItem(name, code)
        self._picker.setToolTip(
            "Language to translate into. Auto follows whoever spoke last."
        )
        overlay.add_hover_cue(self._picker)
        self._picker.currentIndexChanged.connect(lambda _i: overlay.cues.click())
        row.addWidget(self._picker)

        self._input = QLineEdit()
        self._input.setObjectName("composeInput")
        self._input.setPlaceholderText(f"Say something in {my_language}…")
        self._input.returnPressed.connect(self._submit)
        overlay.add_hover_cue(self._input)
        row.addWidget(self._input, 1)

        self._send = PressableButton(Icon.SEND, radius=20, sounds=overlay.cues)
        self._send.setObjectName("sendButton")
        self._send.setToolTip("Translate  (Enter)")
        self._send.clicked.connect(self._submit)
        row.addWidget(self._send)

        outer.addLayout(row)

    def set_my_language(self, name: str) -> None:
        self._input.setPlaceholderText(f"Say something in {name}…")

    def dismiss(self) -> None:
        """Clear the translation result and hand focus back to the input."""
        self._overlay.sounds.play("tap")
        motion.collapse(self._panel, duration=200)
        self._input.setFocus(Qt.OtherFocusReason)

    def _copy_row(self, label_style: str) -> tuple[PressableFrame, QLabel, QLabel]:
        """A click-to-copy row: the text, and a pronunciation line beneath it."""
        row = PressableFrame(radius=12, sounds=self._overlay.cues)
        row.setObjectName("outgoingRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(7, 4, 7, 4)
        layout.setSpacing(8)

        stack = QVBoxLayout()
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(1)
        label = _label("", label_style)
        say = _label("", "outgoingSay")
        say.hide()
        stack.addWidget(label)
        stack.addWidget(say)
        layout.addLayout(stack, 1)

        layout.addWidget(_label(Icon.COPY, "copyIcon", wrap=False), 0, Qt.AlignTop)
        row.setToolTip("Click to copy")
        # Copies the text itself, never the romanisation
        row.clicked.connect(lambda: self._overlay.copy_to_clipboard(label.text()))
        return row, label, say


    def set_auto_language(self, code: str) -> None:
        """Track the most recent speaker for the Auto entry."""
        self._auto_code = code
        self._picker.setItemText(0, f"Auto · {language_name(code)}" if code else "Auto")

    def target_code(self) -> str:
        return self._picker.currentData() or self._auto_code

    def _submit(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        target = self.target_code()
        if not target:
            self.show_error("No language yet - wait for someone to speak, or pick one.")
            return
        self._input.clear()
        self._overlay.sounds.play("send")
        motion.flash(self._send)
        self.show_pending(text, target)
        self.submitted.emit(text, target)


    def set_busy(self, busy: bool) -> None:
        self._send.setEnabled(not busy)

    def _open_panel(self, *, animate: bool) -> None:
        """Show the result panel, expanding it into place the first time."""
        first_time = not self._panel.isVisible()
        self._panel.show()
        if animate and first_time:
            QTimer.singleShot(0, lambda: motion.reveal(self._panel, duration=220))

    @staticmethod
    def _set_say(label: QLabel, text: str) -> None:
        label.setText(text)
        label.setVisible(bool(text))

    def show_pending(self, text: str, target: str) -> None:
        self._echo.setText(f"{text}   →   {language_name(target)}")
        self._primary.setText("translating…")
        self._set_say(self._primary_say, "")
        self._primary_row.show()
        self._note.hide()
        self._open_panel(animate=True)

    def show_result(self, result: Outgoing) -> None:
        if result.error:
            self.show_error(result.error)
            return
        self._primary.setText(result.natural)
        self._set_say(self._primary_say, result.say)
        self._primary_row.show()
        if result.note:
            self._note.setText(f"{Icon.WARNING}  {result.note}")
            self._note.show()
        else:
            self._note.hide()
        self._open_panel(animate=True)
        motion.fade_in(self._primary_row, duration=220)

    def show_error(self, message: str) -> None:
        self._primary.setText(message)
        self._set_say(self._primary_say, "")
        self._primary_row.show()
        self._note.hide()
        self._open_panel(animate=True)


class Overlay(QWidget):
    """The overlay window and its public, thread-safe API."""

    def __init__(
        self,
        *,
        width: int = 580,
        height: int = 560,
        opacity: float = 0.96,
        font_size: int = 11,
        max_history: int = 40,
        always_on_top: bool = True,
        my_language: str = "English",
        font_family: str = "",
        sounds: SoundBoard | None = None,
        acrylic: bool = False,
        gradient: str = theme.DEFAULT_GRADIENT,
    ) -> None:
        self._app = QApplication.instance() or QApplication([])
        # Must happen before the stylesheet is applied, or the families are unknown
        theme.load_bundled_fonts()
        super().__init__()

        self.font_size = font_size
        self.max_history = max_history
        self._opacity = opacity
        self._paused = False
        self._closing = False
        self.backdrop = "none"
        self.sounds = sounds if sounds is not None else silent()
        self.cues = SoundCues(hover=lambda: self.sounds.play("hover"),
                              click=lambda: self.sounds.play("tap"))
        self._dragging = False
        self._acrylic = acrylic
        self._hover_cued: set[QWidget] = set()
        self._gradient = gradient if gradient in theme.GRADIENTS else theme.DEFAULT_GRADIENT
        self._font_family = font_family
        self._on_gradient: Callable[[str], None] | None = None
        self._on_language: Callable[[str], None] | None = None
        self.my_language = my_language

        self._on_toggle_pause: Callable[[bool], None] | None = None
        self._on_quit: Callable[[], None] | None = None
        self._on_copy: Callable[[str], None] | None = None

        self._cards: list[Card] = []
        self._drag_offset = None

        flags = Qt.FramelessWindowHint | Qt.Tool
        if always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # Appearing must not pull the user out of their game or call
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowTitle("live-translator")
        self.setWindowOpacity(opacity)
        self.resize(width, height)
        self.setMinimumSize(420, 340)
        self._app.setStyleSheet(
            theme.stylesheet(font_size, font_family, self._gradient)
        )

        self._build(my_language)
        self._position()

        self._bridge = _Bridge()
        self._bridge.invoke.connect(self._run_callable, Qt.QueuedConnection)

        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast.hide)


    def _build(self, my_language: str) -> None:
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)

        self._root = QFrame()
        self._root.setObjectName("root")
        shell.addWidget(self._root)

        layout = QVBoxLayout(self._root)
        layout.setContentsMargins(14, 12, 14, 6)
        layout.setSpacing(10)

        layout.addWidget(self._build_titlebar())
        layout.addWidget(self._build_feed(), 1)

        self.compose = ComposeBar(self, my_language)
        layout.addWidget(self.compose)

        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.setSpacing(0)
        grip_row.addStretch(1)
        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(13, 13)
        grip_row.addWidget(self._grip, 0, Qt.AlignBottom)
        layout.addLayout(grip_row)
        # The grip is decoration, not a row of content; do not let it claim space
        layout.setStretch(layout.count() - 1, 0)

        self._sheen = QFrame(self._root)
        self._sheen.setObjectName("sheen")
        self._sheen.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._sheen.lower()

        self._settings = SettingsPanel(self._root, current=self._gradient,
                                       sounds=self.cues, my_language=my_language)
        self._settings.chosen.connect(self._apply_gradient)
        self._settings.language_chosen.connect(self._apply_language)

        # raised last so the splash draws over the content
        self._splash = SplashSurface(theme.R_WINDOW, self._root,
                                     colour=QColor(theme.BLUE))
        self._splash.raise_()

    def _build_titlebar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(34)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(3, 0, 0, 0)
        layout.setSpacing(8)

        self._dot = _label(Icon.MIC, "statusIcon", wrap=False)
        self._dot.setStyleSheet(f"color: {theme.LABEL_3};")
        self._pulse = motion.Pulse(self._dot)
        layout.addWidget(self._dot)

        self._titleLabel = _label("live-translator", "title", wrap=False)
        layout.addWidget(self._titleLabel)
        layout.addSpacing(8)

        self._toast = _label("", "toast", wrap=False)
        self._toast.hide()
        layout.addWidget(self._toast)
        layout.addStretch(1)

        self._pause_btn = self._chrome_button(Icon.PAUSE, self.toggle_pause,
                                              "Pause listening")
        layout.addWidget(self._pause_btn)
        layout.addWidget(self._chrome_button(Icon.SETTINGS, self._toggle_settings,
                                             "Appearance"))
        layout.addWidget(self._chrome_button(Icon.CLEAR, self.clear, "Clear history"))
        layout.addWidget(self._chrome_button(Icon.OPACITY_DOWN,
                                             lambda: self.nudge_opacity(-0.06),
                                             "More transparent"))
        layout.addWidget(self._chrome_button(Icon.OPACITY_UP,
                                             lambda: self.nudge_opacity(0.06),
                                             "More opaque"))
        close = self._chrome_button(Icon.CLOSE, self.request_quit, "Close")
        close.setObjectName("chromeClose")
        layout.addWidget(close)

        # Dragging is handled at window level, so the bar needs no handlers of its own
        self._titleBar = bar
        return bar

    def _chrome_button(self, glyph: str, slot: Callable[[], None],
                       tooltip: str) -> QPushButton:
        button = PressableButton(glyph, radius=11, sounds=self.cues)
        button.setObjectName("chrome")
        button.setToolTip(tooltip)
        button.clicked.connect(slot)
        return button

    def _build_feed(self) -> QScrollArea:
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        self._feed = QVBoxLayout(container)
        self._feed.setContentsMargins(0, 0, 5, 0)
        self._feed.setSpacing(10)

        self._empty = QWidget()
        empty_layout = QVBoxLayout(self._empty)
        empty_layout.setContentsMargins(6, 26, 6, 0)
        empty_layout.setSpacing(10)
        icon = _label(Icon.GLOBE, "emptyIcon", wrap=False)
        icon.setAlignment(Qt.AlignHCenter)
        empty_layout.addWidget(icon)
        message = _label(
            "Listening to your speakers.\n\n"
            "Play some speech and it will appear here - "
            "or type below to say something in their language.",
            "empty",
        )
        message.setAlignment(Qt.AlignHCenter)
        empty_layout.addWidget(message)

        self._feed.addWidget(self._empty)
        self._feed.addStretch(1)

        self._scroll.setWidget(container)
        return self._scroll

    def _position(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.left() + 40, area.top() + 40)

    def resizeEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        super().resizeEvent(event)
        if hasattr(self, "_sheen"):
            self._sheen.setGeometry(1, 1, max(0, self.width() - 2), 48)
        if hasattr(self, "_splash"):
            self._splash.setGeometry(self._root.rect())
        self._clip_corners()

    def showEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        super().showEvent(event)
        if self.backdrop == "none":
            self.backdrop = glass.apply(int(self.winId()), acrylic=self._acrylic)
            log.info("window backdrop: %s", self.backdrop)
        self._clip_corners()

    def _clip_corners(self) -> None:
        """Clip the window when the compositor is drawing the backdrop."""
        if self._acrylic and self.isVisible():
            glass.round_window(int(self.winId()), self.width(), self.height(),
                               theme.R_WINDOW)


    # Anything the user can operate; a press on one of these is not a drag
    _INTERACTIVE = (QPushButton, QLineEdit, QComboBox, QScrollBar, QSizeGrip,
                    PressableFrame)

    def _is_interactive(self, child: QWidget | None) -> bool:
        """Walk up from the clicked widget looking for a control."""
        while child is not None and child is not self:
            if isinstance(child, self._INTERACTIVE):
                return True
            child = child.parentWidget()
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Start a drag from anywhere that is not a control."""
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        if self._is_interactive(self.childAt(event.position().toPoint())):
            return super().mousePressEvent(event)

        self._drag_offset = (
            event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        )
        self._dragging = True
        self.sounds.play("grab")
        local = self._root.mapFrom(self, event.position().toPoint())
        self._splash.splash_press(local, peak=0.55)
        motion.animate_opacity(self, max(0.55, self._opacity - 0.06), duration=180)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._dragging = False
            self._drag_offset = None
            self.sounds.play("drop")
            self._splash.splash_release()
            motion.animate_opacity(self, self._opacity, duration=260)
        super().mouseReleaseEvent(event)

    def nudge_opacity(self, delta: float) -> None:
        self._opacity = min(1.0, max(0.30, self._opacity + delta))
        self.setWindowOpacity(self._opacity)

    def toggle_pause(self) -> None:
        self._paused = not self._paused
        self._pause_btn.setText(Icon.PLAY if self._paused else Icon.PAUSE)
        self._pause_btn.setObjectName("chromeActive" if self._paused else "chrome")
        _restyle(self._pause_btn)
        self.set_listening(False)
        if self._on_toggle_pause is not None:
            self._on_toggle_pause(self._paused)

    @property
    def paused(self) -> bool:
        return self._paused

    def clear(self) -> None:
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self._empty.show()

    def request_quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._on_quit is not None:
            self._on_quit()
        self._app.quit()

    def closeEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        self.request_quit()
        super().closeEvent(event)

    def copy_to_clipboard(self, text: str) -> None:
        if not text or text == "translating…":
            return
        QGuiApplication.clipboard().setText(text)
        self.sounds.play("copy")
        self.toast("copied")
        if self._on_copy is not None:
            try:
                self._on_copy(text)
            except Exception:
                log.exception("copy hook failed")

    def toast(self, message: str, ms: int = 1500) -> None:
        self._toast.setText(message)
        self._toast.show()
        motion.fade_in(self._toast, duration=160)
        self._toast_timer.start(ms)


    def add_hover_cue(self, widget: QWidget) -> None:
        """Play the hover cue when the pointer enters ``widget``."""
        widget.setAttribute(Qt.WA_Hover, True)
        widget.installEventFilter(self)
        self._hover_cued.add(widget)

    def eventFilter(self, watched, event) -> bool:  # noqa: ANN001 - Qt signature
        if event.type() == QEvent.Type.Enter and watched in self._hover_cued:
            self.cues.hover()
        return super().eventFilter(watched, event)

    def set_pause_hook(self, fn: Callable[[bool], None]) -> None:
        self._on_toggle_pause = fn

    def set_quit_hook(self, fn: Callable[[], None]) -> None:
        self._on_quit = fn

    def set_copy_hook(self, fn: Callable[[str], None]) -> None:
        """Called with the text whenever the user copies a reply or translation."""
        self._on_copy = fn

    def set_compose_hook(self, fn: Callable[[str, str], None]) -> None:
        """Called with (text, target_code) when the user submits the compose box."""
        self.compose.submitted.connect(fn)

    def post(self, fn: Callable[[], None]) -> None:
        """Schedule ``fn`` to run on the GUI thread. Safe from any thread."""
        if not self._closing:
            self._bridge.invoke.emit(fn)

    def _run_callable(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            log.exception("overlay callback failed")


    def _toggle_settings(self) -> None:
        self._position_settings()
        self._settings.toggle()

    def _position_settings(self) -> None:
        """Centre the panel near the top, under the title bar."""
        panel = self._settings
        panel.adjustSize()
        x = max(14, (self._root.width() - panel.width()) // 2)
        panel.move(x, 54)

    def _apply_gradient(self, name: str) -> None:
        """Restyle the whole app with a new window gradient and remember it."""
        self._gradient = name
        self._app.setStyleSheet(
            theme.stylesheet(self.font_size, self._font_family, name)
        )
        self.sounds.play("click")
        if self._on_gradient is not None:
            try:
                self._on_gradient(name)
            except Exception:
                log.exception("could not persist gradient choice")

    def set_gradient_hook(self, fn: Callable[[str], None]) -> None:
        """Called with the gradient name whenever the user picks one."""
        self._on_gradient = fn

    def _apply_language(self, name: str) -> None:
        """Change the language everything is translated into."""
        self.my_language = name
        self.compose.set_my_language(name)
        if self._on_language is not None:
            try:
                self._on_language(name)
            except Exception:
                log.exception("could not apply language change")

    def set_language_hook(self, fn: Callable[[str], None]) -> None:
        """Called with the language name whenever the user picks a new one."""
        self._on_language = fn

    def set_status(self, text: str) -> None:
        """Pipeline state. """
        self._titleLabel.setToolTip(text)
        self._dot.setToolTip(text)

    def set_listening(self, active: bool) -> None:
        if self._paused:
            colour = theme.ORANGE
        else:
            colour = theme.GREEN if active else theme.LABEL_3
        self._dot.setStyleSheet(f"color: {colour};")
        if active and not self._paused:
            self._pulse.start()
        else:
            self._pulse.stop()

    def set_target_language(self, code: str) -> None:
        self.compose.set_auto_language(code)

    def show_outgoing(self, result: Outgoing) -> None:
        self.compose.show_result(result)
        self.compose.set_busy(False)

    def add_card(self, *, original: str, language: str, confidence: float,
                 meta: str = "") -> Card:
        self._empty.hide()
        card = Card(self, original=original, language=language,
                    confidence=confidence, meta=meta)
        # Newest first, so the latest line is always visible without scrolling
        self._feed.insertWidget(0, card)
        self._cards.insert(0, card)

        while len(self._cards) > self.max_history:
            stale = self._cards.pop()
            stale.setParent(None)
            stale.deleteLater()

        self.sounds.play("card")
        # Height is only known after a layout pass, so animate on the next tick
        QTimer.singleShot(0, lambda: motion.reveal(card))
        QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(0))
        return card

    def run(self) -> None:
        self.show()
        self._app.exec()
        self._closing = True

    def destroy(self) -> None:  # noqa: A003 - mirrors the previous API
        self._closing = True
        try:
            self.hide()
        except RuntimeError:
            pass
