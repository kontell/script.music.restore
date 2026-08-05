"""The browsable side of the addon — what the library node points at.

``plugin://script.music.restore/`` lists the recorded queues; selecting one
restores it. Action items follow the convention used elsewhere in this codebase
(plugin.audio.koshelf): added as a non-folder directory item, and the route that
handles them ends with ``succeeded=False`` so the listing is not replaced by an
empty page.

The pop-up is unaffected. It is still what ``RunScript(script.music.restore)``
and running the addon give you; this is a second surface, not a replacement.
"""

import sys
from typing import List
from urllib.parse import parse_qsl, urlencode

import xbmcgui
import xbmcplugin

from . import history, logging as log, naming, restore, settings
from .model import QueueRecord

BASE = "plugin://script.music.restore/"

CATEGORY = 30010  # "Recent queues"
EMPTY_ROW = 30014


def _url(**params: object) -> str:
    return BASE + "?" + urlencode(params)


def _row(record: QueueRecord) -> xbmcgui.ListItem:
    item = xbmcgui.ListItem(
        label=naming.queue_title(record, settings.localised),
        label2=naming.queue_detail(record, tr=settings.localised),
    )
    current = record.current or (record.tracks[0] if record.tracks else None)
    if current and current.thumb:
        item.setArt({"thumb": current.thumb, "icon": current.thumb})
    # Same two lines the pop-up shows, so the surfaces read alike. Plot is what
    # most skins put in the info pane for a non-media item.
    item.setInfo("music", {"title": item.getLabel(), "comment": item.getLabel2()})
    return item


def _list_queues(handle: int) -> None:
    records: List[QueueRecord] = history.load()
    xbmcplugin.setPluginCategory(
        handle, settings.localised(CATEGORY) or "Recent queues"
    )

    if not records:
        # A directory cannot be empty and still be navigable, so say why.
        empty = xbmcgui.ListItem(
            label=settings.localised(EMPTY_ROW) or "Nothing set aside yet"
        )
        empty.setProperty("IsPlayable", "false")
        xbmcplugin.addDirectoryItem(handle, _url(noop=1), empty, isFolder=False)
    else:
        for index, record in enumerate(records):
            xbmcplugin.addDirectoryItem(
                handle, _url(restore=index), _row(record), isFolder=False
            )

    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


def _restore(handle: int, index: int) -> None:
    records = history.load()
    if 0 <= index < len(records):
        restore.restore(records[index], settings.start_paused())
    else:
        log.error("no queue at index %d", index)
    # Leave the listing where it is: this was an action, not navigation.
    xbmcplugin.endOfDirectory(
        handle, succeeded=False, updateListing=False, cacheToDisc=False
    )


def run(argv: List[str]) -> None:
    handle = int(argv[1])
    params = dict(parse_qsl(argv[2][1:])) if len(argv) > 2 and argv[2] else {}

    if "restore" in params:
        try:
            index = int(params["restore"])
        except ValueError:
            log.error("bad restore index %r", params["restore"])
            index = -1
        _restore(handle, index)
    elif "noop" in params:
        xbmcplugin.endOfDirectory(
            handle, succeeded=False, updateListing=False, cacheToDisc=False
        )
    else:
        _list_queues(handle)


def main() -> None:
    run(sys.argv)
