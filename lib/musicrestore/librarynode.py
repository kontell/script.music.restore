"""The "Recent queues" entry in Kodi's music library.

The music categories row on a skin's home screen is fed by ``library://music/``,
which is a directory of node XML files. Dropping one in puts an entry there
natively — in any skin, with no skin edit — which is the only way an addon can
place itself in that row.

The catch is ``CLibraryDirectory::GetNode``: it reads the *profile's*
``library/music/`` folder if one exists and otherwise falls back to Kodi's
defaults in ``special://xbmc/system/library/music/``. It does not merge them.
So writing a single node into an empty profile folder would replace Artists,
Albums, Songs and the rest with just this one entry. When the folder has to be
created, Kodi's own defaults are copied in first.
"""

from typing import List

import xbmcvfs

from . import logging as log

SYSTEM_NODES = "special://xbmc/system/library/music/"
PROFILE_NODES = "special://profile/library/music/"
NODE_NAME = "recentqueues.xml"
NODE_PATH = PROFILE_NODES + NODE_NAME

ICON = "special://home/addons/script.music.restore/resources/node.png"

# Sorted by SortBy::PLAYLIST_ORDER, so this decides where the entry lands in the
# row. Kodi's own nodes run from 10 (Genres) to 150 (Music add-ons); sitting
# just before Genres puts it first without needing every default renumbered.
NODE_ORDER = 5

# Library node labels are resolved as global Kodi string ids or literal text —
# an addon's own 30xxx ids are not in scope here, so this stays English.
NODE_LABEL = "Recent queues"

NODE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<node order="{order}" type="folder">
\t<label>{label}</label>
\t<icon>{icon}</icon>
\t<path>plugin://script.music.restore/</path>
</node>
"""


def _write(path: str, content: str) -> bool:
    try:
        with xbmcvfs.File(path, "w") as handle:
            handle.write(content)
    except Exception as exc:  # noqa: BLE001 - a failed node must not stop the service
        log.error("could not write %s: %s", path, exc)
        return False
    return True


def _seed_defaults() -> bool:
    """Copy Kodi's stock music nodes into the profile.

    Only called when the profile has no ``library/music/`` of its own. Without
    this the profile would be left with one node and no Artists or Albums.
    """
    log.info(
        "no profile music nodes yet; seeding Kodi's defaults into %s", PROFILE_NODES
    )
    if not xbmcvfs.mkdirs(PROFILE_NODES):
        log.error("could not create %s", PROFILE_NODES)
        return False

    dirs, files = xbmcvfs.listdir(SYSTEM_NODES)
    for name in files:
        if not xbmcvfs.copy(SYSTEM_NODES + name, PROFILE_NODES + name):
            log.error("could not copy default node %s", name)
            return False
    # Sub-folders (top100, musicroles, musicvideos) are nodes too.
    for name in dirs:
        source = "%s%s/" % (SYSTEM_NODES, name)
        target = "%s%s/" % (PROFILE_NODES, name)
        xbmcvfs.mkdirs(target)
        _, nested = xbmcvfs.listdir(source)
        for child in nested:
            if not xbmcvfs.copy(source + child, target + child):
                log.error("could not copy default node %s/%s", name, child)
                return False
    log.info("seeded %d default node file(s)", len(files))
    return True


def install() -> bool:
    """Put the node in place. Returns True if it is there afterwards."""
    if xbmcvfs.exists(NODE_PATH):
        return True

    if not xbmcvfs.exists(PROFILE_NODES) and not _seed_defaults():
        log.error(
            "not installing the library node: the profile nodes are not safe to touch"
        )
        return False

    xml = NODE_XML.format(order=NODE_ORDER, label=NODE_LABEL, icon=ICON)
    if not _write(NODE_PATH, xml):
        return False
    log.info("installed the library node at %s", NODE_PATH)
    return True


def remove() -> bool:
    """Take the node out again, leaving the rest of the profile's nodes alone."""
    if not xbmcvfs.exists(NODE_PATH):
        return True
    if not xbmcvfs.delete(NODE_PATH):
        log.error("could not delete %s", NODE_PATH)
        return False
    log.info("removed the library node")
    return True


def sync(wanted: bool) -> bool:
    """Install or remove the node to match the setting."""
    return install() if wanted else remove()


def default_node_names() -> List[str]:
    """Kodi's stock music node files. Exposed for the tests."""
    _, files = xbmcvfs.listdir(SYSTEM_NODES)
    return sorted(files)
