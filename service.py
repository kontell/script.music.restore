"""Service entry point: record the music queue whenever it is taken away."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from musicrestore.recorder import Recorder  # noqa: E402

if __name__ == "__main__":
    Recorder().run()
