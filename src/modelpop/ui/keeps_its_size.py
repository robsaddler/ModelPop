"""Keeping the interface the size it was when the display changes underneath it.

Windows going to sleep and waking again re-enumerates the displays. Qt sees
screens disappear and reappear, and the scaling it resolved at start-up - here
a device pixel ratio of 2.0 against a logical 96 DPI - is worked out again
against whatever Windows reports first. Get that moment wrong and every widget
that was never given an explicit font is laid out for a display half the size
of the real one. The window comes back with all the text tiny, and nothing
about it looks like a bug the application could have caused.

There is no way to make Qt resolve it correctly the first time, so this does
the next best thing: it remembers what the interface was sized at, notices
every event that could have disturbed it, and puts it back.

Two halves, and both are needed. The **application font** is re-applied,
because that is what the unstyled widgets follow. And every top-level window is
re-**polished**, because a style sheet's sizes are resolved when the widget is
polished and are not recomputed on their own - a re-applied font on a stale
style sheet leaves half the interface at the old size and looks worse than
leaving it alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from PySide6.QtGui import QScreen

__all__ = ["KeepsItsSize"]


class KeepsItsSize(QObject):
    """Puts the interface back to its own size after the display changes.

    Built once, beside the application, and left alone. It holds the font the
    interface started with and restores it whenever a screen appears, goes
    away, or changes how densely it reports itself.
    """

    def __init__(self, app: QApplication, parent: QObject | None = None) -> None:
        """Remember how the interface is sized, and watch for that changing."""
        super().__init__(parent)
        self._app = app
        # Taken now rather than read back later, because by the time anything
        # has gone wrong the application font *is* the wrong one.
        self._font = QFont(app.font())
        self._watching: list[QScreen] = []

        app.screenAdded.connect(self._screen_arrived)
        app.screenRemoved.connect(self._something_changed)
        # And whenever the application is brought back to the front. Belt and
        # braces: which of the screen signals Windows emits on resume is not
        # something that can be established without sleeping the machine, and
        # coming back to a window is the one event certain to happen after it.
        # `restore` costs a layout pass and is safe to call for nothing.
        app.applicationStateChanged.connect(self._came_back)
        for screen in QGuiApplication.screens():
            self._watch(screen)

    @property
    def font(self) -> QFont:
        """The font the interface is meant to be using."""
        return QFont(self._font)

    def _watch(self, screen: QScreen) -> None:
        """Follow one screen's idea of how dense it is."""
        if screen in self._watching:
            return
        self._watching.append(screen)
        screen.logicalDotsPerInchChanged.connect(self._something_changed)
        screen.physicalDotsPerInchChanged.connect(self._something_changed)
        screen.geometryChanged.connect(self._something_changed)

    def _screen_arrived(self, screen: QScreen) -> None:
        self._watch(screen)
        self._something_changed()

    def _came_back(self, state: Qt.ApplicationState) -> None:
        """The user is looking at the window again, so make sure it is right."""
        if state is Qt.ApplicationState.ApplicationActive:
            self.restore()

    def _something_changed(self, *_ignored: object) -> None:
        """A display changed. Put the interface back to its own size."""
        self.restore()

    def restore(self) -> None:
        """Re-apply the remembered font and re-polish every window.

        Safe to call when nothing is wrong: re-applying the same font and
        re-polishing costs a layout pass and changes nothing anybody can see.
        That matters, because the signals this hangs off fire for reasons that
        have nothing to do with sleeping - moving a window between monitors,
        for one.
        """
        if self._app.font() != self._font:
            self._app.setFont(self._font)

        style = self._app.style()
        for window in self._app.topLevelWidgets():
            # A style sheet's sizes are resolved at polish time and are not
            # worked out again on their own. Without this the font comes back
            # and every hint, heading and label styled in the style sheet
            # stays at whatever size the bad reading left it.
            style.unpolish(window)
            style.polish(window)
            window.update()
