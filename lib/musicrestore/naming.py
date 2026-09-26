"""Turning a recorded queue into the two lines a history row shows.

Pure functions over :class:`~musicrestore.model.QueueRecord` — no Kodi calls —
so the wording is unit-testable. Callers pass a translator; the English
fallbacks below are what the tests use and what renders if a string id is ever
missing from strings.po.
"""

import time
from typing import Callable, List, Optional, Tuple

from .model import QueuePreview, QueueRecord

Translator = Callable[[int], str]

# Row and time-ago wording. Ids match resources/language/*/strings.po.
TITLE_ALBUM_BY_ARTIST = 30030
TITLE_ARTIST_TRACKS = 30031
TITLE_FIRST_PLUS_MORE = 30032
TITLE_BARE_TRACKS = 30033
LINE_TRACK_COUNTS = 30034
LINE_STOPPED_AT = 30035
LINE_PLAYED_THROUGH = 30036
AGO_JUST_NOW = 30040
AGO_A_MINUTE = 30041
AGO_MINUTES = 30042
AGO_AN_HOUR = 30043
AGO_HOURS = 30044
AGO_YESTERDAY = 30045
AGO_DAYS = 30046
AGO_WEEKS = 30047

FALLBACK = {
    TITLE_ALBUM_BY_ARTIST: "{0} — {1}",
    TITLE_ARTIST_TRACKS: "{0} — {1} tracks",
    TITLE_FIRST_PLUS_MORE: "{0} + {1} more",
    TITLE_BARE_TRACKS: "{0} tracks",
    LINE_TRACK_COUNTS: "Track {0} of {1} · {2} unplayed",
    LINE_STOPPED_AT: "{0} · stopped at {1}",
    LINE_PLAYED_THROUGH: "Played through",
    AGO_JUST_NOW: "just now",
    AGO_A_MINUTE: "a minute ago",
    AGO_MINUTES: "{0} minutes ago",
    AGO_AN_HOUR: "an hour ago",
    AGO_HOURS: "{0} hours ago",
    AGO_YESTERDAY: "yesterday",
    AGO_DAYS: "{0} days ago",
    AGO_WEEKS: "{0} weeks ago",
}


def _fallback(string_id: int) -> str:
    return FALLBACK.get(string_id, "")


def _resolve(tr: Optional[Translator], string_id: int) -> str:
    """Translated string, falling back to English if the id is unset."""
    if tr is None:
        return _fallback(string_id)
    return tr(string_id) or _fallback(string_id)


def _unique(values: List[str]) -> List[str]:
    seen: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def title_parts(record: QueueRecord) -> Tuple[int, Tuple[str, ...]]:
    """A compact, language-independent recipe for the queue's title.

    Kodi keeps no name for the live playlist — there is no infolabel for it and
    ``CPlayList::m_strPlayListName`` is not reachable over JSON-RPC — so the
    only thing to go on is the tracks themselves.
    """
    if not record.tracks:
        return TITLE_BARE_TRACKS, ("0",)

    albums = _unique([track.album for track in record.tracks])
    artists = _unique([track.artist for track in record.tracks])

    # One album: the album is the queue.
    if len(albums) == 1 and all(track.album for track in record.tracks):
        if len(artists) == 1:
            return TITLE_ALBUM_BY_ARTIST, (albums[0], artists[0])
        return 0, (albums[0],)

    # One artist across several albums: name the artist and say how much.
    if len(artists) == 1 and all(track.artist for track in record.tracks):
        return TITLE_ARTIST_TRACKS, (artists[0], str(record.total))

    # A mixture. Name it for the track that was playing when it stopped —
    # that is what the listener will hear if they pick this row. Fall back
    # to the first track only when the saved position is out of range.
    playing = record.current or record.tracks[0]
    if playing.title:
        if record.total == 1:
            return 0, (playing.title,)
        return TITLE_FIRST_PLUS_MORE, (playing.title, str(record.total - 1))

    return TITLE_BARE_TRACKS, (str(record.total),)


def format_title(
    string_id: int, args: Tuple[str, ...], tr: Optional[Translator] = None
) -> str:
    if string_id == 0:
        return args[0] if args else ""
    return _resolve(tr, string_id).format(*args)


def queue_title(record: QueueRecord, tr: Optional[Translator] = None) -> str:
    """A name for the queue, derived from what is in it."""
    string_id, args = title_parts(record)
    return format_title(string_id, args, tr)


def preview_title(preview: QueuePreview, tr: Optional[Translator] = None) -> str:
    return format_title(preview.title_id, preview.title_args, tr)


def time_ago(
    when: float, now: Optional[float] = None, tr: Optional[Translator] = None
) -> str:
    """Render an age as "2 hours ago". Kodi has no relative-time formatter."""
    if now is None:
        now = time.time()
    seconds = max(0, int(now - when))

    if seconds < 60:
        return _resolve(tr, AGO_JUST_NOW)
    minutes = seconds // 60
    if minutes == 1:
        return _resolve(tr, AGO_A_MINUTE)
    if minutes < 60:
        return _resolve(tr, AGO_MINUTES).format(minutes)
    hours = minutes // 60
    if hours == 1:
        return _resolve(tr, AGO_AN_HOUR)
    if hours < 24:
        return _resolve(tr, AGO_HOURS).format(hours)
    days = hours // 24
    if days == 1:
        return _resolve(tr, AGO_YESTERDAY)
    if days < 14:
        return _resolve(tr, AGO_DAYS).format(days)
    return _resolve(tr, AGO_WEEKS).format(days // 7)


def clock(seconds: float) -> str:
    """Seconds as m:ss, or h:mm:ss once it runs past an hour."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%d:%02d" % (minutes, secs)


def _detail(
    saved: float,
    position: int,
    total: int,
    unplayed: int,
    tick: float,
    finished: bool,
    now: Optional[float],
    tr: Optional[Translator],
) -> str:
    """The two-line subtitle.

    Rendered into the detailed select dialog's Label2, which skin.contuary
    draws as a wrapping textbox — so the newline is honoured rather than
    truncated.
    """
    ago = time_ago(saved, now, tr)
    if finished:
        # Restore starts this row at the beginning, so "Track N of N ·
        # stopped at 3:45" would describe a point we will not return to.
        return "%s[CR]%s" % (_resolve(tr, LINE_PLAYED_THROUGH), ago)
    counts = _resolve(tr, LINE_TRACK_COUNTS).format(position + 1, total, unplayed)
    # Under a second in means the track had barely started; a "stopped at 0:00"
    # is noise, so the second line is just the age.
    if tick >= 1.0:
        ago = _resolve(tr, LINE_STOPPED_AT).format(ago, clock(tick))
    return "%s[CR]%s" % (counts, ago)


def queue_detail(
    record: QueueRecord, now: Optional[float] = None, tr: Optional[Translator] = None
) -> str:
    return _detail(
        record.saved,
        record.position,
        record.total,
        record.unplayed,
        record.tick,
        record.finished,
        now,
        tr,
    )


def preview_detail(
    preview: QueuePreview, now: Optional[float] = None, tr: Optional[Translator] = None
) -> str:
    return _detail(
        preview.saved,
        preview.position,
        preview.total,
        preview.unplayed,
        preview.tick,
        preview.finished,
        now,
        tr,
    )
