"""Nothing in the interface may be given a bound method to call back on.

A view-model, and ``PrinterMonitor`` with it, announces from whichever thread
did the work - and the work is on a worker, always, because that is the rule.
So a listener registered as ``self._something`` is a widget being driven from a
thread that has no business touching it. Qt does not report this. It corrupts,
deadlocks, or takes the process down.

**This has now bitten three times**, each in a new file, each found by a user
rather than by a test:

* ``MainWindow`` was wired straight to bound methods; a rebuild drew geometry
  wrong and the next orbit deadlocked the process - fifty-seven threads in Wait.
* The gallery handed ``download_selected`` a callback that closed a modal and
  opened a file from the worker.
* ``MonitorDialog`` registered ``self._show`` with the monitor, and calling
  ``QProgressBar.setValue`` from the polling thread **crashed the application
  mid-print**. Only mid-print: idle polls leave the bar hidden, and a hidden
  bar asks for no repaint.

The runtime test in ``tests/geometry/test_thread_affinity.py`` covers the main
window only, which is why the third one got through. This one reads the source
of the whole interface package, so a new file is covered the day it is written
rather than the day somebody reports a crash.
"""

import ast
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[2] / "src" / "modelpop" / "ui"

# Every way of asking to be told something later. Each one is handed a callable
# that will be invoked from whichever thread did the work.
REGISTRARS = frozenset(
    {
        "on_change",
        "on_state",
        "on_state_changed",
        "on_busy",
        "on_busy_changed",
        "on_outcome",
        "on_notification",
    }
)


def registrations() -> list[tuple[Path, int, str, str]]:
    """Every listener registration in the interface, as (file, line, call, argument)."""
    found: list[tuple[Path, int, str, str]] = []
    for source in sorted(UI.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in REGISTRARS or not node.args:
                continue
            found.append((source, node.lineno, node.func.attr, ast.unparse(node.args[0])))
    return found


def test_there_are_registrations_to_check():
    """A test that silently checks nothing is worse than no test."""
    assert len(registrations()) >= 8


@pytest.mark.parametrize(
    ("source", "line", "call", "argument"),
    registrations(),
    ids=lambda value: value.name if isinstance(value, Path) else str(value),
)
def test_every_listener_is_a_signal_emit(source, line, call, argument):
    """The one rule. A lambda is no better than a method - both run there."""
    assert argument.endswith(".emit"), (
        f"{source.name}:{line} gives {call}() `{argument}`, which will run on the "
        "worker thread and touch widgets from it. Emit a Qt signal instead."
    )
