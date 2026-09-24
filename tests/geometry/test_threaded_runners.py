"""The runners that keep the window responsive.

In the fast suite on purpose. These build Qt objects but draw nothing, so they
run anywhere with the offscreen platform that ``conftest`` sets.

They exist because both runners were written with a bug that produces **no
symptom at all**: the worker is collected the moment the call returns, the
queued connection dies with it, and the thread starts, runs an empty event loop
and waits forever. No exception, no output, nothing in a log. A regression here
has to be caught by a test, because nothing else will catch it.
"""

import time

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QWidget

from modelpop.ui.background import BackgroundRunner
from modelpop.ui.gallery import _ThreadedRunner


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


@pytest.fixture
def owner(app):
    widget = QWidget()
    yield widget
    widget.deleteLater()


def wait_for(predicate, seconds: float = 5.0) -> bool:
    """Pump the event loop until something becomes true."""
    end = time.time() + seconds
    while time.time() < end:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.parametrize("runner_type", [BackgroundRunner, _ThreadedRunner])
def test_the_work_actually_runs(owner, runner_type):
    """The bug this catches: the thread starts and the work never happens."""
    done: list[int] = []
    runner = runner_type(owner)
    runner(lambda: done.append(1))

    assert wait_for(lambda: bool(done)), "the work never ran"


@pytest.mark.parametrize("runner_type", [BackgroundRunner, _ThreadedRunner])
def test_the_thread_is_released_afterwards(owner, runner_type):
    """A runner that never lets go leaks a thread per click."""
    runner = runner_type(owner)
    runner(lambda: None)

    assert wait_for(lambda: runner.running == 0), "the thread was never released"


@pytest.mark.parametrize("runner_type", [BackgroundRunner, _ThreadedRunner])
def test_work_that_raises_still_releases_the_thread(owner, runner_type):
    """Otherwise one failure locks the interface for the rest of the session."""

    def explode() -> None:
        raise RuntimeError("something went wrong in a worker")

    runner = runner_type(owner)
    runner(explode)

    assert wait_for(lambda: runner.running == 0), "a raising worker leaked its thread"


@pytest.mark.parametrize("runner_type", [BackgroundRunner, _ThreadedRunner])
def test_several_pieces_of_work_all_run(owner, runner_type):
    runner = runner_type(owner)
    done: list[int] = []
    for index in range(5):
        runner(lambda i=index: done.append(i))

    assert wait_for(lambda: len(done) == 5), f"only {len(done)} of 5 ran"


@pytest.mark.parametrize("runner_type", [BackgroundRunner, _ThreadedRunner])
def test_the_work_does_not_run_on_the_interface_thread(owner, runner_type):
    """If it did, the window would freeze for as long as the work takes."""
    import threading

    seen: list[int] = []
    runner = runner_type(owner)
    runner(lambda: seen.append(threading.get_ident()))

    assert wait_for(lambda: bool(seen))
    assert seen[0] != threading.get_ident()
