"""The recorded queue and its tracks.

Everything needed to draw a history row is denormalised onto the record, so the
list opens without touching the music database — and a queue whose songs have
since been removed from the library still renders as something recognisable
rather than a blank row.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1

# A record captured before we stored ``completed`` still counts as finished
# when it was left on the last track, within this many seconds of the end.
FINISHED_SLOP = 2.0


@dataclass
class Track:
    """One entry of a recorded queue.

    ``file`` is what playback actually uses. ``songid`` is kept alongside it so
    a restored item is recognised as the library song it came from (play counts,
    the info dialog) rather than a loose file.

    ``thumb`` and ``fanart`` are stored because a restored item gets exactly the
    art we put on it and no more — Kodi does not fill library art in for a track
    that plays from an http(s) URL, songid or not. See ``restore._list_item``.
    Only those two are kept. The rest of the library's art map is fetched for
    the track that is about to play, and the whole history is parsed to open
    the dialog, so a row here is not the place for fourteen image URLs per track.
    """

    file: str
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: int = 0
    songid: Optional[int] = None
    thumb: str = ""
    fanart: str = ""
    year: int = 0
    genres: Tuple[str, ...] = ()
    playcount: int = 0
    tracknumber: int = 0
    discnumber: int = 0

    @property
    def key(self) -> str:
        """Identity for comparing two queues. Library id where there is one."""
        return "song:%d" % self.songid if self.songid else "file:%s" % self.file

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"file": self.file}
        for name in ("title", "artist", "album", "thumb", "fanart"):
            value = getattr(self, name)
            if value:
                data[name] = value
        if self.duration:
            data["duration"] = self.duration
        if self.songid:
            data["songid"] = self.songid
        for name in ("year", "playcount", "tracknumber", "discnumber"):
            value = getattr(self, name)
            if value:
                data[name] = value
        if self.genres:
            data["genres"] = list(self.genres)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Track":
        raw_genres = data.get("genres")
        if isinstance(raw_genres, list):
            genres = tuple(str(value) for value in raw_genres if value)
        elif isinstance(raw_genres, str) and raw_genres:
            genres = (raw_genres,)
        else:
            genres = ()
        return cls(
            file=str(data.get("file", "")),
            title=str(data.get("title", "")),
            artist=str(data.get("artist", "")),
            album=str(data.get("album", "")),
            duration=int(data.get("duration", 0) or 0),
            songid=data.get("songid") or None,
            thumb=str(data.get("thumb", "")),
            fanart=str(data.get("fanart", "")),
            year=int(data.get("year", 0) or 0),
            genres=genres,
            playcount=int(data.get("playcount", 0) or 0),
            tracknumber=int(data.get("tracknumber", 0) or 0),
            discnumber=int(data.get("discnumber", 0) or 0),
        )

    @classmethod
    def from_playlist_item(cls, item: Dict[str, Any]) -> "Track":
        """Build from one entry of a ``Playlist.GetItems`` response."""

        def first(value: Any) -> str:
            if isinstance(value, list):
                return str(value[0]) if value else ""
            return str(value or "")

        songid = item.get("id") if item.get("type") == "song" else None
        # Callers ask for either the flat ``fanart`` field or the whole ``art``
        # map, depending on how much of it they have a use for. Take whichever
        # arrived.
        raw_art = item.get("art")
        art: Dict[str, Any] = raw_art if isinstance(raw_art, dict) else {}
        raw_genres = item.get("genre") or item.get("genres") or []
        if isinstance(raw_genres, list):
            genres = tuple(str(value) for value in raw_genres if value)
        elif isinstance(raw_genres, str):
            genres = (raw_genres,) if raw_genres else ()
        else:
            genres = ()
        return cls(
            file=str(item.get("file", "")),
            title=str(item.get("title") or item.get("label") or ""),
            # Prefer the album artist: it is what names a queue, and track
            # artists on a compilation differ row to row.
            artist=first(item.get("albumartist")) or first(item.get("artist")),
            album=str(item.get("album", "")),
            duration=int(item.get("duration", 0) or 0),
            songid=int(songid) if songid else None,
            thumb=str(item.get("thumbnail") or art.get("thumb") or ""),
            fanart=str(item.get("fanart") or art.get("fanart") or ""),
            year=int(item.get("year", 0) or 0),
            genres=genres,
            playcount=int(item.get("playcount", 0) or 0),
            tracknumber=int(item.get("track", 0) or item.get("tracknumber", 0) or 0),
            discnumber=int(item.get("disc", 0) or item.get("discnumber", 0) or 0),
        )


@dataclass
class QueueRecord:
    """A music queue as it was at the moment it was taken away."""

    tracks: List[Track] = field(default_factory=list)
    position: int = 0
    tick: float = 0.0
    saved: float = 0.0
    completed: bool = False

    @property
    def total(self) -> int:
        return len(self.tracks)

    @property
    def unplayed(self) -> int:
        """Tracks after the one that was playing. Assumes linear playback."""
        return max(0, self.total - self.position - 1)

    @property
    def current(self) -> Optional[Track]:
        if 0 <= self.position < self.total:
            return self.tracks[self.position]
        return None

    @property
    def finished(self) -> bool:
        """True if this queue played through rather than being interrupted.

        New captures set ``completed`` when ``Player.OnStop`` arrives with
        ``end: true`` on the last track. Older rows have no flag, so a last
        track left within ``FINISHED_SLOP`` seconds of its duration is treated
        the same — otherwise restoring one starts at the last second of the
        last track and it ends again immediately.
        """
        if self.completed:
            return True
        if self.unplayed > 0:
            return False
        current = self.current
        if current is None or not current.duration:
            return False
        return self.tick >= max(0.0, float(current.duration) - FINISHED_SLOP)

    @property
    def signature(self) -> Tuple[str, ...]:
        """Track identity of the queue, ignoring where playback had got to."""
        return tuple(track.key for track in self.tracks)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "saved": round(self.saved, 3),
            "position": self.position,
            "tick": round(self.tick, 3),
            "tracks": [track.to_dict() for track in self.tracks],
        }
        if self.completed:
            data["completed"] = True
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QueueRecord":
        raw: Sequence[Any] = data.get("tracks") or []
        return cls(
            tracks=[Track.from_dict(item) for item in raw if isinstance(item, dict)],
            position=int(data.get("position", 0) or 0),
            tick=float(data.get("tick", 0.0) or 0.0),
            saved=float(data.get("saved", 0.0) or 0.0),
            completed=bool(data.get("completed")),
        )


@dataclass(frozen=True)
class QueuePreview:
    """Everything the dialog needs before a queue is selected."""

    saved: float
    position: int
    total: int
    tick: float
    finished: bool
    thumb: str
    title_id: int
    title_args: Tuple[str, ...]

    @property
    def unplayed(self) -> int:
        return max(0, self.total - self.position - 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "saved": self.saved,
            "position": self.position,
            "total": self.total,
            "tick": self.tick,
            "finished": self.finished,
            "thumb": self.thumb,
            "title_id": self.title_id,
            "title_args": list(self.title_args),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QueuePreview":
        args = data.get("title_args")
        if not isinstance(args, list):
            raise ValueError("invalid preview title")
        return cls(
            saved=float(data["saved"]),
            position=int(data["position"]),
            total=int(data["total"]),
            tick=float(data["tick"]),
            finished=bool(data["finished"]),
            thumb=str(data.get("thumb") or ""),
            title_id=int(data["title_id"]),
            title_args=tuple(str(arg) for arg in args),
        )


def playback_start(
    record: QueueRecord, from_track_start: bool = False
) -> Tuple[int, float]:
    """Where a restore should begin: ``(position, tick)``.

    A finished queue starts at the first track, 0:00 — not the last second of
    the last track, which is where the capture actually sat. ``from_track_start``
    keeps the track and drops only the offset.
    """
    if record.finished:
        return 0, 0.0
    position = record.position if 0 <= record.position < record.total else 0
    tick = 0.0 if from_track_start else max(0.0, record.tick)
    return position, tick
