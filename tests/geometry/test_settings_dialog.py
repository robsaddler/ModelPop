"""The settings dialog, and the fact that it has to fit on a screen.

"Settings screen is a tall mess and doesn't fit vertically on my laptop."

It did not: seven groups stacked in a 520-pixel column came to 1,176 pixels
tall against 1,152 of available screen, so the Save button was below the
bottom edge with no way to reach it.

Tabs, grouped by what each setting is *about* rather than by what kind of
control it is - the printer is one job and the assistant is another, and nobody
configures both in the same sitting.
"""

import pytest
from PySide6.QtWidgets import QApplication, QGroupBox, QTabWidget

from modelpop.ai.secrets import LayeredSecretStore
from modelpop.application.ai_ports import AiSettings
from modelpop.printing.bambu_profiles import KnownPrinters
from modelpop.ui.dialogs import WIDE_ENOUGH, SettingsDialog

# The shortest laptop worth designing for, in logical pixels. A 768-tall panel
# at 150% scaling leaves about this much once the taskbar has had its share.
A_SHORT_LAPTOP = 700


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


def a_dialog() -> SettingsDialog:
    return SettingsDialog(
        LayeredSecretStore(),
        AiSettings(),
        None,
        "generation status",
        "graphics status",
        KnownPrinters(None).all(),
        "",
    )


class TestItFitsOnAScreen:
    def test_it_is_short_enough_for_a_laptop(self, app):
        """The bug, as a number."""
        dialog = a_dialog()
        try:
            assert dialog.sizeHint().height() <= A_SHORT_LAPTOP, (
                f"it wants {dialog.sizeHint().height()}px of height"
            )
        finally:
            dialog.deleteLater()

    def test_it_is_wide_rather_than_tall(self, app):
        """A field and its Forget button belong on one line."""
        dialog = a_dialog()
        try:
            assert dialog.minimumWidth() >= WIDE_ENOUGH
        finally:
            dialog.deleteLater()

    def test_no_tab_is_taller_than_the_dialog(self, app):
        """Switching tabs must not resize the window under the user."""
        dialog = a_dialog()
        try:
            tabs = dialog.findChild(QTabWidget)
            assert tabs is not None
            for index in range(tabs.count()):
                page = tabs.widget(index)
                assert page.sizeHint().height() <= A_SHORT_LAPTOP, (
                    f"the {tabs.tabText(index)!r} tab wants {page.sizeHint().height()}px"
                )
        finally:
            dialog.deleteLater()


class TestEverythingIsStillThere:
    """Tabs hide things by design, so nothing may go missing in the move."""

    def test_every_group_is_on_some_tab(self, app):
        dialog = a_dialog()
        try:
            titles = {box.title() for box in dialog.findChildren(QGroupBox)}
            assert titles == {
                "Anthropic API key",
                "Models",
                "Limits",
                "Making a model from a picture",
                "Where to search for models",
                "The viewport",
                "The printer",
            }
        finally:
            dialog.deleteLater()

    def test_the_tabs_are_named_for_what_they_are_about(self, app):
        dialog = a_dialog()
        try:
            tabs = dialog.findChild(QTabWidget)
            named = [tabs.tabText(i) for i in range(tabs.count())]

            assert named == [
                "Printer",
                "Assistant",
                "Making models",
                "Finding models",
                "This machine",
            ]
        finally:
            dialog.deleteLater()

    def test_the_printer_comes_first(self, app):
        """It is the one most people open this dialog to change."""
        dialog = a_dialog()
        try:
            assert dialog.findChild(QTabWidget).tabText(0) == "Printer"
        finally:
            dialog.deleteLater()

    def test_the_printer_hint_says_where_to_look_on_a_p2s(self, app):
        """ "Settings > Network" is the X1's menu and sent the user hunting.

        The P2S puts it under General, and the code arriving as all zeros is
        common enough to be worth saying here rather than in a forum.
        """
        from PySide6.QtWidgets import QLabel

        dialog = a_dialog()
        try:
            said = " ".join(label.text() for label in dialog.findChildren(QLabel))

            assert "Settings > General > LAN-Only mode" in said
            assert "all zeros" in said
        finally:
            dialog.deleteLater()
