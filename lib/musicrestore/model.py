"""The recorded queue and its tracks.

Everything needed to draw a history row is denormalised onto the record, so the
list opens without touching the music database — and a queue whose songs have
since been removed from the library still renders as something recognisable
rather than a blank row.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1


@dataclass
class Track:
    """One entry of a recorded queue.

    ``file`` is what playback actually uses. ``songid`` is kept alongside it so
    a restored item is recognised as the library song it came from (play counts,
    art, the info dialog) rather than a loose file.
    """

    file: str
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: int = 0
    songid: Optional[int] = None
    thumb: str = ""

    @property
    def key(self) -> str:
        """Identity for comparing two queues. Library id where there is one."""
        return "song:%d" % self.songid if self.songid else "file:%s" % self.file

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"file": self.file}
        for name in ("title", "artist", "album", "thumb"):
            value = getattr(self, name)
            if value:
                data[name] = value
        if self.duration:
            data["duration"] = self.duration
        if self.songid:
            data["songid"] = self.songid
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Track":
        return cls(
            file=str(data.get("file", "")),
            title=str(data.get("title", "")),
            artist=str(data.get("artist", "")),
            album=str(data.get("album", "")),
            duration=int(data.get("duration", 0) or 0),
            songid=data.get("songid") or None,
            thumb=str(data.get("thumb", "")),
        )

    @classmethod
    def from_playlist_item(cls, item: Dict[str, Any]) -> "Track":
        """Build from one entry of a ``Playlist.GetItems`` response."""

        def first(value: Any) -> str:
            if isinstance(value, list):
                return str(value[0]) if value else ""
            return str(value or "")

        songid = item.get("id") if item.get("type") == "song" else None
        return cls(
            file=str(item.get("file", "")),
            title=str(item.get("title") or item.get("label") or ""),
            # Prefer the album artist: it is what names a queue, and track
            # artists on a compilation differ row to row.
            artist=first(item.get("albumartist")) or first(item.get("artist")),
            album=str(item.get("album", "")),
            duration=int(item.get("duration", 0) or 0),
            songid=int(songid) if songid else None,
            thumb=str(item.get("thumbnail", "")),
        )


@dataclass
class QueueRecord:
    """A music queue as it was at the moment it was taken away."""

    tracks: List[Track] = field(default_factory=list)
    position: int = 0
    tick: float = 0.0
    saved: float = 0.0

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
    def signature(self) -> Tuple[str, ...]:
        """Track identity of the queue, ignoring where playback had got to."""
        return tuple(track.key for track in self.tracks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "saved": round(self.saved, 3),
            "position": self.position,
            "tick": round(self.tick, 3),
            "tracks": [track.to_dict() for track in self.tracks],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QueueRecord":
        raw: Sequence[Any] = data.get("tracks") or []
        return cls(
            tracks=[Track.from_dict(item) for item in raw if isinstance(item, dict)],
            position=int(data.get("position", 0) or 0),
            tick=float(data.get("tick", 0.0) or 0.0),
            saved=float(data.get("saved", 0.0) or 0.0),
        )
