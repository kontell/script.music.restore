"""Start playback, then build the queue with native library items.

The resume item alone goes into Kodi before Player.play. A read-only library
map validates song IDs after playback has started, and JSON-RPC Playlist.Add
creates the rest as full C++ library items. Kodi can reuse song IDs after a
library repair, so no saved ID is used without matching its Jellyfin item ID.
"""

import glob
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

import xbmc
import xbmcgui
import xbmcvfs

from . import history, jsonrpc, logging as log, rebind
from .model import QueueRecord, Track, playback_start

MUSIC_PLAYLIST = 0

# Below this, resuming is not worth the offset — the track had barely started.
MIN_OFFSET = 5.0

# How long to wait for playback before pausing a queue restored paused.
PAUSE_TIMEOUT = 10.0
PAUSE_STEP = 100  # ms

# Set on a queued file whose song id could not be checked. The candidate id
# rides alongside it: stamping that id as the dbid before the check is how a
# repaired library gets the wrong song.
PENDING = "musicrestore.pending"
SONGID = "musicrestore.songid"

# Home-window property. Set while the playlist is being replaced so the
# recorder does not snapshot a half-built queue.
BUILDING = "musicrestore.building"
ENRICHING = "musicrestore.enriching"
RESTORE_SOURCE = "musicrestore.source"
RESTORE_READY = "musicrestore.ready"

# Progress through the add loop. One line per chunk, plus a line for any
# single add that stalls, so a slow restore shows where the time went.
ADD_LOG_EVERY = 50
SLOW_ADD_MS = 100.0

# Asked of each page in the post-play library scan. Image properties start a
# thumb loader for every library song, while the saved queue already has art.
SONG_FIELDS = [
    "title",
    "artist",
    "album",
    "duration",
    "file",
    "year",
    "genre",
    "playcount",
    "track",
    "disc",
]

PAGE_SIZE = 1000
CACHE_STORE = history.PROFILE + "library_cache.json"
REVISION_FIELDS = [
    "librarylastupdated",
    "librarylastcleaned",
    "songslastadded",
    "songsmodified",
]


@dataclass
class SongFacts:
    """Year, genre and play count from the library row.

    The song-info dialog shows whatever is already on the item's tag. Marking
    that tag loaded stops Kodi filling these from the database, which is how
    a restored track was left with only title, artist, album and duration.
    """

    year: int = 0
    genres: Tuple[str, ...] = ()
    playcount: int = 0


@dataclass
class FillTimings:
    """Aggregate time spent crossing Kodi's Python API during item writes."""

    calls: int = 0
    total_ms: float = 0.0
    stages: Dict[str, List[float]] = field(default_factory=dict)

    def record(self, stage: str, started: float) -> None:
        self.stages.setdefault(stage, []).append(_since(started))

    def report(self, context: str) -> None:
        details = ", ".join(
            "%s %d/%.0fms" % (name, len(times), sum(times))
            for name, times in sorted(self.stages.items())
        )
        log.info(
            "%s item writes %d in %.0fms (%s)",
            context,
            self.calls,
            self.total_ms,
            details,
        )


