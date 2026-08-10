"""The "Recent queues" pop-up.

``Dialog().select(useDetails=True)`` switches Kodi to the select dialog's
detailed list (``CONTROL_DETAILED_LIST``, id 6), which skins draw as a thumb
plus a label and a wrapping Label2 — in skin.contuary a 110x110 thumbnail, the
label in font14, and Label2 as a textbox, which is what lets each row carry two
lines of detail rather than one.
"""

from typing import List, Union

import xbmcgui

from . import history, naming, restore, settings
from .model import QueueRecord

# 30010 is the button's label and stays "Recent queues" — the skin references it
# by id. What the dialog itself is called is a separate string, because the row
# you are looking at is named for what picking it does.
HEADING = 30011
EMPTY_BODY = 30012
RESTORING = 30013

FALLBACK = {
    HEADING: "Restore queue",
    EMPTY_BODY: "Nothing has been set aside yet. A queue is saved whenever one is replaced, interrupted or stopped.",
    RESTORING: "Restoring",
}


def _text(string_id: int) -> str:
    return settings.localised(string_id) or FALLBACK.get(string_id, "")


def _row(record: QueueRecord) -> xbmcgui.ListItem:
    item = xbmcgui.ListItem(
        label=naming.queue_title(record, settings.localised),
        label2=naming.queue_detail(record, tr=settings.localised),
    )
    current = record.current or (record.tracks[0] if record.tracks else None)
    if current and current.thumb:
        item.setArt({"thumb": current.thumb, "icon": current.thumb})
    return item


def show() -> None:
    """Offer the recorded queues; restore whichever is chosen."""
    records = history.load()
    if not records:
        xbmcgui.Dialog().ok(_text(HEADING), _text(EMPTY_BODY))
        return

    # Kodi's select() takes strings or ListItems; the annotation has to be the
    # union it declares, not the narrower list this actually builds.
    rows: List[Union[str, xbmcgui.ListItem]] = [_row(record) for record in records]
    chosen = xbmcgui.Dialog().select(_text(HEADING), rows, useDetails=True)
    if chosen < 0:
        return

    record = records[chosen]
    # The addon's own icon rather than NOTIFICATION_INFO's generic "i": the
    # toast is this addon speaking, and the mark is the one on the button that
    # opened the dialog. Falls back to the stock icon if the path comes back
    # empty, since notification() renders nothing for an empty string.
    xbmcgui.Dialog().notification(
        _text(RESTORING),
        naming.queue_title(record, settings.localised),
        settings.icon() or xbmcgui.NOTIFICATION_INFO,
        2000,
    )
    # Restoring deliberately leaves the entry in place: the history is a log of
    # every time a queue was taken away, not a set of distinct queues. The
    # restored queue is captured again, as a new row, the next time it goes.
    restore.restore(record, settings.start_paused(), settings.from_track_start())
