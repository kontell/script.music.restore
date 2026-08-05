"""Turning a recorded queue into the two lines a history row shows.

Pure functions over :class:`~musicrestore.model.QueueRecord` — no Kodi calls —
so the wording is unit-testable. Callers pass a translator; the English
fallbacks below are what the tests use and what renders if a string id is ever
missing from strings.po.
"""

import time
from typing import Callable, List, Optional

from .model import QueueRecord

Translator = Callable[[int], str]

# Row and time-ago wording. Ids match resources/language/*/strings.po.
TITLE_ALBUM_BY_ARTIST = 30030
TITLE_ARTIST_TRACKS = 30031
TITLE_FIRST_PLUS_MORE = 30032
TITLE_BARE_TRACKS = 30033
LINE_TRACK_COUNTS = 30034
LINE_STOPPED_AT = 30035
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


def queue_title(record: QueueRecord, tr: Optional[Translator] = None) -> str:
    """A name for the queue, derived from what is in it.

    Kodi keeps no name for the live playlist — there is no infolabel for it and
    ``CPlayList::m_strPlayListName`` is not reachable over JSON-RPC — so the
    only thing to go on is the tracks themselves.
    """
    if not record.tracks:
        return _resolve(tr, TITLE_BARE_TRACKS).format(0)

    albums = _unique([track.album for track in record.tracks])
    artists = _unique([track.artist for track in record.tracks])

    # One album: the album is the queue.
    if len(albums) == 1 and all(track.album for track in record.tracks):
        if len(artists) == 1:
            return _resolve(tr, TITLE_ALBUM_BY_ARTIST).format(albums[0], artists[0])
        return albums[0]

    # One artist across several albums: name the artist and say how much.
    if len(artists) == 1 and all(track.artist for track in record.tracks):
        return _resolve(tr, TITLE_ARTIST_TRACKS).format(artists[0], record.total)

    # A mixture. Lead with the first track, which is what the listener started.
    first = record.tracks[0].title
    if first:
        if record.total == 1:
            return first
        return _resolve(tr, TITLE_FIRST_PLUS_MORE).format(first, record.total - 1)

    return _resolve(tr, TITLE_BARE_TRACKS).format(record.total)


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


def queue_detail(
    record: QueueRecord, now: Optional[float] = None, tr: Optional[Translator] = None
) -> str:
    """The two-line subtitle.

    Rendered into the detailed select dialog's Label2, which skin.contuary
    draws as a wrapping textbox — so the newline is honoured rather than
    truncated.
    """
    counts = _resolve(tr, LINE_TRACK_COUNTS).format(
        record.position + 1, record.total, record.unplayed
    )
    ago = time_ago(record.saved, now, tr)
    # Under a second in means the track had barely started; a "stopped at 0:00"
    # is noise, so the second line is just the age.
    if record.tick >= 1.0:
        ago = _resolve(tr, LINE_STOPPED_AT).format(ago, clock(record.tick))
    return "%s[CR]%s" % (counts, ago)