def song_facts(song: Dict[str, Any]) -> SongFacts:
    """Pull year, genre and play count out of a library song row."""
    try:
        year = int(song.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    raw = song.get("genre")
    if isinstance(raw, str):
        genres: Sequence[str] = (raw,) if raw else ()
    elif isinstance(raw, list):
        genres = tuple(str(item) for item in raw if item)
    else:
        genres = ()
    try:
        playcount = int(song.get("playcount") or 0)
    except (TypeError, ValueError):
        playcount = 0
    return SongFacts(year, tuple(genres), playcount)


def _detail_properties(with_art: bool) -> List[str]:
    if with_art:
        return SONG_FIELDS + ["art", "thumbnail", "fanart"]
    return list(SONG_FIELDS)


@dataclass
class Confirmed:
    """A track after one attempt to point it at the live library row."""

    track: Track
    art: Dict[str, str]
    # False when Kodi could not be asked. The item stays pending and is tried
    # again; a settled miss drops the song id instead of keeping a stale one.
    settled: bool
    lookups: int
    facts: SongFacts = field(default_factory=SongFacts)


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


def _fill(
    item: xbmcgui.ListItem,
    track: Track,
    library_art: Dict[str, str],
    stamp_songid: bool,
    pending: bool,
    facts: Optional[SongFacts] = None,
    timings: Optional[FillTimings] = None,
    creating: bool = False,
    clear_pending: bool = True,
) -> None:
    """Put the record, and any library facts, onto a playlist item.

    ``setInfo`` is deprecated, and it is the call that marks the music tag
    loaded. The InfoTagMusic setters do not, and an unloaded tag makes Kodi
    open the music database and then the file for every queued item the
    first time the player asks about it. One label is enough to set the
    flag, so mediatype is always present.
    """
    started = time.perf_counter()
    title = track.title if track.title and "://" not in track.title else ""
    if title and not creating and item.getLabel() != title:
        stage = time.perf_counter()
        item.setLabel(title)
        if timings is not None:
            timings.record("label", stage)
    if not creating and item.getPath() != track.file:
        stage = time.perf_counter()
        item.setPath(track.file)
        if timings is not None:
            timings.record("path", stage)
    info: Dict[str, Any] = {"mediatype": "song"}
    if title:
        info["title"] = title
    if track.album and "://" not in track.album:
        info["album"] = track.album
    if track.artist and "://" not in track.artist:
        info["artist"] = track.artist
    if track.duration:
        info["duration"] = str(track.duration)
    year = facts.year if facts is not None else track.year
    genres = facts.genres if facts is not None else track.genres
    playcount = facts.playcount if facts is not None else track.playcount
    info["year"] = str(year)
    info["genre"] = list(genres)
    info["playcount"] = str(playcount)
    info["tracknumber"] = str(track.tracknumber)
    info["discnumber"] = str(track.discnumber)
    if stamp_songid and track.songid:
        info["dbid"] = str(track.songid)
    stage = time.perf_counter()
    item.setInfo("music", info)
    if timings is not None:
        timings.record("setInfo", stage)
    if library_art or track.thumb or track.fanart:
        stage = time.perf_counter()
        _apply_art(item, track, library_art)
        if timings is not None:
            timings.record("art", stage)
    stage = time.perf_counter()
    if pending:
        wanted_songid = str(track.songid or "")
        if item.getProperty(SONGID) != wanted_songid:
            item.setProperty(SONGID, wanted_songid)
        if item.getProperty(PENDING) != "1":
            item.setProperty(PENDING, "1")
    elif clear_pending and item.getProperty(PENDING):
        item.setProperty(PENDING, "")
        item.setProperty(SONGID, "")
    if timings is not None:
        timings.record("properties", stage)
        timings.calls += 1
        timings.total_ms += _since(started)


def _scan(item_id: str, with_art: bool) -> object:
    """The current library row for a Jellyfin audio id.

    Returns the song dict, None when the library was asked and the song is
    gone, or :data:`rebind.LOOKUP_FAILED` when it could not be asked.

    Filters on path: Kodi stores the host URL in ``path.strPath`` and the
    ``stream.*`` leaf in ``song.strFileName``, so a filename filter misses.
    ``includesingles`` is set because an absent value makes ``GetSongs`` skip
    singles, and a repaired single would then look deleted.
    """
    result, error = jsonrpc.invoke(
        "AudioLibrary.GetSongs",
        filter={"field": "path", "operator": "contains", "value": item_id},
        properties=_detail_properties(with_art),
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
            properties=_detail_properties(with_art),
        )
        song: Optional[dict] = None
        if error is None and isinstance(result, dict):
            raw = result.get("songdetails")
            song = raw if isinstance(raw, dict) else None
        if song is not None and rebind.same_library_song(
            track, str(song.get("file") or "")
        ):
            art = rebind.art_map(song) if with_art else {}
            return Confirmed(
                rebind.apply_library_hit(track, song),
                art,
                True,
                lookups,
                song_facts(song),
            )
        # The id is gone, or it was reused. Fall through to the path scan.
        # An error here is not fatal on its own: the scan is the second ask,
        # and if that cannot be made either, the track stays unsettled.

    lookups += 1
    found = _scan(item_id, with_art)
    if found is rebind.LOOKUP_FAILED:
        return Confirmed(track, {}, False, lookups)
    if isinstance(found, dict):
        art = rebind.art_map(found) if with_art else {}
        return Confirmed(
            rebind.apply_library_hit(track, found),
            art,
            True,
            lookups,
            song_facts(found),
        )
    return Confirmed(replace(track, songid=None), {}, True, lookups)


def _note_rebind(before: Track, after: Track) -> None:
    if after.songid != before.songid:
        # The title is a display name. A record captured off a bare stream
        # item has the URL there, and a log line must not carry that.
        title = after.title
        if not title or "://" in title:
            title = "track"
        log.info(
            "rebound %s songid %s -> %s",
            title,
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


def _stamp(
    item: xbmcgui.ListItem,
    track: Track,
    art: Optional[Dict[str, str]],
    facts: SongFacts,
) -> None:
    _fill(item, track, art or {}, bool(track.songid), False, facts)


def confirmation_order(count: int, started: int) -> List[int]:
    """Every index except ``started``, beginning at the next track.

    Playback has already started at ``started``. The walk continues to the
    end of the queue and then from the top up to the track that started, so
    the song about to play is settled first and the rest of the queue is
    still covered.
    """
    if count <= 1 or not 0 <= started < count:
        return []
    return list(range(started + 1, count)) + list(range(started))


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
    _stamp(
        item,
        confirmed.track,
        confirmed.art if with_art else None,
        confirmed.facts,
    )


def _since(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _set_building(active: bool) -> None:
    try:
        xbmcgui.Window(10000).setProperty(BUILDING, "1" if active else "")
    except Exception:  # noqa: BLE001 - a missing window must not block playback
        log.error("could not mark the playlist build")


def _set_enriching(active: bool) -> None:
    try:
        xbmcgui.Window(10000).setProperty(ENRICHING, "1" if active else "")
    except Exception:  # noqa: BLE001 - a missing window must not block playback
        log.error("could not mark library enrichment")


def _set_restore_source(marker: str) -> None:
    try:
        xbmcgui.Window(10000).setProperty(RESTORE_SOURCE, marker)
    except Exception:  # noqa: BLE001 - a missing window must not block playback
        log.error("could not mark the restore source")


def _mark_restore_ready() -> None:
    try:
        xbmcgui.Window(10000).setProperty(RESTORE_READY, str(time.time_ns()))
    except Exception:  # noqa: BLE001 - a missing window must not block playback
        log.error("could not mark the restored queue ready")


def _revision() -> Optional[Tuple[str, ...]]:
    """A library generation for validating song ids kept between restores."""
    result, error = jsonrpc.invoke(
        "AudioLibrary.GetProperties", properties=list(REVISION_FIELDS)
    )
    if error is not None or not isinstance(result, dict):
        return None
    values = tuple(str(result.get(field) or "") for field in REVISION_FIELDS)
    return values if any(values) else None


def _cache_contents() -> Dict[str, Any]:
    try:
        if not xbmcvfs.exists(CACHE_STORE):
            return {}
        with xbmcvfs.File(CACHE_STORE) as handle:
            raw = handle.read()
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:  # noqa: BLE001 - cache damage cannot delay playback
        return {}


def _cached_rows(item_ids: Sequence[str]) -> Dict[str, Optional[Dict[str, Any]]]:
    """Rows, including settled misses, only when Kodi's library is unchanged."""
    data = _cache_contents()
    if data.get("version") != 1 or not isinstance(data.get("revision"), list):
        return {}
    revision = _revision()
    if revision is None or list(revision) != data["revision"]:
        return {}
    raw_rows = data.get("songs")
    if not isinstance(raw_rows, dict):
        return {}
    rows: Dict[str, Optional[Dict[str, Any]]] = {}
    for item_id in item_ids:
        if item_id not in raw_rows:
            continue
        row = raw_rows[item_id]
        if row is None:
            rows[item_id] = None
        elif (
            isinstance(row, dict)
            and isinstance(row.get("songid"), int)
            and row["songid"] > 0
            and rebind.jellyfin_audio_id(str(row.get("file") or "")) == item_id
        ):
            rows[item_id] = row
    log.info("library cache hit %d/%d id(s)", len(rows), len(item_ids))
    return rows


def _save_cache(
    revision: Optional[Tuple[str, ...]],
    item_ids: Sequence[str],
    found: Dict[str, Dict[str, Any]],
) -> None:
    if revision is None:
        return
    previous = _cache_contents()
    raw_rows = (
        previous.get("songs")
        if previous.get("version") == 1 and previous.get("revision") == list(revision)
        else {}
    )
    rows: Dict[str, Any] = dict(raw_rows) if isinstance(raw_rows, dict) else {}
    for item_id in item_ids:
        rows[item_id] = found.get(item_id)
    payload = json.dumps(
        {"version": 1, "revision": list(revision), "songs": rows},
        separators=(",", ":"),
    )
    try:
        if not xbmcvfs.exists(history.PROFILE):
            xbmcvfs.mkdirs(history.PROFILE)
        temporary = CACHE_STORE + ".tmp"
        with xbmcvfs.File(temporary, "w") as handle:
            handle.write(payload)
        os.replace(xbmcvfs.translatePath(temporary), xbmcvfs.translatePath(CACHE_STORE))
    except Exception as exc:  # noqa: BLE001 - cache failure is not restore failure
        log.error("could not update library cache: %s", exc)


def _batch(item_ids: Sequence[str]) -> Tuple[Dict[str, Dict[str, Any]], bool]:
    """Find queue songs by paging the library without a giant SQL expression.

    ``asked`` is false when a page failed or the library changed mid-scan. A
    successful scan settles absent ids as misses and stores them in the cache.
    """
    if not item_ids:
        return {}, True
    wanted = set(item_ids)
    for attempt in range(2):
        started = time.perf_counter()
        before = _revision()
        found: Dict[str, Dict[str, Any]] = {}
        start = 0
        pages = 0
        total = 0
        while True:
            result, error = jsonrpc.invoke(
                "AudioLibrary.GetSongs",
                properties=list(SONG_FIELDS),
                includesingles=True,
                limits={"start": start, "end": start + PAGE_SIZE},
            )
            if error is not None or not isinstance(result, dict):
                log.error("library page %d failed", pages + 1)
                return {}, False
            limits = result.get("limits")
            songs = result.get("songs") or []
            if not isinstance(limits, dict) or not isinstance(songs, list):
                log.error("library page %d returned an incomplete result", pages + 1)
                return {}, False
            reported_total = limits.get("total")
            if not isinstance(reported_total, int) or (
                not songs and start < reported_total
            ):
                log.error("library page %d returned an invalid count", pages + 1)
                return {}, False
            total = reported_total
            pages += 1
            for song in songs:
                if not isinstance(song, dict):
                    continue
                item_id = rebind.jellyfin_audio_id(str(song.get("file") or ""))
                if item_id in wanted and item_id not in found:
                    found[item_id] = song
            start += len(songs)
            if start >= total or len(found) == len(wanted):
                break
        after = _revision()
        if before is not None and after is not None and before != after:
            log.info("music library changed during scan; retrying (%d/2)", attempt + 1)
            continue
        stable = before if before == after else None
        _save_cache(stable, item_ids, found)
        log.info(
            "library scan %d page(s), %d/%d id(s), %d song(s) in %.0fms",
            pages,
            len(found),
            len(wanted),
            total,
            _since(started),
        )
        return found, True
    return {}, False


def _fill_at(
    playlist: xbmc.PlayList,
    index: int,
    track: Track,
    library_art: Dict[str, str],
    stamp_songid: bool,
    pending: bool,
    facts: Optional[SongFacts],
    timings: Optional[FillTimings] = None,
    playlist_reads: Optional[List[float]] = None,
    clear_pending: bool = True,
) -> None:
    started = time.perf_counter()
    try:
        item = playlist[index]  # type: ignore[index]
    except Exception:  # noqa: BLE001
        if playlist_reads is not None:
            playlist_reads.append(_since(started))
        return
    if playlist_reads is not None:
        playlist_reads.append(_since(started))
    # A user can replace the queue while the library scan is running. Never
    # write its result onto whatever now occupies the same numeric position.
    if rebind.jellyfin_audio_id(item.getPath()) != rebind.jellyfin_audio_id(track.file):
        return
    _fill(
        item,
        track,
        library_art,
        stamp_songid,
        pending,
        facts,
        timings,
        clear_pending=clear_pending,
    )


def _apply_library(
    playlist: xbmc.PlayList,
    tracks: Sequence[Track],
    cached: Dict[str, Optional[Dict[str, Any]]],
    pending_ids: Sequence[str],
    position: int,
    timings: FillTimings,
) -> None:
    """Hydrate the already-playing queue, then refresh its library identities."""
    queried = time.perf_counter()
    found, asked = _batch(pending_ids)
    query_ms = _since(queried)
    if not pending_ids:
        log.info("library cache covered the restored queue")
    wrote = time.perf_counter()
    playlist_reads: List[float] = []
    order = [position] + confirmation_order(len(tracks), position)
    for ordinal, index in enumerate(order):
        track = tracks[index]
        item_id = rebind.jellyfin_audio_id(track.file)
        if item_id in cached:
            row = cached[item_id]
            settled = True
        elif item_id:
            row = found.get(item_id)
            settled = asked
        else:
            row = None
            settled = True
        if isinstance(row, dict):
            display = rebind.apply_library_hit(track, row)
            _note_rebind(track, display)
            facts = song_facts(row)
        elif item_id and settled:
            display = replace(track, songid=None)
            facts = None
        else:
            display = track
            facts = None
        _fill_at(
            playlist,
            index,
            display,
            {},
            bool(display.songid) and settled,
            not settled,
            facts,
            timings,
            playlist_reads,
            clear_pending=False,
        )
        if ordinal and ordinal % 25 == 0:
            xbmc.sleep(1)
    log.info(
        "library query %.0fms, wrote %d track(s) in %.0fms (%d uncached id(s))",
        query_ms,
        len(tracks),
        _since(wrote),
        len(pending_ids),
    )
    log.info(
        "library metadata lookup returned %d/%d rows; playlist reads %d in %.0fms",
        len(found) + sum(isinstance(row, dict) for row in cached.values()),
        len(set(pending_ids) | set(cached)),
        len(playlist_reads),
        sum(playlist_reads),
    )
    timings.report("library")


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


def _sqlite_rows(
    item_ids: Sequence[str],
    tracks: Sequence[Track],
) -> Tuple[Dict[str, Dict[str, Any]], bool]:
    """Read the local music DB once, without building a giant SQL expression.

    Kodi's JSON-RPC ``or`` filter fails above SQLite's expression depth limit.
    Indexed parent-path reads give us the IDs and live paths needed for native
    playlist additions. Validate several rows through Kodi
    before trusting the file: a configured MySQL library may leave an old local
    SQLite file behind. Other DB engines use the JSON-RPC page fallback.
    """
    database_dir = xbmcvfs.translatePath("special://database/")
    candidates = []
    for path in glob.glob(os.path.join(database_dir, "MyMusic*.db")):
        match = re.fullmatch(r"MyMusic(\d+)\.db", os.path.basename(path))
        if match:
            candidates.append((int(match.group(1)), path))
    if not candidates:
        return {}, False
    path = max(candidates)[1]
    wanted = set(item_ids)
    directories = list(
        dict.fromkeys(
            track.file.rsplit("/", 1)[0] + "/"
            for track in tracks
            if rebind.jellyfin_audio_id(track.file) in wanted
        )
    )
    found: Dict[str, Dict[str, Any]] = {}
    scanned = 0
    started = time.perf_counter()
    try:
        connection = sqlite3.connect("file:" + path + "?mode=ro", uri=True, timeout=2)
        try:
            for offset in range(0, len(directories), 400):
                block = directories[offset : offset + 400]
                marks = ",".join("?" for _ in block)
                cursor = connection.execute(
                    "SELECT song.idSong, path.strPath, song.strFileName "
                    "FROM path JOIN song ON song.idPath=path.idPath "
                    "WHERE path.strPath IN (" + marks + ")",
                    block,
                )
                for songid, directory, leaf in cursor:
                    scanned += 1
                    file = str(directory or "") + str(leaf or "")
                    item_id = rebind.jellyfin_audio_id(file)
                    if item_id in wanted:
                        found[item_id] = {"songid": int(songid), "file": file}
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as exc:
        log.info("read-only music DB scan unavailable: %s", type(exc).__name__)
        return {}, False
    samples = list(found.items())
    if samples:
        for item_id, row in (samples[0], samples[len(samples) // 2], samples[-1]):
            result, error = jsonrpc.invoke(
                "AudioLibrary.GetSongDetails",
                songid=row["songid"],
                properties=["file"],
            )
            details = result.get("songdetails") if isinstance(result, dict) else None
            if (
                error is not None
                or not isinstance(details, dict)
                or rebind.jellyfin_audio_id(str(details.get("file") or "")) != item_id
            ):
                log.info("local music DB differs from Kodi's active library")
                return {}, False
    log.info(
        "indexed music DB lookup matched %d/%d id(s) from %d rows in %.0fms",
        len(found),
        len(wanted),
        scanned,
        _since(started),
    )
    return found, True


def _library_rows(
    item_ids: Sequence[str],
    tracks: Sequence[Track],
) -> Tuple[Dict[str, Optional[Dict[str, Any]]], bool]:
    cached = _cached_rows(item_ids)
    pending = [item_id for item_id in item_ids if item_id not in cached]
    if not pending:
        return cached, True
    before = _revision()
    found, asked = _sqlite_rows(pending, tracks)
    after = _revision()
    if asked and before is not None and after is not None and before != after:
        log.info("music library changed during ID scan; using JSON-RPC pages")
        asked = False
    if not asked:
        found, asked = _batch(pending)
    elif len(found) < len(pending):
        missing = [item_id for item_id in pending if item_id not in found]
        extra, complete = _batch(missing)
        found.update(extra)
        asked = complete
    if asked and before == after:
        _save_cache(before, pending, found)
    rows: Dict[str, Optional[Dict[str, Any]]] = dict(cached)
    if asked:
        rows.update({item_id: found.get(item_id) for item_id in pending})
    return rows, asked


def _native_requests(method: str, params: Sequence[Dict[str, Any]]) -> List[bool]:
    """Run an ordered JSON-RPC batch; each request contains one song.

    Each Playlist.Insert request shifts Kodi's playing index by one. A single
    Insert containing an array shifts it only once, so the prefix uses a
    JSON-RPC batch of individual inserts.
    """
    if not params:
        return []
    request = [
        {"jsonrpc": "2.0", "id": index, "method": method, "params": entry}
        for index, entry in enumerate(params)
    ]
    try:
        response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    except Exception:  # noqa: BLE001 - a failed batch is retried as files
        return [False] * len(params)
    if not isinstance(response, list):
        return [False] * len(params)
    outcomes = [False] * len(params)
    for answer in response:
        if isinstance(answer, dict):
            index = answer.get("id")
            if isinstance(index, int) and 0 <= index < len(outcomes):
                outcomes[index] = "result" in answer
    return outcomes


def _native_one(method: str, position: Optional[int], item: Dict[str, Any]) -> bool:
    params: Dict[str, Any] = {"playlistid": MUSIC_PLAYLIST, "item": item}
    if position is not None:
        params["position"] = position
    return _native_requests(method, [params]) == [True]


def _append_array(items: Sequence[Dict[str, Any]]) -> bool:
    """Let Kodi construct many library items in C++ in one playlist call.

    Kodi's per-item FillFileItemList sorts the one-item list on its first
    call, then appends later items without re-sorting. This was checked on a
    real 124-track and 1,313-track queues; the order is still checked after add.
    """
    result, error = jsonrpc.invoke(
        "Playlist.Add", playlistid=MUSIC_PLAYLIST, item=list(items)
    )
    return error is None and result is not None


def _same_entry(track: Track, file: str) -> bool:
    item_id = rebind.jellyfin_audio_id(track.file)
    return rebind.jellyfin_audio_id(file) == item_id if item_id else file == track.file


def _repair_tail(tail: Sequence[Track], starter_count: int) -> None:
    """Replace songs a partially successful native bulk add skipped."""
    result, error = jsonrpc.invoke(
        "Playlist.GetItems", playlistid=MUSIC_PLAYLIST, properties=["file"]
    )
    actual = result.get("items") if isinstance(result, dict) else None
    if error is not None or not isinstance(actual, list) or not actual:
        log.error("could not verify restored tail")
        return
    actual_index = starter_count
    inserted = 0
    for offset, track in enumerate(tail):
        file = (
            str(actual[actual_index].get("file") or "")
            if actual_index < len(actual) and isinstance(actual[actual_index], dict)
            else ""
        )
        if _same_entry(track, file):
            actual_index += 1
            continue
        if _native_one("Playlist.Insert", offset + starter_count, {"file": track.file}):
            inserted += 1
        else:
            log.error("could not repair a skipped restored item")
    log.info("repaired %d skipped native tail item(s)", inserted)


def _repair_prefix(prefix: Sequence[Track]) -> None:
    result, error = jsonrpc.invoke(
        "Playlist.GetItems", playlistid=MUSIC_PLAYLIST, properties=["file"]
    )
    actual = result.get("items") if isinstance(result, dict) else None
    if error is not None or not isinstance(actual, list):
        log.error("could not verify restored prefix")
        return
    actual_index = 0
    inserted = 0
    for position, track in enumerate(prefix):
        file = (
            str(actual[actual_index].get("file") or "")
            if actual_index < len(actual) and isinstance(actual[actual_index], dict)
            else ""
        )
        if _same_entry(track, file):
            actual_index += 1
            continue
        if _native_one("Playlist.Insert", position, {"file": track.file}):
            inserted += 1
        else:
            log.error("could not repair a skipped prefix item")
    log.info("repaired %d skipped native prefix item(s)", inserted)


def _wait_for_size(playlist: xbmc.PlayList, wanted: int) -> bool:
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if playlist.size() == wanted:
            return True
        xbmc.sleep(100)
    return playlist.size() == wanted


def restore(
    record: QueueRecord,
    start_paused: bool = False,
    from_track_start: bool = False,
) -> bool:
    """Start one recorded song, then append and prepend native library items."""
    started = time.perf_counter()
    tracks: List[Track] = [track for track in record.tracks if track.file]
    if not tracks:
        log.error("nothing playable in this record")
        return False

    position, tick = playback_start(record, from_track_start)
    if position >= len(tracks):
        position, tick = 0, 0.0

    item_ids = list(
        dict.fromkeys(
            item_id
            for track in tracks
            if (item_id := rebind.jellyfin_audio_id(track.file))
        )
    )
    # The player may advance immediately when the resume tick is near the end.
    # Resolve both tracks before play, including current DB IDs, tags and art.
    near_indexes = [position]
    if position + 1 < len(tracks):
        near_indexes.append(position + 1)
    preparing = time.perf_counter()
    near = {
        index: confirm_track(tracks[index], with_art=True) for index in near_indexes
    }
    prepared: List[Tuple[str, xbmcgui.ListItem]] = []
    for index in near_indexes:
        confirmed = near[index]
        track = confirmed.track
        title = track.title if track.title and "://" not in track.title else ""
        item = xbmcgui.ListItem(
            label=title or track.file, path=track.file, offscreen=True
        )
        item_id = rebind.jellyfin_audio_id(track.file)
        facts = (
            confirmed.facts if item_id and confirmed.settled and track.songid else None
        )
        _fill(
            item,
            track,
            confirmed.art,
            bool(track.songid) and confirmed.settled,
            bool(item_id) and not confirmed.settled,
            facts,
            creating=True,
        )
        if index == position and tick >= MIN_OFFSET:
            item.setProperty("StartOffset", str(tick))
        prepared.append((track.file, item))
    prepare_ms = _since(preparing)
    playlist = xbmc.PlayList(MUSIC_PLAYLIST)
    _set_building(True)
    played = False
    try:
        playlist.clear()
        _set_restore_source("%.3f:%d" % (record.saved, len(tracks)))
        for file, item in prepared:
            playlist.add(file, item)
        xbmc.Player().play(playlist, startpos=0)
        played = True
        log.info(
            "playback requested for %d-track restore at position %d in %.0fms"
            " (%d tagged before play in %.0fms, %d lookup(s))",
            len(tracks),
            position,
            _since(started),
            len(prepared),
            prepare_ms,
            sum(confirmed.lookups for confirmed in near.values()),
        )
        _set_enriching(True)
        if start_paused:
            _pause_once_playing()
        querying = time.perf_counter()
        rows, asked = _library_rows(item_ids, tracks)
        log.info(
            "library ID map ready in %.0fms (%d/%d cached or found)",
            _since(querying),
            sum(isinstance(row, dict) for row in rows.values()),
            len(item_ids),
        )

        # Native JSON-RPC items come from CMusicDatabase and already have all
        # library tags and artwork. A missing song uses its saved file; its
        # saved display facts are applied after the queue has reached full size.
        def native_item(track: Track) -> Dict[str, Any]:
            item_id = rebind.jellyfin_audio_id(track.file)
            row = rows.get(item_id) if item_id else None
            if isinstance(row, dict):
                return {"songid": row["songid"]}
            if not item_id and track.songid:
                return {"songid": track.songid}
            return {"file": track.file}

        tail_started = time.perf_counter()
        starter_count = len(prepared)
        tail = tracks[position + starter_count :]
        if tail and not _append_array([native_item(track) for track in tail]):
            for offset in range(0, len(tail), 100):
                block = tail[offset : offset + 100]
                outcomes = _native_requests(
                    "Playlist.Add",
                    [
                        {"playlistid": MUSIC_PLAYLIST, "item": native_item(track)}
                        for track in block
                    ],
                )
                log.info(
                    "native retry accepted %d/%d item(s)", sum(outcomes), len(block)
                )
        if not _wait_for_size(playlist, len(tail) + starter_count):
            _repair_tail(tail, starter_count)
        log.info("native queue appended %d following item(s)", len(tail))
        tail_ms = _since(tail_started)
        prefix_started = time.perf_counter()
        prefix = tracks[:position]
        reversed_prefix = list(reversed(prefix))
        all_inserted = True
        for offset in range(0, len(reversed_prefix), 100):
            block = reversed_prefix[offset : offset + 100]
            outcomes = _native_requests(
                "Playlist.Insert",
                [
                    {
                        "playlistid": MUSIC_PLAYLIST,
                        "position": 0,
                        "item": native_item(track),
                    }
                    for track in block
                ],
            )
            all_inserted = all_inserted and all(outcomes)
        if not all_inserted or not _wait_for_size(playlist, len(tracks)):
            _repair_prefix(prefix)
        prefix_ms = _since(prefix_started)
        complete = _wait_for_size(playlist, len(tracks))
        if not complete:
            log.error("restored queue has %d/%d item(s)", playlist.size(), len(tracks))
        # The first two items were fully tagged before play. Native song items
        # already carry library metadata; only unresolved or changed rows need
        # Python writes now.
        for index in [position] + [i for i in range(len(tracks)) if i != position]:
            track = tracks[index]
            item_id = rebind.jellyfin_audio_id(track.file)
            row = rows.get(item_id) if item_id else None
            if index in near:
                confirmed = near[index]
                if confirmed.settled and (
                    (isinstance(row, dict) and confirmed.track.songid == row["songid"])
                    or (row is None and asked and confirmed.track.songid is None)
                    or not item_id
                ):
                    continue
            if index not in near and isinstance(row, dict):
                continue
            if index in near and isinstance(row, dict):
                display = rebind.apply_library_hit(track, row)
                details, error = jsonrpc.invoke(
                    "AudioLibrary.GetSongDetails",
                    songid=row["songid"],
                    properties=_detail_properties(True),
                )
                song = (
                    details.get("songdetails")
                    if error is None and isinstance(details, dict)
                    else None
                )
                if isinstance(song, dict) and rebind.same_library_song(
                    track, str(song.get("file") or "")
                ):
                    display = rebind.apply_library_hit(track, song)
                    art = rebind.art_map(song)
                    facts = song_facts(song)
                else:
                    art, facts = {}, None
                _fill_at(playlist, index, display, art, True, False, facts)
            else:
                _fill_at(
                    playlist,
                    index,
                    replace(track, songid=None) if item_id and asked else track,
                    {},
                    not bool(item_id) and bool(track.songid),
                    not asked and bool(item_id),
                    None,
                )
        _mark_restore_ready()
        log.info(
            "restored %d track(s) in %.0fms (tail %.0fms, prefix %.0fms, complete %s)",
            len(tracks),
            _since(started),
            tail_ms,
            prefix_ms,
            complete,
        )
    finally:
        if not played:
            _set_restore_source("")
        _set_enriching(False)
        _set_building(False)
    return played
