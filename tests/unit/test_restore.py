"""Restore starts from the record and confirms library ids lazily."""

from typing import Any, Dict, List, Optional, Tuple

from musicrestore.model import QueueRecord, Track
from musicrestore.restore import (
    PENDING,
    SONGID,
    confirmation_order,
    confirm_playlist_slot,
    restore,
)


def _url(n: int) -> str:
    return "http://jelly/Audio/%s/stream.flac?static=true" % format(n, "032x")


def _track(n: int, songid: Optional[int]) -> Track:
    return Track(
        file=_url(n),
        title="T%d" % n,
        artist="A",
        album="Al",
        duration=100,
        songid=songid,
        thumb="image://thumb/%d" % n,
        fanart="image://fan/%d" % n,
    )


class FakeTag:
    def __init__(self) -> None:
        self.dbid: Optional[int] = None
        self.title = ""
        self.artist = ""
        self.album = ""
        self.duration = 0
        self.year = 0
        self.genres: List[str] = []
        self.playcount = 0

    def setDbId(self, dbid: int, typ: str) -> None:
        self.dbid = dbid
        self.media_type = typ

    def setYear(self, year: int) -> None:
        self.year = year

    def setGenres(self, genres: List[str]) -> None:
        self.genres = list(genres)

    def setPlayCount(self, playcount: int) -> None:
        self.playcount = playcount

    def getTitle(self) -> str:
        return self.title

    def getArtist(self) -> str:
        return self.artist

    def getAlbum(self) -> str:
        return self.album

    def getDuration(self) -> int:
        return self.duration


class FakeItem:
    def __init__(self, label: str = "", offscreen: bool = False) -> None:
        self.label = label
        self.offscreen = offscreen
        self.path = ""
        self.props: Dict[str, str] = {}
        self.art: Dict[str, str] = {}
        self.info: Optional[Tuple[str, Dict[str, str]]] = None
        self.tag = FakeTag()

    def setPath(self, path: str) -> None:
        self.path = path

    def getPath(self) -> str:
        return self.path

    def setProperty(self, key: str, value: str) -> None:
        self.props[key] = value

    def getProperty(self, key: str) -> str:
        return self.props.get(key, "")

    def setArt(self, values: Dict[str, str]) -> None:
        self.art.update(values)

    def getArt(self, key: str) -> str:
        return self.art.get(key, "")

    def setInfo(self, kind: str, labels: Dict[str, str]) -> None:
        self.info = (kind, dict(labels))
        self.tag.title = labels.get("title", "")
        self.tag.artist = labels.get("artist", "")
        self.tag.album = labels.get("album", "")
        if "duration" in labels:
            self.tag.duration = int(labels["duration"])
        if "dbid" in labels:
            self.tag.dbid = int(labels["dbid"])

    def getMusicInfoTag(self) -> FakeTag:
        return self.tag


class FakePlaylist:
    def __init__(self) -> None:
        self.items: List[FakeItem] = []

    def clear(self) -> None:
        self.items.clear()

    def add(self, url: str, item: FakeItem, index: int = -1) -> None:
        item.setPath(url)
        self.items.append(item)

    def __getitem__(self, index: int) -> FakeItem:
        return self.items[index]

    def size(self) -> int:
        return len(self.items)


class FakePlayer:
    def __init__(self, events: List[str]) -> None:
        self.events = events
        self.started: Optional[int] = None
        self.audio = True

    def play(self, playlist: FakePlaylist, startpos: int = -1) -> None:
        self.started = startpos
        self.events.append("play")

    def isPlayingAudio(self) -> bool:
        return self.audio


