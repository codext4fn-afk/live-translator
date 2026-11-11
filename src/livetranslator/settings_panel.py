"""In-window settings: window gradient and the language you read."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from . import motion, theme
from .motion import PressableButton, PressableFrame, SoundCues
from .languages import NAMES as LANGUAGE_NAMES
from .theme import Icon

log = logging.getLogger(__name__)


class NoWheelComboBox(QComboBox):
    """A dropdown that ignores the scroll wheel unless its list is open.

    A normal QComboBox changes its value when you scroll over it. In an overlay
    you scroll past all the time, so this kept switching languages by accident,
    once for every language the pointer rolled over
    """

    def wheelEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


class Swatch(PressableFrame):
    """A preview tile filled with one gradient."""

    def __init__(self, name: str, *, sounds: SoundCues | None) -> None:
        super().__init__(radius=14, sounds=sounds)
        self.name = name
        self.setObjectName("swatch")
        self.setFixedSize(74, 46)
        self.setToolTip(name.capitalize())
        self._apply_fill()

    def _apply_fill(self) -> None:
        g0, g1, g2 = theme.gradient_stops(self.name)
        # Painted inline: each swatch needs its own colours
        self.setStyleSheet(
            f"#swatch, #swatchActive {{ background: qlineargradient("
            f"x1:0, y1:0, x2:0.4, y2:1, stop:0 {g0}, stop:0.55 {g1}, stop:1 {g2}); }}"
        )

    def set_active(self, active: bool) -> None:
        self.setObjectName("swatchActive" if active else "swatch")
        self.style().unpolish(self)
        self.style().polish(self)
        self._apply_fill()


class SettingsPanel(QFrame):
    """Gradient picker, shown over the feed."""

    chosen = Signal(str)
    language_chosen = Signal(str)

    def __init__(self, parent, *, current: str, sounds: SoundCues | None,
                 my_language: str = "English",
                 on_close: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPanel")
        self._sounds = sounds
        self._on_close = on_close
        self._swatches: list[Swatch] = []
        self._current = current

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("Appearance")
        title.setObjectName("settingsTitle")
        head.addWidget(title, 1)

        close = PressableButton(Icon.CLOSE, radius=9, sounds=sounds)
        close.setObjectName("dismiss")
        close.setToolTip("Close")
        close.clicked.connect(self.dismiss)
        head.addWidget(close, 0, Qt.AlignTop)
        outer.addLayout(head)

        hint = QLabel("Window gradient")
        hint.setObjectName("settingsHint")
        outer.addWidget(hint)
        outer.addSpacing(8)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        for index, name in enumerate(theme.GRADIENT_ORDER):
            cell = QVBoxLayout()
            cell.setSpacing(5)

            swatch = Swatch(name, sounds=sounds)
            swatch.clicked.connect(lambda n=name: self._choose(n))
            swatch.set_active(name == current)
            self._swatches.append(swatch)

            caption = QLabel(name.capitalize())
            caption.setObjectName(
                "swatchNameActive" if name == current else "swatchName"
            )
            caption.setAlignment(Qt.AlignHCenter)
            swatch.caption = caption  # kept together so _choose can restyle both

            cell.addWidget(swatch, 0, Qt.AlignHCenter)
            cell.addWidget(caption)
            grid.addLayout(cell, index // 4, index % 4)

        outer.addLayout(grid)
        outer.addSpacing(14)

        language_hint = QLabel("Translate everything into")
        language_hint.setObjectName("settingsHint")
        outer.addWidget(language_hint)
        outer.addSpacing(6)

        self._language = NoWheelComboBox()
        self._language.setObjectName("langPicker")
        self._language.setCursor(Qt.PointingHandCursor)
        names = sorted({*LANGUAGE_NAMES.values(), "English"})
        self._language.addItems(names)
        if my_language in names:
            self._language.setCurrentText(my_language)
        self._language.setToolTip(
            "Incoming speech is translated into this, and reply meanings are "
            "written in it."
        )
        # textActivated only fires when the user actually picks something, not
        # when the value changes in code or while arrowing through the list
        self._language.textActivated.connect(self._choose_language)
        self._my_language = my_language
        outer.addWidget(self._language)

        self.hide()

    def _choose_language(self, name: str) -> None:
        if name == self._my_language:
            return
        self._my_language = name
        if self._sounds is not None:
            self._sounds.click()
        self.language_chosen.emit(name)


    def _choose(self, name: str) -> None:
        if name == self._current:
            return
        self._current = name
        for swatch in self._swatches:
            active = swatch.name == name
            swatch.set_active(active)
            swatch.caption.setObjectName(
                "swatchNameActive" if active else "swatchName"
            )
            swatch.caption.style().unpolish(swatch.caption)
            swatch.caption.style().polish(swatch.caption)
        self.chosen.emit(name)

    def reveal(self) -> None:
        self.show()
        self.raise_()
        motion.fade_in(self, duration=200)

    def dismiss(self) -> None:
        motion.collapse(self, duration=180, on_done=self._on_close)

    def toggle(self) -> None:
        if self.isVisible():
            self.dismiss()
        else:
            self.reveal()
