"""Script entry point: show the "Recent queues" pop-up.

Reached two ways, both the same call: the skin's widget button
(``RunScript(script.music.restore)``) and running the addon from the addon
browser.
"""

import os
import sys
import time

# Taken before any addon import so the "dialog ready" log includes interpreter
# startup, not just the work after we are imported.
_STARTED = time.time()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from musicrestore import presenter  # noqa: E402

if __name__ == "__main__":
    presenter.show(started=_STARTED)