class Rig:
    def __init__(self, monkeypatch: Any, rows: Dict[int, Dict[str, Any]]) -> None:
        self.events: List[str] = []
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self.playlist = FakePlaylist()
        self.player = FakePlayer(self.events)
        self.rows = rows
        monkeypatch.setattr(restore_mod.xbmc, "PlayList", lambda _which: self.playlist)
        monkeypatch.setattr(restore_mod.xbmcgui, "ListItem", FakeItem)
        monkeypatch.setattr(restore_mod.xbmc, "Player", lambda: self.player)
        monkeypatch.setattr(restore_mod.xbmc, "sleep", lambda _ms: None)
        monkeypatch.setattr(restore_mod.jsonrpc, "invoke", self.invoke)

    def invoke(
        self, method: str, **params: Any
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        self.events.append(method)
        self.calls.append((method, params))
        if method == "AudioLibrary.GetSongDetails":
            row = self.rows.get(params["songid"])
            if row is None:
                return None, {"message": "Not found"}
            song = dict(row)
            if "art" not in params["properties"]:
                song.pop("art", None)
            return {"songdetails": song}, None
        if method == "AudioLibrary.GetSongs":
            wanted = params["filter"]["value"]
            for row in self.rows.values():
                if wanted in row["file"]:
                    song = dict(row)
                    if "art" not in params["properties"]:
                        song.pop("art", None)
                    return {"songs": [song]}, None
            return {"songs": []}, None
        if method == "Player.PlayPause":
            return {"speed": 0}, None
        raise AssertionError(method)


import musicrestore.restore as restore_mod  # noqa: E402


def _rows(*tracks: Track) -> Dict[int, Dict[str, Any]]:
    rows = {}
    for track in tracks:
        assert track.songid is not None
        rows[track.songid] = {
            "songid": track.songid,
            "file": track.file,
            "year": 1977,
            "genre": ["Rock"],
            "playcount": 4,
            "art": {"clearlogo": "image://logo/%d" % track.songid},
        }
    return rows


def _record(tracks: List[Track], position: int = 0, tick: float = 30.0) -> QueueRecord:
    return QueueRecord(tracks=tracks, position=position, tick=tick, saved=1.0)


class TestRestoreOrder:
    def test_play_precedes_every_lookup_but_the_resume_track(
        self, monkeypatch: Any
    ) -> None:
        tracks = [_track(n, 100 + n) for n in range(3)]
        rig = Rig(monkeypatch, _rows(*tracks))

        assert restore(_record(tracks), from_track_start=False) is True

        # The resume track before play, then the rest in order once audio
        # has started. Only the next track asks for art.
        assert rig.events == [
            "AudioLibrary.GetSongDetails",
            "play",
            "AudioLibrary.GetSongDetails",
            "AudioLibrary.GetSongDetails",
        ]
        assert rig.player.started == 0
        assert [call[1]["songid"] for call in rig.calls] == [100, 101, 102]
        assert "art" in rig.calls[0][1]["properties"]
        assert "art" in rig.calls[1][1]["properties"]
        assert rig.calls[2][1]["properties"] == ["file", "year", "genre", "playcount"]
        assert "GetSongs" not in "".join(name for name, _ in rig.calls)

        resume, nxt, later = rig.playlist.items
        assert resume.info is not None and resume.info[1]["dbid"] == "100"
        assert resume.props.get(PENDING, "") == ""
        assert resume.art["clearlogo"] == "image://logo/100"
        assert resume.art["thumb"] == "image://thumb/0"
        assert resume.art["fanart"] == "image://fan/0"
        assert resume.props["StartOffset"] == "30.0"
        assert resume.tag.year == 1977
        assert resume.tag.genres == ["Rock"]
        assert resume.tag.playcount == 4
        assert later.tag.year == 1977
        assert later.tag.playcount == 4

        assert "dbid" not in (nxt.info[1] if nxt.info else {})
        # Both later tracks are matched after play. Art was asked for the
        # next one only.
        assert nxt.tag.dbid == 101
        assert nxt.props.get(PENDING, "") == ""
        assert nxt.art["clearlogo"] == "image://logo/101"
        assert later.tag.dbid == 102
        assert later.props.get(PENDING, "") == ""
        assert "clearlogo" not in later.art

    def test_the_walk_wraps_to_the_tracks_before_the_one_that_started(
        self, monkeypatch: Any
    ) -> None:
        tracks = [_track(n, 100 + n) for n in range(4)]
        rig = Rig(monkeypatch, _rows(*tracks))

        assert restore(_record(tracks, position=2, tick=40.0)) is True

        assert [call[1]["songid"] for call in rig.calls] == [102, 103, 100, 101]
        assert rig.events[1] == "play"
        assert "art" in rig.calls[1][1]["properties"]
        assert rig.calls[2][1]["properties"] == ["file", "year", "genre", "playcount"]
        assert rig.calls[3][1]["properties"] == ["file", "year", "genre", "playcount"]
        assert [item.tag.dbid for item in rig.playlist.items] == [100, 101, 102, 103]
        assert all(item.props.get(PENDING, "") == "" for item in rig.playlist.items)

    def test_a_reused_songid_is_found_by_the_path_scan(self, monkeypatch: Any) -> None:
        tracks = [_track(1, 50)]
        # The stored id now points at a different Jellyfin item. The scan
        # finds the real row.
        rows = {
            50: {
                "songid": 50,
                "file": _url(99),
                "art": {"clearlogo": "image://wrong/"},
            },
            7: {
                "songid": 7,
                "file": tracks[0].file,
                "year": 1980,
                "genre": ["Metal"],
                "playcount": 2,
                "art": {"clearlogo": "image://right/"},
            },
        }
        rig = Rig(monkeypatch, rows)

        assert restore(_record(tracks, tick=1.0)) is True

        assert [name for name, _ in rig.calls] == [
            "AudioLibrary.GetSongDetails",
            "AudioLibrary.GetSongs",
        ]
        scan = rig.calls[1][1]
        assert scan["includesingles"] is True
        assert scan["filter"]["value"] == format(1, "032x")
        assert "art" in scan["properties"]
        item = rig.playlist.items[0]
        assert item.info is not None and item.info[1]["dbid"] == "7"
        assert item.art["clearlogo"] == "image://right/"
        assert item.tag.year == 1980
        assert item.tag.genres == ["Metal"]
        assert item.tag.playcount == 2
        assert item.props.get("StartOffset") is None  # 1s is below the offset

    def test_a_local_file_is_not_looked_up(self, monkeypatch: Any) -> None:
        local = Track(
            file="/music/track.flac",
            title="Local",
            songid=4,
            duration=80,
            thumb="image://t/",
        )
        rig = Rig(monkeypatch, {})

        assert restore(_record([local], tick=10.0)) is True

        assert rig.calls == []
        assert rig.player.started == 0
        item = rig.playlist.items[0]
        assert item.info is not None and item.info[1]["dbid"] == "4"
        assert item.props.get(PENDING, "") == ""

    def test_nothing_playable_does_not_open_the_player(self, monkeypatch: Any) -> None:
        rig = Rig(monkeypatch, {})
        assert restore(_record([Track(file="")])) is False
        assert rig.player.started is None
        assert rig.playlist.items == []

    def test_an_unreachable_library_still_plays_and_stays_pending(
        self, monkeypatch: Any
    ) -> None:
        tracks = [_track(1, 50), _track(2, 51)]

        def down(
            method: str, **params: Any
        ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
            return None, {"message": "down"}

        rig = Rig(monkeypatch, {})
        monkeypatch.setattr(restore_mod.jsonrpc, "invoke", down)

        assert restore(_record(tracks)) is True
        assert rig.player.started == 0
        resume, nxt = rig.playlist.items
        # Could not ask, so the recorded id is what plays, and both stay
        # pending for another try.
        assert resume.info is not None and resume.info[1]["dbid"] == "50"
        assert resume.props[PENDING] == "1"
        assert nxt.props[PENDING] == "1"
        assert nxt.tag.dbid is None


class TestConfirmationOrder:
    def test_starts_at_the_next_track_and_wraps(self) -> None:
        assert confirmation_order(5, 2) == [3, 4, 0, 1]

    def test_from_the_top_goes_straight_to_the_end(self) -> None:
        assert confirmation_order(3, 0) == [1, 2]

    def test_the_last_track_wraps_to_the_start(self) -> None:
        assert confirmation_order(4, 3) == [0, 1, 2]

    def test_a_single_track_has_nothing_left(self) -> None:
        assert confirmation_order(1, 0) == []


class TestConfirmLater:
    def test_a_pending_item_takes_one_details_call(self, monkeypatch: Any) -> None:
        track = _track(3, 80)
        rig = Rig(monkeypatch, _rows(track))
        item = FakeItem()
        item.setPath(track.file)
        item.setProperty(SONGID, "80")
        item.setProperty(PENDING, "1")
        item.tag.title = "T3"
        item.art = {"thumb": "image://thumb/3", "fanart": "image://fan/3"}
        rig.playlist.items.append(item)

        confirm_playlist_slot(rig.playlist, 0, with_art=True)  # type: ignore[arg-type]

        assert [name for name, _ in rig.calls] == ["AudioLibrary.GetSongDetails"]
        assert item.tag.dbid == 80
        assert item.props[PENDING] == ""
        assert item.art["clearlogo"] == "image://logo/80"
        assert item.art["thumb"] == "image://thumb/3"

        rig.calls.clear()
        confirm_playlist_slot(rig.playlist, 0, with_art=True)  # type: ignore[arg-type]
        assert rig.calls == []
