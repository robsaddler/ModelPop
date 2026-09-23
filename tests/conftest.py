"""Settings that must be in place before anything imports Qt, and shared gates.

Two things only.

Qt is told to run without a window system unless the developer has asked for
something else. Every Qt test here builds widgets and inspects them rather than
looking at them, and a test that pops a window on each run is one people stop
running.

And the "is the CAD kernel installed" question is answered **once, lazily**.
Asking it starts an interpreter and imports build123d, which is slow; asking it
at module scope in three different files - which is what a plain
``pytest.mark.skipif`` condition does - made the *fast* suite pay for it three
times over before a single test ran. Measured: 7.8 seconds of an 22.4 second
run was collection, almost all of it this.

The `renders` marker still exists for the tests that actually rasterise pixels.
That is a different problem - VTK does not fail on a GPU-less runner, it takes
the process down - and no environment variable fixes it.
"""

import os
from functools import cache

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@cache
def kernel_is_available() -> bool:
    """Whether build123d can be imported by a worker interpreter.

    Cached, so the subprocess starts at most once per test session however many
    tests ask.
    """
    from modelpop.cad import Build123dKernel

    return Build123dKernel().is_available()


# A string condition rather than a boolean: pytest evaluates it when the test
# is set up, not when the module is imported, which is the whole point. The
# name is resolved against the test module's globals, so each file that uses
# this imports `kernel_is_available` alongside it.
kernel_required = pytest.mark.skipif(
    "not kernel_is_available()", reason="build123d is not installed"
)
