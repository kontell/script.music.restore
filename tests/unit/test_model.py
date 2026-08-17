"""Track / QueueRecord: round-tripping and the fields the recorder relies on."""

from musicrestore.model import QueueRecord, Track, playback_start


class TestTrackFromPlaylistItem:
    def test_library_song_keeps_its_id(self):
        track = Track.from_playlist_item(
            {
                "type": "song",
                "id": 1174,
                "file": "https://jelly/Audio/x/stream.flac",
                "title": "Hells Bells",
                "artist": ["AC/DC"],
                "albumartist": ["AC/DC"],
                "album": "Back in Black",
                "duration": 311,
                "thumbnail": "image://x/",
            }
        )
        assert track.songid == 1174
        assert track.title == "Hells Bells"
        assert track.album == "Back in Black"
        assert track.duration == 311
        assert track.key == "song:1174"

    def test_non_library_item_falls_back_to_the_path(self):
        track = Track.from_playlist_item(
            {"type": "unknown", "file": "/music/loose.flac", "label": "Loose"}
        )
        assert track.songid is None
        assert track.title == "Loose"
        assert track.key == "file:/music/loose.flac"

    def test_album_artist_wins_over_track_artist(self):
        # On a compilation the track artists differ row to row; the album
        # artist is what actually names the queue.
        track = Track.from_playlist_item(
            {
                "type": "song",
                "id": 5,
                "file": "/a.flac",
                "artist": ["Guest Vocalist"],
                "albumartist": ["Various Artists"],
            }
        )
        assert track.artist == "Various Artists"

    def test_track_artist_used_when_there_is_no_album_artist(self):
        track = Track.from_playlist_item(
            {"type": "song", "id": 5, "file": "/a.flac", "artist": ["Pixies"]}
        )
        assert track.artist == "Pixies"

    def test_missing_fields_do_not_raise(self):
        track = Track.from_playlist_item({"file": "/a.flac"})
        assert track.title == ""
        assert track.duration == 0
        assert track.songid is None

    def test_zero_id_is_not_treated_as_a_library_song(self):
        track = Track.from_playlist_item({"type": "song", "id": 0, "file": "/a.flac"})
        assert track.songid is None

    def test_flat_fanart_is_kept(self):
        # What the recorder asks Playlist.GetItems for.
        track = Track.from_playlist_item(
            {"type": "song", "id": 5, "file": "/a.flac", "fanart": "image://back/"}
        )
        assert track.fanart == "image://back/"

    def test_fanart_and_thumb_come_off_the_art_map_when_that_is_what_arrived(self):
        # What the restore-side library lookup asks for.
        track = Track.from_playlist_item(
            {
                "type": "song",
                "id": 5,
                "file": "/a.flac",
                "art": {"thumb": "image://cover/", "fanart": "image://back/"},
            }
        )
        assert track.thumb == "image://cover/"
        assert track.fanart == "image://back/"

    def test_a_junk_art_value_does_not_raise(self):
        track = Track.from_playlist_item(
            {"type": "song", "id": 5, "file": "/a.flac", "art": "not a map"}
        )
        assert track.fanart == ""


class TestRoundTrip:
    def test_track_survives_serialisation(self):
        track = Track(
            file="/a.flac",
            title="T",
            artist="A",
            album="Al",
            duration=100,
            songid=7,
            thumb="image://x/",
            fanart="image://back/",
        )
        assert Track.from_dict(track.to_dict()) == track

    def test_empty_fields_are_left_out_of_the_json(self):
        data = Track(file="/a.flac").to_dict()
        assert data == {"file": "/a.flac"}

    def test_record_survives_serialisation(self):
        record = QueueRecord(
            tracks=[Track(file="/a.flac", songid=1), Track(file="/b.flac")],
            position=1,
            tick=70.25,
            saved=1234.5,
        )
        assert QueueRecord.from_dict(record.to_dict()) == record

    def test_completed_is_omitted_when_false(self):
        data = QueueRecord(tracks=[Track(file="/a.flac")]).to_dict()
        assert "completed" not in data

    def test_completed_survives_serialisation(self):
        record = QueueRecord(
            tracks=[Track(file="/a.flac"), Track(file="/b.flac")],
            position=1,
            tick=90.0,
            completed=True,
        )
        loaded = QueueRecord.from_dict(record.to_dict())
        assert loaded.completed is True
        assert loaded.finished is True

    def test_a_record_with_junk_in_it_still_loads(self):
        record = QueueRecord.from_dict(
            {"tracks": [{"file": "/a.flac"}, "not a dict", 7], "position": "2"}
        )
        assert len(record.tracks) == 1
        assert record.position == 2


class TestDerivedFields:
    def test_unplayed_counts_what_comes_after(self):
        record = QueueRecord(
            tracks=[Track(file="/%d.flac" % i) for i in range(10)], position=2
        )
        assert record.total == 10
        assert record.unplayed == 7

    def test_unplayed_never_goes_negative(self):
        record = QueueRecord(tracks=[Track(file="/a.flac")], position=9)
        assert record.unplayed == 0

    def test_current_is_none_when_the_position_is_out_of_range(self):
        record = QueueRecord(tracks=[Track(file="/a.flac")], position=5)
        assert record.current is None

    def test_signature_ignores_position_and_tick(self):
        tracks = [Track(file="/a.flac", songid=1), Track(file="/b.flac")]
        first = QueueRecord(tracks=list(tracks), position=0, tick=0.0)
        second = QueueRecord(tracks=list(tracks), position=1, tick=99.0)
        assert first.signature == second.signature == ("song:1", "file:/b.flac")

    def test_finished_is_false_when_there_are_unplayed_tracks(self):
        record = QueueRecord(
            tracks=[Track(file="/a.flac", duration=100), Track(file="/b.flac")],
            position=0,
            tick=99.0,
        )
        assert record.finished is False

    def test_finished_without_a_flag_needs_the_last_track_near_its_end(self):
        last = Track(file="/b.flac", duration=180)
        record = QueueRecord(
            tracks=[Track(file="/a.flac", duration=100), last],
            position=1,
            tick=178.5,
        )
        assert record.finished is True
        record.tick = 10.0
        assert record.finished is False


class TestPlaybackStart:
    def _album(self, **kwargs) -> QueueRecord:
        tracks = [
            Track(file="/a.flac", duration=100),
            Track(file="/b.flac", duration=180),
        ]
        return QueueRecord(tracks=tracks, **kwargs)

    def test_an_interrupted_queue_keeps_its_place(self):
        assert playback_start(self._album(position=1, tick=70.0)) == (1, 70.0)

    def test_from_track_start_zeroes_only_the_tick(self):
        assert playback_start(self._album(position=1, tick=70.0), True) == (1, 0.0)

    def test_a_completed_flag_starts_at_the_beginning(self):
        assert playback_start(self._album(position=1, tick=179.0, completed=True)) == (
            0,
            0.0,
        )

    def test_a_legacy_finished_row_starts_at_the_beginning(self):
        assert playback_start(self._album(position=1, tick=179.0)) == (0, 0.0)

    def test_stop_on_the_last_track_mid_way_is_not_finished(self):
        assert playback_start(self._album(position=1, tick=10.0)) == (1, 10.0)

    def test_from_track_start_does_not_override_a_finished_queue(self):
        assert playback_start(
            self._album(position=1, tick=179.0, completed=True), True
        ) == (0, 0.0)
