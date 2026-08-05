"""Script entry point: show the "Recent queues" pop-up.

Reached two ways, both the same call: the skin's widget button
(``RunScript(script.music.restore)``) and running the addon from the addon
browser.

The exception is the two skin-edit buttons in the addon settings, which pass
an argument (``RunScript(script.music.restore, skin_button=add)``) and land in
:mod:`musicrestore.skinpatch` instead.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from musicrestore import presenter, skinpatch  # noqa: E402

if __name__ == "__main__":
    args = dict(arg.split("=", 1) for arg in sys.argv[1:] if "=" in arg)
    if "skin_button" in args:
        skinpatch.run_interactive(args["skin_button"])
    else:
        presenter.show()
