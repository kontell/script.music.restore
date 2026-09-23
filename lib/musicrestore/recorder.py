"""The service: keep a live copy of the music queue, save it when it goes.

By the time a Python addon is told the queue changed, Kodi has already changed
it. Announcements are queued and delivered on a worker thread
(``CAnnouncementManager::Process``), so a handler that responds to
``Playlist.OnClear`` by calling ``Playlist.GetItems`` gets the *incoming* queue
back, and a ``position`` that has already been reset. Measured on Kodi 21.3:
the outgoing queue had ten tracks at position 2, and the handler saw eight
tracks at position 0.

So this class mirrors the queue continuously and commits what it already holds:

* ``Playlist.OnAdd`` / ``OnRemove`` schedule a debounced ``Playlist.GetItems``.
  Adding an album fires one notification per track, so the burst is coalesced.
  The notification payloads are not enough on their own — a track with no
  library id arrives carrying no file path at all.
* Every second the position and elapsed time are read straight off the player
  (no JSON-RPC), which is what makes the shadow *armed*: it has a position that
  was sampled while these exact tracks were live. The position Kodi reports
  belongs to whichever playlist the *player* is on, not to the music playlist
  it was asked for, so it is reconciled against the file actually playing
  before it is believed — see :meth:`Recorder._locate`.
* A commit copies the shadow and disarms it. Nothing commits unless armed,
  which is what stops one user action leaving two rows — replacing an album
  fires ``OnClear`` and then ``Player.OnStop`` about 150 ms later, and by then
  the shadow has been cleared and is refilling with the *new* queue.
"""

import json
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import xbmc

from . import history, jsonrpc, logging as log, restore, settings
from .model import QueueRecord, Track

MUSIC_PLAYLIST = 0

# What a snapshot records. Enough to draw a history row and to rebuild the
# queue without going back to the music database.
SNAPSHOT_PROPERTIES = [
    "file",
    "title",
    "artist",
    "albumartist",
    "album",
    "duration",
    "thumbnail",
    # The flat field rather than the whole "art" map: fanart is the one piece
    # a restore cannot get back on its own, and asking for "art" would put
    # fourteen image URLs per track through this call on every queue change.
    "fanart",
]

POLL_INTERVAL = 0.5  # main loop
SAMPLE_EVERY = 2  # sample the player every other poll, so ~1s
REFRESH_DELAY = 0.25  # debounce after an Add/Remove burst

# Kodi fires Player.OnStop for the outgoing track on *every* track change, not
# only when the queue dies — skipping to the next track, or a track simply
# ending, looks identical to being stopped. So a stop is held for a moment and
# only acted on if audio has not come back by then. Measured gap between
# OnStop and the following OnPlay: about 10 ms, so this is generous.
STOP_GRACE = 1.5

# How long to let Kodi finish starting before a startup restore. Generous:
# arriving late is harmless, arriving before the skin is up is not.
STARTUP_WAIT = 90.0


def _seconds(value: Dict[str, Any]) -> float:
    """Kodi's split time object as plain seconds."""
    return (
        float(value.get("hours", 0)) * 3600
        + float(value.get("minutes", 0)) * 60
        + float(value.get("seconds", 0))
        + float(value.get("milliseconds", 0)) / 1000.0
    )


