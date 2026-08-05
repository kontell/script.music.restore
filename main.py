"""Plugin entry point: the browsable list the library node points at.

Separate from default.py, which is the pop-up. Both are declared in addon.xml —
the script extension first, so RunScript and the addon browser keep resolving to
the pop-up, with plugin:// coming here.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from musicrestore import plugin  # noqa: E402

if __name__ == "__main__":
    plugin.main()
