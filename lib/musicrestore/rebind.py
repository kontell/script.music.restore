"""Re-attach a recorded track to the current music library.

A kofin library repair deletes songs and inserts them again. ``idSong`` is
``INTEGER PRIMARY KEY`` without ``AUTOINCREMENT``, so the new rows reuse the
old numbers — often for different tracks, because a repair walks newest-first.
The file URL still names the Jellyfin item (``/Audio/<id>/`` or
``plugin://plugin.video.kofin/<library>/<id>/``), which survives the rebuild.

Look that id up in the live library and rewrite ``songid`` and ``file`` before
we play. A stale ``setDbId`` would otherwise stamp the restored item onto
whoever now occupies the old number — play counts, the info dialog, and
anything else reading the music tag follow the wrong row. The stream URL
itself is still right; the library link is what breaks.
"""

import re
from typing import Any, Callable, Dict, Optional, Union

from .model import Track

# Returned by a lookup that could not ask Kodi (JSON-RPC failed). Distinct
# from None, which means "asked, and this id is not in the library".
LOOKUP_FAILED = object()

Lookup = Callable[[str], Union[Dict[str, Any], None, object]]

# Jellyfin audio item ids are 32 hex chars. Direct paths put them under
# /Audio/<id>/; the plugin form puts them as the last path segment.
_JELLYFIN_AUDIO = re.compile(
    r"(?:/Audio/|plugin://plugin\.video\.kofin/[^/]+/)([0-9a-fA-F]{32})",
)


def jellyfin_audio_id(file: str) -> Optional[str]:
    """The Jellyfin item id baked into a kofin/Jellyfin song URL, or None."""
    match = _JELLYFIN_AUDIO.search(file or "")
    return match.group(1).lower() if match else None


def apply_library_hit(track: Track, song: Dict[str, Any]) -> Track:
    """Copy the live library row onto a recorded track.

    Display fields already on the record win: they are what the history row
    showed. ``file`` and ``songid`` come from the live row — those are what
    playback and the music tag must match after a rebuild.
    """
    payload = dict(song)
    if "id" not in payload and payload.get("songid"):
        payload["id"] = payload["songid"]
    payload.setdefault("type", "song")
    live = Track.from_playlist_item(payload)
    return Track(
        file=live.file or track.file,
        title=track.title or live.title,
        artist=track.artist or live.artist,
        album=track.album or live.album,
        duration=track.duration or live.duration,
        songid=live.songid,
        thumb=track.thumb or live.thumb,
        fanart=track.fanart or live.fanart,
    )


def rebind_track(track: Track, lookup: Lookup) -> Track:
    """Return ``track`` pointed at the live library row, if one still exists.

    ``lookup`` takes a Jellyfin item id and returns a GetSongs row, or None
    when the song is gone. A URL that is not a Jellyfin audio path is left
    alone — local files keep their stored songid. A Jellyfin path that no
    longer resolves drops the songid so we play the stored URL as a loose
    file rather than attaching a reused id to the wrong row.
    """
    item_id = jellyfin_audio_id(track.file)
    if not item_id:
        return track
    hit = lookup(item_id)
    if hit is LOOKUP_FAILED:
        return track
    if isinstance(hit, dict):
        rebound = apply_library_hit(track, hit)
        if rebound.songid != track.songid or rebound.file != track.file:
            return rebound
        return track
    if track.songid:
        return Track(
            file=track.file,
            title=track.title,
            artist=track.artist,
            album=track.album,
            duration=track.duration,
            songid=None,
            thumb=track.thumb,
            fanart=track.fanart,
        )
    return track