class Recorder(xbmc.Monitor):
    """Mirrors the music queue and writes it to history when it is taken away."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._items: List[Track] = []
        self._position = 0
        self._tick = 0.0
        self._armed = False
        self._refresh_at: Optional[float] = None
        self._stop_at: Optional[float] = None
        # True when the OnStop we are waiting out was a natural end, not a
        # user Stop. end:true fires on every track change as well, so this is
        # only meaningful once the grace period expires with no audio back.
        self._stop_ended = False
        self._pending: Deque[QueueRecord] = deque()
        self._player = xbmc.Player()
        self._playlist = xbmc.PlayList(MUSIC_PLAYLIST)
        # Cached rather than read on demand: the last drain of the session runs
        # after Kodi has deregistered the addon, and constructing an Addon then
        # throws "Unknown addon id". Refreshed by onSettingsChanged.
        self._keep = settings.keep()

    def onSettingsChanged(self) -> None:
        self._keep = settings.keep()
        log.debug("keeping %d queue(s)", self._keep)

    # ------------------------------------------------------------------ Kodi

    def onNotification(self, sender: str, method: str, data: str) -> None:
        """Runs on Kodi's announcement thread — stay cheap, never block.

        A capture here only copies in-memory state onto a queue; the file write
        happens on the service thread in :meth:`_drain`.
        """
        try:
            payload: Dict[str, Any] = json.loads(data) if data else {}
        except ValueError:
            payload = {}

        if method == "Playlist.OnClear":
            if payload.get("playlistid") != MUSIC_PLAYLIST:
                return
            # Fires before Player.OnStop, while the shadow still holds the
            # outgoing queue. After this the tracks are gone from Kodi, so drop
            # them here too rather than letting a later commit resurrect them.
            self._capture("queue replaced")
            with self._lock:
                self._items = []
                self._position = 0
                self._tick = 0.0
                self._armed = False
                # This supersedes any stop still in its grace period: the
                # OnStop that follows a replacement is about the same event.
                self._stop_at = None
                self._stop_ended = False

        elif method in ("Playlist.OnAdd", "Playlist.OnRemove"):
            if payload.get("playlistid") != MUSIC_PLAYLIST:
                return
            with self._lock:
                self._refresh_at = time.time() + REFRESH_DELAY

        elif method == "Player.OnStop":
            # No playerid in this payload — the item type is the only thing
            # that says whether it was music that stopped.
            item = payload.get("item") or {}
            if item.get("type") != "song":
                return
            # Might be the queue dying, might just be the next track starting.
            # Decided in _settle_stop once the grace period is up.
            # ``end`` is true on every natural track end, not only the last
            # one — see Application.cpp GUI_MSG_PLAYBACK_ENDED. Combined with
            # "nothing after this track" it is what marks a finished queue.
            with self._lock:
                self._stop_at = time.time() + STOP_GRACE
                self._stop_ended = bool(payload.get("end"))

        elif method == "System.OnQuit":
            self._capture("Kodi quitting")
            self._drain()

    # --------------------------------------------------------------- shadow

    def _refresh(self) -> None:
        """Re-read the queue from Kodi."""
        result = jsonrpc.call(
            "Playlist.GetItems",
            playlistid=MUSIC_PLAYLIST,
            properties=SNAPSHOT_PROPERTIES,
        )
        raw = (result or {}).get("items") or []
        tracks = [
            Track.from_playlist_item(item)
            for item in raw
            if isinstance(item, dict) and item.get("file")
        ]
        with self._lock:
            if [t.key for t in tracks] == [t.key for t in self._items]:
                return  # same queue — keep the position we already have
            self._items = tracks
            self._position = 0
            self._tick = 0.0
            # A different queue has no sampled position yet. Until the tick
            # loop gives it one, there is nothing worth saving.
            self._armed = False
        log.debug("queue is now %d track(s)", len(tracks))

    def _maybe_refresh(self) -> None:
        with self._lock:
            due = self._refresh_at is not None and time.time() >= self._refresh_at
            if due:
                self._refresh_at = None
        if due:
            self._refresh()

    def _settle_stop(self) -> None:
        """Decide what a Player.OnStop meant, now the grace period is up.

        Audio playing again means the queue survived — the stop was a track
        change, or a new queue taking over (which OnClear will already have
        captured). Silence means the queue really has been left behind: a video
        took over, or playback was stopped, or it ran off the end. That case
        never fires OnClear, because the tracks are still sitting in Kodi's
        playlist; what was lost is the position, which is what we hold.
        """
        with self._lock:
            due = self._stop_at is not None and time.time() >= self._stop_at
            ended = self._stop_ended
            if due:
                self._stop_at = None
                self._stop_ended = False
        if not due:
            return
        try:
            if self._player.isPlayingAudio():
                return
        except Exception:  # noqa: BLE001
            pass
        self._capture("playback stopped", ended=ended)

    def _locate(self, playing: str, position: int, tick: float) -> Optional[int]:
        """Which track of the shadow is playing, or None if it cannot be placed.

        ``getposition()`` cannot be taken at face value. It reports the
        *playlist player's* current index rather than an index into the
        playlist object it was called on, and starting a video switches the
        playlist player to the video playlist — and its index to 0 — several
        seconds before the music actually stops. Every sample in that window
        says "track 1" while the music still coming out of the speakers is
        track 4, and the capture that follows saves the wrong track.

        Measured on Kodi 22.0-BETA1 (Android): with 16 songs queued and track 4
        playing, ``PlayList(1).getposition()`` returned 3 while
        ``PlayList(1).size()`` was 0 — the video playlist was empty and still
        reported the music playlist's index. A second later, with the video
        opened and the music still audible, both playlists reported 0.

        The file the player is actually on has no such ambiguity, so when the
        reported position moves it is the file that decides.

        Caller holds the lock.
        """
        count = len(self._items)
        if not count:
            return None
        if position == self._position and position < count:
            return position  # nothing moved; no need to look any further

        matches = [i for i, track in enumerate(self._items) if track.file == playing]
        if matches:
            # A queue can hold the same song twice, so prefer the copy nearest
            # to where playback already was.
            return min(matches, key=lambda i: abs(i - self._position))

        # Not a file we are holding: a plugin that resolved to a different URL,
        # most likely. Fall back to the reported position, except when it has
        # jumped backwards while the elapsed time kept climbing — one track
        # cannot move, so that is another playlist taking the player over.
        if position >= count:
            return None
        if position < self._position and tick > self._tick:
            return None
        return position

    def _sample(self) -> Optional[int]:
        """Read position and elapsed time off the player. No JSON-RPC.

        Returns the reconciled music-playlist index when a sample was taken,
        so the caller can confirm the track that is playing and the one
        after it without doing that work in here.
        """
        try:
            if not self._player.isPlayingAudio():
                return None
            tick = float(self._player.getTime())
            position = int(self._playlist.getposition())
            playing = str(self._player.getPlayingFile())
        except Exception:  # noqa: BLE001 - playback can end mid-call
            return None
        if position < 0:
            return None
        with self._lock:
            index = self._locate(playing, position, tick)
            if index is None:
                return None
            self._position = index
            self._tick = max(0.0, tick)
            self._armed = True
        return index

    def _confirm_near(self, index: int) -> None:
        """Point the playing track and the next one at the live library row.

        Restore leaves every Jellyfin track but the resume one pending, so
        this is what checks them as they come up. An item that is not
        pending costs a property read. Runs on the service thread, outside
        the sample lock, because the check is a library call.
        """
        try:
            size = int(self._playlist.size())
        except Exception:  # noqa: BLE001
            return
        for slot in (index, index + 1):
            if 0 <= slot < size:
                try:
                    restore.confirm_playlist_slot(self._playlist, slot, with_art=True)
                except Exception as exc:  # noqa: BLE001
                    log.error("confirming track %d failed: %s", slot, exc)

    # -------------------------------------------------------------- capture

    def _capture(self, reason: str, ended: bool = False) -> bool:
        """Copy the shadow onto the pending queue, if it is worth saving."""
        with self._lock:
            if not self._armed or not self._items:
                return False
            unplayed = max(0, len(self._items) - self._position - 1)
            record = QueueRecord(
                tracks=list(self._items),
                position=self._position,
                tick=self._tick,
                saved=time.time(),
                completed=bool(ended and unplayed == 0),
            )
            self._armed = False
        self._pending.append(record)
        log.info(
            "captured %d track(s) at %d/%d, %.0fs in (%s)%s",
            record.total,
            record.position + 1,
            record.total,
            record.tick,
            reason,
            " (completed)" if record.completed else "",
        )
        return True

    def _drain(self) -> None:
        """Write anything captured. Runs on the service thread."""
        while self._pending:
            history.add(self._pending.popleft(), self._keep)

    # -------------------------------------------------------------- startup

    def _restore_on_startup(self) -> None:
        """Put the newest queue back, once Kodi is up enough to play it."""
        deadline = time.time() + STARTUP_WAIT
        while time.time() < deadline:
            if xbmc.getCondVisibility("Window.IsActive(home)"):
                break
            if self.waitForAbort(1.0):
                return
        records = history.load()
        if not records:
            return
        log.info("restoring the newest queue on startup")
        restore.restore(
            records[0], settings.start_paused(), settings.from_track_start()
        )

    # ----------------------------------------------------------------- loop

    def run(self) -> None:
        # A service restart (an addon enable/disable bounce) can land while
        # music is playing, so adopt whatever is already queued.
        self._refresh()

        if settings.restore_on_startup():
            self._restore_on_startup()

        log.info("recorder running; history at %s", history.store_path())

        ticks = 0
        while not self.waitForAbort(POLL_INTERVAL):
            ticks += 1
            self._maybe_refresh()
            self._settle_stop()
            if ticks % SAMPLE_EVERY == 0:
                heard = self._sample()
                if heard is not None:
                    self._confirm_near(heard)
            self._drain()

        # Kodi is going down and the queue goes with it. Usually already done
        # from System.OnQuit; the armed flag makes the second call a no-op.
        self._capture("Kodi shutting down")
        self._drain()
        log.info("recorder stopped")
