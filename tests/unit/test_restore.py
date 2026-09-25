"""Playback-first restore, library ID validation and native queue building."""

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from musicrestore.model import QueueRecord, Track
from musicrestore.restore import (
    BUILDING,
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
    def __init__(
        self, label: str = "", path: str = "", offscreen: bool = False
    ) -> None:
        self.label = label
        self.offscreen = offscreen
        self.path = path
        self.props: Dict[str, str] = {}
        self.art: Dict[str, str] = {}
        self.info: Optional[Tuple[str, Dict[str, str]]] = None
        self.tag = FakeTag()

    def setLabel(self, label: str) -> None:
        self.label = label

    def getLabel(self) -> str:
        return self.label

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

    def setInfo(self, kind: str, labels: Dict[str, Any]) -> None:
        self.info = (kind, dict(labels))
        self.tag.title = labels.get("title", "")
        self.tag.artist = labels.get("artist", "")
        self.tag.album = labels.get("album", "")
        if "duration" in labels:
            self.tag.duration = int(labels["duration"])
        if "dbid" in labels:
            self.tag.dbid = int(labels["dbid"])
        if "year" in labels:
            self.tag.year = int(labels["year"])
        if "genre" in labels:
            self.tag.genres = list(labels["genre"])
        if "playcount" in labels:
            self.tag.playcount = int(labels["playcount"])

    def getMusicInfoTag(self) -> FakeTag:
        return self.tag


class FakeWindow:
    props: Dict[str, str] = {}

    def __init__(self, window_id: int) -> None:
        self.window_id = window_id

    def setProperty(self, key: str, value: str) -> None:
        FakeWindow.props[key] = value

    def getProperty(self, key: str) -> str:
        return FakeWindow.props.get(key, "")


class FakePlaylist:
    def __init__(self) -> None:
        self.items: List[FakeItem] = []

    def clear(self) -> None:
        self.items.clear()

    def add(self, url: str, item: Optional[FakeItem] = None, index: int = -1) -> None:
        if item is None:
            item = FakeItem(label=url)
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
        self.at_play: List[Tuple[str, Optional[int], str]] = []
        self.at_play_years: List[int] = []

    def play(self, playlist: FakePlaylist, startpos: int = -1) -> None:
        self.started = startpos
        self.at_play = [
            (item.label, item.tag.dbid, item.props.get(PENDING, ""))
            for item in playlist.items
        ]
        self.at_play_years = [item.tag.year for item in playlist.items]
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
        self.cache: Dict[str, Any] = {}
        self.revision = "stable"
        monkeypatch.setattr(restore_mod.xbmc, "PlayList", lambda _which: self.playlist)
        monkeypatch.setattr(restore_mod.xbmcgui, "ListItem", FakeItem)
        monkeypatch.setattr(restore_mod.xbmcgui, "Window", FakeWindow)
        monkeypatch.setattr(restore_mod.xbmc, "Player", lambda: self.player)
        monkeypatch.setattr(restore_mod.xbmc, "sleep", lambda _ms: None)
        monkeypatch.setattr(restore_mod.jsonrpc, "invoke", self.invoke)
        monkeypatch.setattr(restore_mod, "_cache_contents", lambda: self.cache)
        monkeypatch.setattr(restore_mod, "_save_cache", self.save_cache)
        monkeypatch.setattr(
            restore_mod, "_sqlite_rows", lambda _ids, _tracks: ({}, False)
        )
        monkeypatch.setattr(restore_mod, "_native_requests", self.native_requests)

    def native_requests(self, method: str, params: List[Dict[str, Any]]) -> List[bool]:
        outcomes = []
        for entry in params:
            wanted = entry["item"]
            song = self.rows.get(wanted.get("songid")) if "songid" in wanted else None
            if "songid" in wanted and song is None:
                outcomes.append(False)
                continue
            path = song["file"] if song else wanted["file"]
            item = FakeItem(label=song.get("title", "") if song else path, path=path)
            if song:
                artist = song.get("artist", [])
                item.setInfo(
                    "music",
                    {
                        "title": song.get("title", ""),
                        "artist": (
                            artist[0] if isinstance(artist, list) and artist else artist
                        ),
                        "album": song.get("album", ""),
                        "duration": str(song.get("duration", 0)),
                        "dbid": str(song["songid"]),
                        "year": str(song.get("year", 0)),
                        "genre": song.get("genre", []),
                        "playcount": str(song.get("playcount", 0)),
                    },
                )
            if method == "Playlist.Add":
                self.playlist.items.append(item)
            else:
                self.playlist.items.insert(entry["position"], item)
            outcomes.append(True)
        return outcomes

    def save_cache(
        self,
        revision: Optional[Tuple[str, ...]],
        item_ids: List[str],
        found: Dict[str, Dict[str, Any]],
    ) -> None:
        if revision is None:
            return
        songs = self.cache.get("songs", {})
        for item_id in item_ids:
            songs[item_id] = found.get(item_id)
        self.cache = {"version": 1, "revision": list(revision), "songs": songs}

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
        if method == "AudioLibrary.GetProperties":
            return {"librarylastupdated": self.revision}, None
        if method == "AudioLibrary.GetSongs":
            all_songs = [dict(row) for row in self.rows.values()]
            if "filter" in params:
                wanted = params["filter"]["value"]
                songs = [
                    song for song in all_songs if wanted in str(song.get("file") or "")
                ][:1]
                return {"songs": songs}, None
            for song in all_songs:
                song.pop("art", None)
            limits = params["limits"]
            songs = all_songs[limits["start"] : limits["end"]]
            return {"songs": songs, "limits": {"total": len(all_songs)}}, None
        if method == "Player.PlayPause":
            return {"speed": 0}, None
        if method == "Playlist.Add":
            self.native_requests(
                method,
                [
                    {"playlistid": params["playlistid"], "item": item}
                    for item in params["item"]
                ],
            )
            return "OK", None  # type: ignore[return-value]
        raise AssertionError(method)


import musicrestore.restore as restore_mod  # noqa: E402


def _rows(*tracks: Track) -> Dict[int, Dict[str, Any]]:
    rows = {}
    for track in tracks:
        assert track.songid is not None
        rows[track.songid] = {
            "songid": track.songid,
            "file": track.file,
            "title": "Library %s" % track.title,
            "artist": [track.artist],
            "album": track.album,
            "duration": track.duration,
            "year": 1977,
            "genre": ["Rock"],
            "playcount": 4,
            "art": {"clearlogo": "image://logo/%d" % track.songid},
        }
    return rows


def _record(tracks: List[Track], position: int = 0, tick: float = 30.0) -> QueueRecord:
    return QueueRecord(tracks=tracks, position=position, tick=tick, saved=1.0)


class TestRestoreOrder:
    def test_two_library_items_are_tagged_before_play(self, monkeypatch: Any) -> None:
        tracks = [_track(n, 100 + n) for n in range(3)]
        rig = Rig(monkeypatch, _rows(*tracks))

        assert restore(_record(tracks), from_track_start=False) is True

        assert rig.events[:3] == [
            "AudioLibrary.GetSongDetails",
            "AudioLibrary.GetSongDetails",
            "play",
        ]
        assert rig.events.count("AudioLibrary.GetSongs") == 1
        assert rig.player.started == 0
        query = next(
            params for name, params in rig.calls if name == "AudioLibrary.GetSongs"
        )
        assert query["includesingles"] is True
        assert "art" not in query["properties"]
        assert "filter" not in query
        assert query["limits"] == {"start": 0, "end": 1000}
        assert [item.path for item in rig.playlist.items] == [
            track.file for track in tracks
        ]
        assert [item.tag.dbid for item in rig.playlist.items] == [100, 101, 102]
        assert [item.label for item in rig.playlist.items] == ["T0", "T1", "Library T2"]
        assert rig.player.at_play == [("T0", 100, ""), ("T1", 101, "")]
        assert rig.player.at_play_years == [1977, 1977]
        assert rig.playlist.items[0].offscreen
        assert rig.playlist.items[0].props["StartOffset"] == "30.0"
        assert rig.playlist.items[0].tag.year == 1977
        assert rig.playlist.items[2].tag.playcount == 4
        assert FakeWindow.props[BUILDING] == ""

    def test_resume_position_keeps_queue_order(self, monkeypatch: Any) -> None:
        tracks = [_track(n, 100 + n) for n in range(4)]
        rig = Rig(monkeypatch, _rows(*tracks))

        assert restore(_record(tracks, position=2, tick=40.0)) is True

        assert rig.events.index("play") == 2
        assert rig.events.count("AudioLibrary.GetSongs") == 1
        assert rig.player.started == 0
        assert rig.player.at_play == [("T2", 102, ""), ("T3", 103, "")]
        assert [item.tag.dbid for item in rig.playlist.items] == [100, 101, 102, 103]
        assert rig.playlist.items[2].props["StartOffset"] == "40.0"
        assert [item.path for item in rig.playlist.items] == [
            track.file for track in tracks
        ]

    def test_next_item_is_fully_tagged_before_nearly_finished_resume(
        self, monkeypatch: Any
    ) -> None:
        tracks = [_track(n, 100 + n) for n in range(3)]
        rig = Rig(monkeypatch, _rows(*tracks))

        assert restore(_record(tracks, position=1, tick=99.8)) is True

        assert rig.player.at_play == [("T1", 101, ""), ("T2", 102, "")]
        assert rig.player.at_play_years == [1977, 1977]
        assert rig.playlist.items[1].props["StartOffset"] == "99.8"
        assert rig.playlist.items[2].tag.genres == ["Rock"]

    def test_a_reused_songid_is_replaced_by_the_path_query(
        self, monkeypatch: Any
    ) -> None:
        tracks = [_track(1, 50)]
        # The stored id now points at a different Jellyfin item. The one
        # query is by the id in the file, and that row is what gets stamped.
        rows = {
            50: {
                "songid": 50,
                "file": _url(99),
                "title": "Wrong",
            },
            7: {
                "songid": 7,
                "file": tracks[0].file,
                "title": "Right",
                "year": 1980,
                "genre": ["Metal"],
                "playcount": 2,
            },
        }
        rig = Rig(monkeypatch, rows)

        assert restore(_record(tracks, tick=1.0)) is True

        assert [name for name, _ in rig.calls].count("AudioLibrary.GetSongs") == 2
        assert rig.player.at_play == [("T1", 7, "")]
        item = rig.playlist.items[0]
        assert item.tag.dbid == 7
        assert item.label == "T1"  # the record's own title wins
        assert item.tag.year == 1980
        assert item.tag.genres == ["Metal"]
        assert item.tag.playcount == 2
        assert item.path == tracks[0].file
        assert item.props.get("StartOffset") is None  # 1s is below the offset

    def test_a_url_stored_as_the_title_is_replaced(self, monkeypatch: Any) -> None:
        track = _track(1, 10)
        rows = _rows(track)
        track.title = track.file
        track.artist = ""
        track.album = ""
        rig = Rig(monkeypatch, rows)

        assert restore(_record([track], tick=9.0)) is True

        item = rig.playlist.items[0]
        assert item.label == "Library T1"
        assert item.info is not None and item.info[1]["artist"] == "A"
        assert item.tag.dbid == 10

    def test_a_song_missing_from_the_library_stays_a_file(
        self, monkeypatch: Any
    ) -> None:
        kept = _track(1, 10)
        gone = _track(2, None)
        rig = Rig(monkeypatch, _rows(kept))

        assert restore(_record([kept, gone], tick=12.0)) is True

        assert [name for name, _ in rig.calls].count("AudioLibrary.GetSongs") == 2
        library, filed = rig.playlist.items
        assert library.path == kept.file
        assert library.tag.dbid == 10
        assert filed.path == gone.file
        assert filed.label == "T2"
        assert filed.props.get(PENDING, "") == ""
        assert filed.tag.dbid is None

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
        assert item.path == "/music/track.flac"
        assert item.info is not None and item.info[1]["dbid"] == "4"
        assert item.props["StartOffset"] == "10.0"

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
        # Could not ask, so the files play and both stay pending. A stored
        # id is not stamped: it may have been reused.
        assert resume.path == tracks[0].file
        assert resume.props[PENDING] == "1"
        assert resume.tag.dbid is None
        assert nxt.props[PENDING] == "1"
        assert nxt.tag.dbid is None
        assert nxt.label == "T2"

    def test_large_queue_pages_library_without_sql_or_filter(
        self, monkeypatch: Any
    ) -> None:
        tracks = [_track(n, 10000 + n) for n in range(1313)]
        tracks[34].year = 1986
        tracks[34].genres = ("Rock", "Pop")
        rig = Rig(monkeypatch, _rows(*tracks))

        assert restore(_record(tracks, position=34, tick=241.0)) is True

        queries = [
            params for name, params in rig.calls if name == "AudioLibrary.GetSongs"
        ]
        assert [query["limits"] for query in queries] == [
            {"start": 0, "end": 1000},
            {"start": 1000, "end": 2000},
        ]
        assert all("filter" not in query for query in queries)
        assert rig.player.started == 0
        assert rig.player.at_play == [("T34", 10034, ""), ("T35", 10035, "")]
        assert rig.player.at_play_years == [1977, 1977]
        assert rig.playlist.items[34].props["StartOffset"] == "241.0"
        assert rig.playlist.items[34].tag.year == 1977
        assert rig.playlist.items[34].tag.genres == ["Rock"]
        assert all(item.tag.dbid is not None for item in rig.playlist.items)
        assert [item.path for item in rig.playlist.items] == [
            track.file for track in tracks
        ]

    def test_warm_cache_populates_resume_before_play_without_scan(
        self, monkeypatch: Any
    ) -> None:
        tracks = [_track(n, 100 + n) for n in range(3)]
        rig = Rig(monkeypatch, _rows(*tracks))
        assert restore(_record(tracks)) is True
        rig.calls.clear()
        rig.events.clear()

        assert restore(_record(tracks)) is True

        assert "AudioLibrary.GetSongs" not in [name for name, _ in rig.calls]
        assert rig.player.at_play == [("T0", 100, ""), ("T1", 101, "")]
        assert [item.tag.dbid for item in rig.playlist.items] == [100, 101, 102]
        assert [item.label for item in rig.playlist.items] == ["T0", "T1", "Library T2"]

    def test_changed_library_revision_invalidates_cached_song_ids(
        self, monkeypatch: Any
    ) -> None:
        track = _track(1, 50)
        rig = Rig(monkeypatch, _rows(track))
        assert restore(_record([track])) is True
        rig.calls.clear()
        rig.events.clear()
        rig.revision = "after repair"
        rig.rows = {7: {**_rows(track)[50], "songid": 7}}

        assert restore(_record([track])) is True

        assert rig.player.at_play == [("T1", 7, "")]
        assert rig.playlist.items[0].tag.dbid == 7
        assert [name for name, _ in rig.calls].count("AudioLibrary.GetSongs") == 2


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


class TestIndexedLibraryLookup:
    def test_uses_bounded_parent_path_queries_for_1313_tracks(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        database = tmp_path / "MyMusic82.db"
        connection = sqlite3.connect(database)
        connection.executescript(
            "CREATE TABLE path(idPath INTEGER PRIMARY KEY,strPath TEXT);"
            "CREATE TABLE song(idSong INTEGER PRIMARY KEY,idPath INTEGER,strFileName TEXT);"
        )
        tracks = [_track(index, None) for index in range(1313)]
        connection.executemany(
            "INSERT INTO path VALUES (?,?)",
            [
                (index + 1, track.file.rsplit("/", 1)[0] + "/")
                for index, track in enumerate(tracks)
            ],
        )
        connection.executemany(
            "INSERT INTO song VALUES (?,?,?)",
            [
                (index + 100, index + 1, "stream.flac?static=true")
                for index in range(1313)
            ],
        )
        connection.commit()
        connection.close()
        monkeypatch.setattr(
            restore_mod.xbmcvfs, "translatePath", lambda _path: str(tmp_path)
        )
        calls: List[int] = []

        def details(method: str, **params: Any) -> Tuple[Dict[str, Any], None]:
            assert method == "AudioLibrary.GetSongDetails"
            songid = params["songid"]
            calls.append(songid)
            return {"songdetails": {"file": tracks[songid - 100].file}}, None

        monkeypatch.setattr(restore_mod.jsonrpc, "invoke", details)
        item_ids = [restore_mod.rebind.jellyfin_audio_id(t.file) for t in tracks]
        rows, usable = restore_mod._sqlite_rows(item_ids, tracks)

        assert usable is True
        assert len(rows) == 1313
        assert len(calls) == 3
        assert rows[item_ids[-1]]["songid"] == 1412

    def test_rejects_stale_local_database(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        database = tmp_path / "MyMusic82.db"
        connection = sqlite3.connect(database)
        connection.executescript(
            "CREATE TABLE path(idPath INTEGER PRIMARY KEY,strPath TEXT);"
            "CREATE TABLE song(idSong INTEGER PRIMARY KEY,idPath INTEGER,strFileName TEXT);"
        )
        track = _track(1, 4)
        connection.execute(
            "INSERT INTO path VALUES (?,?)", (1, track.file.rsplit("/", 1)[0] + "/")
        )
        connection.execute("INSERT INTO song VALUES (?,?,?)", (4, 1, "stream.flac"))
        connection.commit()
        connection.close()
        monkeypatch.setattr(
            restore_mod.xbmcvfs, "translatePath", lambda _path: str(tmp_path)
        )
        monkeypatch.setattr(
            restore_mod.jsonrpc,
            "invoke",
            lambda *_args, **_kwargs: ({"songdetails": {"file": _url(99)}}, None),
        )

        rows, usable = restore_mod._sqlite_rows(
            [restore_mod.rebind.jellyfin_audio_id(track.file)], [track]
        )

        assert rows == {}
        assert usable is False
