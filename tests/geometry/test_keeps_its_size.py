"""The interface staying the size it was when the display changes underneath it.

"When the app is open and Windows goes to sleep and then I wake it up, all the
font sizes in app are tiny."

Waking re-enumerates the displays. Qt sees screens go and come back, and the
scaling it settled on at start-up gets worked out again against whichever
reading Windows offers first. Measured on this machine: the application font is
Segoe UI at 9 point against a *logical* 96 DPI, with all the scaling carried by
a device pixel ratio of 2.0 - so a reading that loses the ratio lays every
unstyled widget out at half size.

Simulated by moving the application font, which is exactly what a bad reading
costs: nothing in ModelPop sets a font on a widget, so every unstyled one
follows the application font and nothing else. Sizes in a style sheet are in
device-independent pixels and are unaffected, which is why they are checked too
- half the interface coming back and half not would be worse than neither.
"""

import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow

from modelpop.ui.keeps_its_size import KeepsItsSize


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


@pytest.fixture
def interface(app):
    """An application sized the way the real one is, and a window in it."""
    app.setFont(QFont("Segoe UI", 9))
    keeper = KeepsItsSize(app)

    window = QMainWindow()
    unstyled = QLabel("plain")
    styled = QLabel("heading")
    window.setCentralWidget(unstyled)
    # Parented and styled after, so the style sheet is applied to a widget
    # that is actually in the window this keeper will re-polish.
    styled.setParent(window)
    styled.setStyleSheet("font-size: 15px;")
    window.show()
    QApplication.processEvents()

    yield keeper, unstyled, styled

    window.close()
    QApplication.processEvents()


def a_bad_reading(app) -> None:
    """What waking up to a display reported at half its density amounts to."""
    app.setFont(QFont("Segoe UI", 4))


class TestPuttingItBack:
    def test_it_remembers_the_size_it_started_at(self, interface):
        keeper, _unstyled, _styled = interface

        assert keeper.font.pointSize() == 9

    def test_an_unstyled_widget_comes_back(self, interface, app):
        """The bug, as a measurement."""
        keeper, unstyled, _styled = interface
        assert unstyled.font().pointSize() == 9

        a_bad_reading(app)
        QApplication.processEvents()
        assert unstyled.font().pointSize() == 4, "the simulation did nothing"

        keeper.restore()
        QApplication.processEvents()

        assert unstyled.font().pointSize() == 9

    def test_a_style_sheet_size_is_left_where_it_was(self, interface, app):
        """Those are device-independent pixels and were never the problem."""
        keeper, _unstyled, styled = interface
        assert styled.font().pixelSize() == 15

        a_bad_reading(app)
        keeper.restore()
        QApplication.processEvents()

        assert styled.font().pixelSize() == 15

    def test_restoring_when_nothing_is_wrong_changes_nothing(self, interface, app):
        """It hangs off signals that fire for ordinary reasons - moving a
        window between monitors, for one - so it has to be safe for nothing."""
        keeper, unstyled, _styled = interface

        keeper.restore()
        keeper.restore()
        QApplication.processEvents()

        assert unstyled.font().pointSize() == 9


class TestWhatItWatches:
    def test_it_follows_every_screen_that_is_there(self, interface):
        from PySide6.QtGui import QGuiApplication

        keeper, _unstyled, _styled = interface

        assert len(keeper._watching) == len(QGuiApplication.screens())

    def test_coming_back_to_the_front_puts_it_right(self, interface, app):
        """The one event certain to happen after a resume.

        Which screen signals Windows emits on wake cannot be established
        without sleeping the machine, so this does not depend on any of them.
        """
        from PySide6.QtCore import Qt

        keeper, unstyled, _styled = interface

        a_bad_reading(app)
        keeper._came_back(Qt.ApplicationState.ApplicationActive)
        QApplication.processEvents()

        assert unstyled.font().pointSize() == 9

    def test_going_to_the_background_is_left_alone(self, interface, app):
        """Only coming back is worth acting on; going away is not."""
        from PySide6.QtCore import Qt

        keeper, unstyled, _styled = interface

        a_bad_reading(app)
        keeper._came_back(Qt.ApplicationState.ApplicationInactive)
        QApplication.processEvents()

        assert unstyled.font().pointSize() == 4
