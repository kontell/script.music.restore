"""The recorded queues, newest first, in a JSON file with a small dialog index.

A queue is only written when one is taken away, so this is a handful of writes
an evening rather than the continuous polling ``kodi-resume`` does — a plain
file beats a database for that, and there is no schema to migrate.

The store is deliberately an append-only log rather than a set of distinct
queues: playing an album, stopping, and playing it again is meant to leave two
rows, and restoring one does not remove it. See INVESTIGATION.md §12.7.

``queues.json`` remains authoritative. ``queue_previews.json`` carries only the
fields needed to draw the list and is rebuilt if it is missing or stale.
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import xbmcvfs

from . import logging as log, naming
from .model import SCHEMA_VERSION, QueuePreview, QueueRecord

PROFILE = "special://profile/addon_data/script.music.restore/"
STORE = PROFILE + "queues.json"
PREVIEWS = PROFILE + "queue_previews.json"

# Two captures are the same event, not two listens, only if the queue, the
# track and the second within it all match. Anything coarser would merge
# genuinely separate listens; anything finer would let one user action that
# fires two notifications leave two rows.
TICK_TOLERANCE = 1.0


def _read_raw() -> Dict[str, Any]:
    if not xbmcvfs.exists(STORE):
        return {}
    try:
        with xbmcvfs.File(STORE) as handle:
            content = handle.read()
    except Exception as exc:  # noqa: BLE001 - a broken store must not kill the service
        log.error("could not read %s: %s", STORE, exc)
        return {}
    if not content:
        return {}
    try:
        data = json.loads(content)
    except ValueError as exc:
        log.error("%s is not valid JSON (%s); starting a new history", STORE, exc)
        return {}
    return data if isinstance(data, dict) else {}


def load() -> List[QueueRecord]:
    """Every recorded queue, newest first."""
    data = _read_raw()
    raw = data.get("queues")
    if not isinstance(raw, list):
        return []
    records = [QueueRecord.from_dict(item) for item in raw if isinstance(item, dict)]
    return [record for record in records if record.tracks]


def _fingerprint() -> Optional[Tuple[int, int]]:
    """Detect a stale preview cache, including manual changes to queues.json."""
    try:
        stat = os.stat(xbmcvfs.translatePath(STORE))
        return stat.st_size, stat.st_mtime_ns
    except OSError:
        return None


def _preview(record: QueueRecord) -> QueuePreview:
    title_id, title_args = naming.title_parts(record)
    current = record.current or record.tracks[0]
    return QueuePreview(
        saved=round(record.saved, 3),
        position=record.position,
        total=record.total,
        tick=round(record.tick, 3),
        finished=record.finished,
        thumb=current.thumb,
        title_id=title_id,
        title_args=title_args,
    )


def _write_previews(records: List[QueueRecord]) -> None:
    fingerprint = _fingerprint()
    if fingerprint is None:
        return
    payload = json.dumps(
        {
            "version": 1,
            "source": list(fingerprint),
            "queues": [_preview(record).to_dict() for record in records],
        }
    )
    temp = PREVIEWS + ".tmp"
    try:
        with xbmcvfs.File(temp, "w") as handle:
            handle.write(payload)
        if xbmcvfs.exists(PREVIEWS):
            xbmcvfs.delete(PREVIEWS)
        if not xbmcvfs.rename(temp, PREVIEWS):
            raise IOError("rename %s -> %s failed" % (temp, PREVIEWS))
    except Exception as exc:  # noqa: BLE001 - the full store is authoritative
        log.error("could not write queue previews: %s", exc)


def load_previews() -> List[QueuePreview]:
    """Read only the small dialog index; rebuild it for old or changed stores."""
    fingerprint = _fingerprint()
    if fingerprint is None:
        # Some VFS backends can read a profile file without exposing it to
        # os.stat. Keep the dialog usable there, even without an index.
        return [_preview(record) for record in load()]
    try:
        with xbmcvfs.File(PREVIEWS) as handle:
            data = json.loads(handle.read())
        if data.get("version") == 1 and data.get("source") == list(fingerprint):
            raw = data.get("queues")
            if isinstance(raw, list):
                return [QueuePreview.from_dict(item) for item in raw]
    except Exception:  # noqa: BLE001 - missing or damaged index is rebuilt
        pass
    records = load()
    _write_previews(records)
    return [_preview(record) for record in records]


def resolve_preview(preview: QueuePreview) -> Optional[QueueRecord]:
    """Load the chosen queue only after the dialog closes.

    A capture can arrive while the dialog is open, changing list indices, so
    match the saved record rather than using its original position in the list.
    """
    for record in load():
        if (
            record.saved == preview.saved
            and record.total == preview.total
            and record.position == preview.position
            and record.tick == preview.tick
        ):
            return record
    return None


def save(records: List[QueueRecord]) -> bool:
    """Replace the store. Returns False if it could not be written."""
    if not xbmcvfs.exists(PROFILE):
        xbmcvfs.mkdirs(PROFILE)
    payload = json.dumps(
        {
            "version": SCHEMA_VERSION,
            "queues": [record.to_dict() for record in records],
        },
        indent=1,
    )
    # Write beside the target and rename, so a crash mid-write cannot leave a
    # truncated history behind. Kodi's own shutdown is one of the moments this
    # runs, which is exactly when a half-written file would be likeliest.
    temp = STORE + ".tmp"
    try:
        with xbmcvfs.File(temp, "w") as handle:
            handle.write(payload)
        if xbmcvfs.exists(STORE):
            xbmcvfs.delete(STORE)
        if not xbmcvfs.rename(temp, STORE):
            raise IOError("rename %s -> %s failed" % (temp, STORE))
    except Exception as exc:  # noqa: BLE001
        log.error("could not write %s: %s", STORE, exc)
        return False
    _write_previews(records)
    return True


def is_duplicate(record: QueueRecord, newest: Optional[QueueRecord]) -> bool:
    """True if this capture says nothing new against the newest entry.

    Same tracks, same track playing, same second into it — nothing happened
    between the two captures, so this is one user action being reported twice
    rather than a second listen.
    """
    if newest is None:
        return False
    return (
        record.signature == newest.signature
        and record.position == newest.position
        and abs(record.tick - newest.tick) < TICK_TOLERANCE
    )


def add(record: QueueRecord, keep: int) -> bool:
    """Prepend a capture and trim to ``keep``. False if it was a duplicate."""
    records = load()
    if is_duplicate(record, records[0] if records else None):
        log.debug("skipping duplicate capture of %d tracks", record.total)
        return False
    records.insert(0, record)
    del records[keep:]
    save(records)
    return True


def count() -> int:
    return len(load())


def clear() -> bool:
    """Drop the whole history. Only used by the tests and by hand."""
    return save([])


def store_path() -> str:
    """Filesystem path of the store, for logging."""
    return os.path.join(xbmcvfs.translatePath(PROFILE), "queues.json")
