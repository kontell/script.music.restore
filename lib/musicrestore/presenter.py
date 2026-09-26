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
from .model import QueuePreview
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


def _row(preview: QueuePreview, tr: Translator) -> xbmcgui.ListItem:
    # offscreen=True skips the graphics lock while we build the list. Without
    # it every ListItem takes the GUI lock, which on a long history is visible.
    item = xbmcgui.ListItem(
        label=naming.preview_title(preview, tr),
        label2=naming.preview_detail(preview, tr=tr),
        offscreen=True,
    )
    if preview.thumb:
        item.setArt({"thumb": preview.thumb, "icon": preview.thumb})
    return item


def show(started: Optional[float] = None) -> None:
    """Offer the recorded queues; restore whichever is chosen."""
    t0 = started if started is not None else time.time()
    stage = time.perf_counter()
    # One Addon for the whole opening: each construction is ~3 ms, and a
    # naive per-string localised() call did that once per format string per
    # row. Icon and the two restore settings come off the same instance.
    addon = settings.addon()
    tr = settings.translator_for(addon)
    setup_ms = (time.perf_counter() - stage) * 1000.0

    def text(string_id: int) -> str:
        return tr(string_id) or FALLBACK.get(string_id, "")

    stage = time.perf_counter()
    previews = history.load_previews()
    load_ms = (time.perf_counter() - stage) * 1000.0
    if not previews:
        xbmcgui.Dialog().ok(text(HEADING), text(EMPTY_BODY))
        return

    # Kodi's select() takes strings or ListItems; the annotation has to be the
    # union it declares, not the narrower list this actually builds.
    stage = time.perf_counter()
    rows: List[Union[str, xbmcgui.ListItem]] = [
        _row(preview, tr) for preview in previews
    ]
    rows_ms = (time.perf_counter() - stage) * 1000.0
    ready_ms = (time.time() - t0) * 1000.0
    log.info("dialog ready in %.0fms (%d row(s))", ready_ms, len(rows))
    log.info(
        "dialog stages: setup %.0fms, load %.0fms, rows %.0fms",
        setup_ms,
        load_ms,
        rows_ms,
    )
    chosen = xbmcgui.Dialog().select(text(HEADING), rows, useDetails=True)
    if chosen < 0:
        return

    preview = previews[chosen]
    record = history.resolve_preview(preview)
    if record is None:
        log.error("selected queue disappeared while dialog was open")
        return
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
