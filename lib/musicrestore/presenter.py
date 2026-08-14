"""The "Recent queues" pop-up.

``Dialog().select(useDetails=True)`` switches Kodi to the select dialog's
detailed list (``CONTROL_DETAILED_LIST``, id 6), which skins draw as a thumb
plus a label and a wrapping Label2 — in skin.contuary a 110x110 thumbnail, the
label in font14, and Label2 as a textbox, which is what lets each row carry two
lines of detail rather than one.
"""

import time
from typing import List, Optional, Union

import xbmcgui

from . import history, logging as log, naming, settings
from .model import QueueRecord
from .naming import Translator

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


def _row(record: QueueRecord, tr: Translator) -> xbmcgui.ListItem:
    # offscreen=True skips the graphics lock while we build the list. Without
    # it every ListItem takes the GUI lock, which on a long history is visible.
    item = xbmcgui.ListItem(
        label=naming.queue_title(record, tr),
        label2=naming.queue_detail(record, tr=tr),
        offscreen=True,
    )
    current = record.current or (record.tracks[0] if record.tracks else None)
    if current and current.thumb:
        item.setArt({"thumb": current.thumb, "icon": current.thumb})
    return item


def show(started: Optional[float] = None) -> None:
    """Offer the recorded queues; restore whichever is chosen."""
    t0 = started if started is not None else time.time()
    # One Addon for the whole opening: each construction is ~3 ms, and a
    # naive per-string localised() call did that once per format string per
    # row. Icon and the two restore settings come off the same instance.
    addon = settings.addon()
    tr = settings.translator_for(addon)

    def text(string_id: int) -> str:
        return tr(string_id) or FALLBACK.get(string_id, "")

    records = history.load()
    if not records:
        xbmcgui.Dialog().ok(text(HEADING), text(EMPTY_BODY))
        return

    # Kodi's select() takes strings or ListItems; the annotation has to be the
    # union it declares, not the narrower list this actually builds.
    rows: List[Union[str, xbmcgui.ListItem]] = [_row(record, tr) for record in records]
    ready_ms = (time.time() - t0) * 1000.0
    log.info("dialog ready in %.0fms (%d row(s))", ready_ms, len(rows))
    chosen = xbmcgui.Dialog().select(text(HEADING), rows, useDetails=True)
    if chosen < 0:
        return

    record = records[chosen]
    try:
        icon = str(addon.getAddonInfo("icon"))
    except Exception:  # noqa: BLE001
        icon = ""
    # The addon's own icon rather than NOTIFICATION_INFO's generic "i": the
    # toast is this addon speaking, and the mark is the one on the button that
    # opened the dialog. Falls back to the stock icon if the path comes back
    # empty, since notification() renders nothing for an empty string.
    xbmcgui.Dialog().notification(
        text(RESTORING),
        naming.queue_title(record, tr),
        icon or xbmcgui.NOTIFICATION_INFO,
        2000,
    )
    # Restoring deliberately leaves the entry in place: the history is a log of
    # every time a queue was taken away, not a set of distinct queues. The
    # restored queue is captured again, as a new row, the next time it goes.
    # Imported here so opening the list does not pay for the player path.
    from . import restore

    try:
        start_paused = bool(addon.getSettingBool("startPaused"))
    except Exception:  # noqa: BLE001
        start_paused = False
    try:
        from_track_start = bool(addon.getSettingBool("fromTrackStart"))
    except Exception:  # noqa: BLE001
        from_track_start = False
    restore.restore(record, start_paused, from_track_start)
