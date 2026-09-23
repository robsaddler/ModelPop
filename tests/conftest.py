"""Settings that must be in place before anything imports Qt.

One thing only: Qt is told to run without a window system unless the developer
has asked for something else. Every Qt test here builds widgets and inspects
them rather than looking at them, and a test that pops a window on each run is
one people stop running.

The `renders` marker still exists for the one test that actually rasterises
pixels. That is a different problem - VTK does not fail on a GPU-less runner, it
takes the process down - and no environment variable fixes it.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
