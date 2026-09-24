"""The application's icon, and the taskbar identity that makes Windows use it.

Worth a test for one reason: the icon file is data inside the package, and
data inside a package is exactly the thing that survives every test and then
goes missing from an install. If the file stops shipping, this says so.
"""

import sys

import pytest
from PySide6.QtWidgets import QApplication

from modelpop.ui.branding import APP_ID, claim_the_taskbar, icon, icon_file


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


class TestTheIconFile:
    def test_it_ships_with_the_package(self):
        assert icon_file().is_file(), (
            f"{icon_file()} is missing - the icon is package data and has to be "
            "included by the build, not just present in the working tree"
        )

    def test_it_lives_inside_the_package_rather_than_beside_the_repo(self):
        """An installed wheel has no repository around it."""
        parts = icon_file().parts
        assert "modelpop" in parts
        assert parts[-2] == "resources"

    @pytest.mark.skipif(sys.platform != "win32", reason="the .ico is for Windows")
    def test_windows_gets_the_multi_size_ico(self):
        assert icon_file().suffix == ".ico"


class TestTheIcon:
    def test_it_loads(self, app):
        assert not icon().isNull(), "the icon file is there but Qt could not read it"

    def test_it_carries_the_small_sizes_a_taskbar_asks_for(self, app):
        """A single 256px image scaled down by the window manager looks poor.

        The file holds every size from 16 up, and this checks Qt found them
        rather than silently keeping one.
        """
        available = {size.width() for size in icon().availableSizes()}
        assert {16, 32} <= available, f"only these sizes are in the icon: {sorted(available)}"

    def test_asking_twice_gives_the_same_object(self, app):
        """Cached, because Qt decodes every size in the file each time."""
        assert icon() is icon()


class TestTheTaskbarIdentity:
    def test_it_is_claimed_on_windows(self):
        """Without this the taskbar shows pythonw.exe's icon, whatever the
        window itself is set to."""
        assert claim_the_taskbar() is (sys.platform == "win32")

    def test_the_identity_is_the_shape_windows_documents(self):
        """Vendor.Product.Component.Version. It only has to be stable and
        unique - changing it orphans any pinned shortcut."""
        assert APP_ID.count(".") == 3
        assert " " not in APP_ID
