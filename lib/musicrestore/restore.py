"""Putting a recorded queue back, on the right track at the right second.

The obvious route does not work: ``Player.Open`` with ``{"playlistid": 0,
"position": n}`` lands on the right track but ignores ``options.resume`` — the
playlist branch of ``CPlayerOperations::Open`` posts ``TMSG_MEDIA_PLAY`` and
never reaches ``HandleResumeOption``. Verified on Kodi 21.3: the resume option
was accepted and playback still started at 0:00.

What does work is building the playlist through the Python API and setting
``StartOffset`` on the resume track. That maps to ``CFileItem::SetStartOffset``
and is read by the audio player as its start time, so the track opens already
seeked — no seek call afterwards, and none of the audible jump from the
beginning that a play-then-seek gives.
"""

import time
from typing import List, Union

import xbmc
import xbmcgui

from . import jsonrpc, logging as log, rebind
from .model import QueueRecord, Track, playback_start

MUSIC_PLAYLIST = 0

# Below this, resuming is not worth the offset — the track had barely started.
MIN_OFFSET = 5.0

# How long to wait for playback before pausing a queue restored paused.
PAUSE_TIMEOUT = 10.0
PAUSE_STEP = 100  # ms


def _list_item(track: Track) -> xbmcgui.ListItem:
    item = xbmcgui.ListItem(label=track.title or track.file, offscreen=True)
    item.setPath(track.file)
    tag = item.getMusicInfoTag()
    if track.title:
        tag.setTitle(track.title)
    if track.album:
        tag.setAlbum(track.album)
    if track.artist:
        tag.setArtist(track.artist)
    if track.duration:
        tag.setDuration(track.duration)
    # Carrying the library id through is what makes the restored item behave
    # like the library song it came from rather than a loose file.
    if track.songid:
        tag.setDbId(track.songid, "song")
    if track.thumb:
        item.setArt({"thumb": track.thumb, "icon": track.thumb})
    return item


def _lookup_library_song(
    jellyfin_id: str,
) -> Union[dict, None, object]:
    """The current library row for a Jellyfin audio id, or None if it is gone.

    Filters on path: Kodi stores the host URL in ``path.strPath`` and the
    ``stream.*`` leaf in ``song.strFileName``, so a filename filter misses.
    Verified live — ``field=path, operator=contains`` returns the one song.
    """
    result = jsonrpc.call(
        "AudioLibrary.GetSongs",
        filter={"field": "path", "operator": "contains", "value": jellyfin_id},
        properties=[
            "file",
            "title",
            "artist",
            "albumartist",
            "album",
            "duration",
            "thumbnail",
        ],
        limits={"end": 1},
    )
    if result is None:
        return rebind.LOOKUP_FAILED
    songs = result.get("songs") or []
    return songs[0] if songs else None


def _rebind(track: Track) -> Track:
    rebound = rebind.rebind_track(track, _lookup_library_song)
    if rebound.songid != track.songid:
        log.info(
            "rebound %s songid %s -> %s",
            rebound.title or rebound.file,
            track.songid,
            rebound.songid,
        )
    return rebound


def _pause_once_playing() -> None:
    deadline = time.time() + PAUSE_TIMEOUT
    player = xbmc.Player()
    while time.time() < deadline:
        if player.isPlayingAudio():
            # Explicit false rather than xbmc.Player().pause(), which toggles
            # and would resume if anything got there first.
            jsonrpc.call("Player.PlayPause", playerid=MUSIC_PLAYLIST, play=False)
            return
        xbmc.sleep(PAUSE_STEP)
    log.error("gave up waiting for playback to start; not pausing")


def restore(
    record: QueueRecord,
    start_paused: bool = False,
    from_track_start: bool = False,
) -> bool:
    """Load a recorded queue and start it where it left off.

    ``from_track_start`` keeps the track but drops the offset, so the resume
    track plays from 0:00 — for when the last seconds heard are worth hearing
    again rather than skipping past.
    """
    tracks: List[Track] = [_rebind(track) for track in record.tracks if track.file]
    if not tracks:
        log.error("nothing playable in this record")
        return False

    position, tick = playback_start(record, from_track_start)
    if position >= len(tracks):
        position, tick = 0, 0.0

    playlist = xbmc.PlayList(MUSIC_PLAYLIST)
    playlist.clear()
    for index, track in enumerate(tracks):
        item = _list_item(track)
        if index == position and tick >= MIN_OFFSET:
            item.setProperty("StartOffset", str(tick))
        playlist.add(track.file, item)

    log.info(
        "restoring %d track(s) from position %d at %.0fs%s%s",
        len(tracks),
        position,
        tick,
        (
            " (from the start of the track)"
            if from_track_start and not record.finished
            else ""
        ),
        " (paused)" if start_paused else "",
    )
    xbmc.Player().play(playlist, startpos=position)

    if start_paused:
        _pause_once_playing()
    return True
