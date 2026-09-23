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

The playlist is built from the record and started immediately. Searching the
music library for every track first is what made a long queue sit there: one
``AudioLibrary.GetSongs`` path scan per song, each opening the database and
filling an art map, before ``play`` was called. The stored song id is checked
with ``GetSongDetails`` for the track about to play, and a path scan only when
that id now names a different song. Later tracks wait until they are next.
"""

import time
from dataclasses import dataclass
from typing import Dict, List, Optional

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

# Set on a queued item whose song id has not been checked against the library
# yet. The candidate id rides alongside it: stamping that id as the dbid
# before the check is how a repaired library gets the wrong song.
PENDING = "musicrestore.pending"
SONGID = "musicrestore.songid"


@dataclass
class Confirmed:
    """A track after one attempt to point it at the live library row."""

    track: Track
    art: Dict[str, str]
    # False when Kodi could not be asked. The item stays pending and is tried
    # again; a settled miss drops the song id instead of keeping a stale one.
    settled: bool
    lookups: int


def _apply_art(
    item: xbmcgui.ListItem, track: Track, library_art: Dict[str, str]
) -> None:
    art: Dict[str, str] = dict(library_art)
    # What the record carries wins, for the same reason it wins in
    # ``rebind.apply_library_hit``: it is what the history row showed.
    thumb = track.thumb or item.getArt("thumb")
    fanart = track.fanart or item.getArt("fanart")
    if thumb:
        art["thumb"] = thumb
        art["icon"] = thumb
    if fanart:
        art["fanart"] = fanart
    if art:
        item.setArt(art)


def _list_item(
    track: Track,
    library_art: Dict[str, str],
    stamp_songid: bool,
    pending: bool,
) -> xbmcgui.ListItem:
    item = xbmcgui.ListItem(label=track.title or track.file, offscreen=True)
    item.setPath(track.file)
    # ``setInfo`` is deprecated, and it is the call that marks the music tag
    # loaded. The InfoTagMusic setters do not, and an unloaded tag makes Kodi
    # open the music database and then the file for every queued item the
    # first time the player asks about it. One label is enough to set the
    # flag, so mediatype is always present.
    info = {"mediatype": "song"}
    if track.title:
        info["title"] = track.title
    if track.album:
        info["album"] = track.album
    if track.artist:
        info["artist"] = track.artist
    if track.duration:
        info["duration"] = str(track.duration)
    if stamp_songid and track.songid:
        info["dbid"] = str(track.songid)
    item.setInfo("music", info)
    _apply_art(item, track, library_art)
    if track.songid:
        item.setProperty(SONGID, str(track.songid))
    if pending:
        item.setProperty(PENDING, "1")
    return item


def _scan(item_id: str, with_art: bool) -> object:
    """The current library row for a Jellyfin audio id.

    Returns the song dict, None when the library was asked and the song is
    gone, or :data:`rebind.LOOKUP_FAILED` when it could not be asked.

    Filters on path: Kodi stores the host URL in ``path.strPath`` and the
    ``stream.*`` leaf in ``song.strFileName``, so a filename filter misses.
    ``includesingles`` is set because an absent value makes ``GetSongs`` skip
    singles, and a repaired single would then look deleted.
    """
    properties = ["file", "art"] if with_art else ["file"]
    result, error = jsonrpc.invoke(
        "AudioLibrary.GetSongs",
        filter={"field": "path", "operator": "contains", "value": item_id},
        properties=properties,
        includesingles=True,
        limits={"end": 1},
    )
    if error is not None or result is None:
        return rebind.LOOKUP_FAILED
    songs = result.get("songs") if isinstance(result, dict) else None
    if not songs:
        return None
    return songs[0] if isinstance(songs[0], dict) else None


def confirm_track(track: Track, with_art: bool) -> Confirmed:
    """Point ``track`` at the live library row, if it has moved.

    A stored song id is one primary-key read. The path scan runs only when
    that id is missing or now belongs to a different Jellyfin item. ``art``
    is filled only when ``with_art`` is set — the track that is about to
    play, not the rest of the queue.
    """
    item_id = rebind.jellyfin_audio_id(track.file)
    if not item_id:
        return Confirmed(track, {}, True, 0)

    lookups = 0
    if track.songid:
        lookups += 1
        result, error = jsonrpc.invoke(
            "AudioLibrary.GetSongDetails",
            songid=track.songid,
            properties=["file", "art"] if with_art else ["file"],
        )
        song: Optional[dict] = None
        if error is None and isinstance(result, dict):
            raw = result.get("songdetails")
            song = raw if isinstance(raw, dict) else None
        if song is not None and rebind.same_library_song(
            track, str(song.get("file") or "")
        ):
            art = rebind.art_map(song) if with_art else {}
            return Confirmed(rebind.apply_library_hit(track, song), art, True, lookups)
        # The id is gone, or it was reused. Fall through to the path scan.
        # An error here is not fatal on its own: the scan is the second ask,
        # and if that cannot be made either, the track stays unsettled.

    lookups += 1
    found = _scan(item_id, with_art)
    if found is rebind.LOOKUP_FAILED:
        return Confirmed(track, {}, False, lookups)
    if isinstance(found, dict):
        art = rebind.art_map(found) if with_art else {}
        return Confirmed(rebind.apply_library_hit(track, found), art, True, lookups)
    return Confirmed(
        Track(
            file=track.file,
            title=track.title,
            artist=track.artist,
            album=track.album,
            duration=track.duration,
            songid=None,
            thumb=track.thumb,
            fanart=track.fanart,
        ),
        {},
        True,
        lookups,
    )


def _note_rebind(before: Track, after: Track) -> None:
    if after.songid != before.songid:
        log.info(
            "rebound %s songid %s -> %s",
            after.title or after.file,
            before.songid,
            after.songid,
        )


def _track_from_item(item: xbmcgui.ListItem) -> Track:
    tag = item.getMusicInfoTag()
    raw = item.getProperty(SONGID)
    songid: Optional[int] = int(raw) if raw.isdigit() else None
    return Track(
        file=item.getPath() or "",
        title=tag.getTitle() or "",
        artist=tag.getArtist() or "",
        album=tag.getAlbum() or "",
        duration=int(tag.getDuration() or 0),
        songid=songid,
        thumb=item.getArt("thumb") or "",
        fanart=item.getArt("fanart") or "",
    )


def _stamp(item: xbmcgui.ListItem, track: Track, art: Optional[Dict[str, str]]) -> None:
    if track.file and track.file != item.getPath():
        item.setPath(track.file)
    if track.songid:
        item.getMusicInfoTag().setDbId(track.songid, "song")
        item.setProperty(SONGID, str(track.songid))
    else:
        item.setProperty(SONGID, "")
    if art is not None:
        _apply_art(item, track, art)
    item.setProperty(PENDING, "")


def confirm_playlist_slot(playlist: xbmc.PlayList, index: int, with_art: bool) -> None:
    """Confirm one queued item, if restore left it pending.

    Safe to call repeatedly, and from the service thread: an item that is
    not pending returns without a library call. Failures stay pending so the
    next pass can try again.
    """
    try:
        # Kodistubs' PlayList declares no __getitem__; Kodi's playlist does.
        item = playlist[index]  # type: ignore[index]
    except Exception:  # noqa: BLE001 - the playlist can shrink under us
        return
    if item.getProperty(PENDING) != "1":
        return
    before = _track_from_item(item)
    confirmed = confirm_track(before, with_art)
    if not confirmed.settled:
        return
    _note_rebind(before, confirmed.track)
    _stamp(item, confirmed.track, confirmed.art if with_art else None)


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
    started = time.perf_counter()
    tracks: List[Track] = [track for track in record.tracks if track.file]
    if not tracks:
        log.error("nothing playable in this record")
        return False

    position, tick = playback_start(record, from_track_start)
    if position >= len(tracks):
        position, tick = 0, 0.0

    lookups = 0
    resume_art: Dict[str, str] = {}
    resume_settled = True
    if rebind.jellyfin_audio_id(tracks[position].file):
        confirmed = confirm_track(tracks[position], with_art=True)
        lookups += confirmed.lookups
        resume_settled = confirmed.settled
        if confirmed.settled:
            _note_rebind(tracks[position], confirmed.track)
            tracks[position] = confirmed.track
            resume_art = confirmed.art

    playlist = xbmc.PlayList(MUSIC_PLAYLIST)
    playlist.clear()
    for index, track in enumerate(tracks):
        is_resume = index == position
        jelly = rebind.jellyfin_audio_id(track.file) is not None
        # The resume track is the one about to play, so it carries a song id
        # now. A check that could not be made still stamps the recorded id
        # and stays pending, which is the same "could not ask" result as
        # before. Every other Jellyfin track waits.
        stamp_songid = (not jelly) or is_resume
        pending = jelly and not (is_resume and resume_settled)
        item = _list_item(
            track,
            resume_art if is_resume else {},
            stamp_songid,
            pending,
        )
        if is_resume and tick >= MIN_OFFSET:
            item.setProperty("StartOffset", str(tick))
        playlist.add(track.file, item)

    log.info(
        "restoring %d track(s) from position %d at %.0fs in %.0fms"
        " (%d library lookup(s) before play)%s%s",
        len(tracks),
        position,
        tick,
        (time.perf_counter() - started) * 1000.0,
        lookups,
        (
            " (from the start of the track)"
            if from_track_start and not record.finished
            else ""
        ),
        " (paused)" if start_paused else "",
    )
    xbmc.Player().play(playlist, startpos=position)

    # The following track, while this one plays, so it is ready when it starts.
    if position + 1 < len(tracks):
        confirm_playlist_slot(playlist, position + 1, with_art=True)

    if start_paused:
        _pause_once_playing()
    return True
