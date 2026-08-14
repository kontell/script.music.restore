"""Rebinding a recorded track onto the live library after a rebuild."""

from musicrestore.model import Track
from musicrestore.rebind import (
    LOOKUP_FAILED,
    apply_library_hit,
    jellyfin_audio_id,
    rebind_track,
)

JID = "4d107b266443a4030a665dc3f016f4c5"


def _track(**kwargs) -> Track:
    defaults = dict(
        file="http://jelly:8096/Audio/%s/stream.flac?static=true" % JID,
        title="Act One",
        artist="Someone",
        album="The Album",
        duration=10,
        songid=21711,
    )
    defaults.update(kwargs)
    return Track(**defaults)


class TestJellyfinAudioId:
    def test_direct_http_path(self):
        assert (
            jellyfin_audio_id(
                "http://192.168.1.167:8096/Audio/%s/stream.flac?static=true" % JID
            )
            == JID
        )

    def test_plugin_path(self):
        assert (
            jellyfin_audio_id(
                "plugin://plugin.video.kofin/455b9a6cc37d4d2e961d7d5236820ee4/"
                "%s/stream.flac?mode=play&id=%s&dbid=99" % (JID, JID)
            )
            == JID
        )

    def test_local_file_has_none(self):
        assert jellyfin_audio_id("/music/album/track.flac") is None

    def test_empty_is_none(self):
        assert jellyfin_audio_id("") is None


class TestRebindTrack:
    def test_rewrites_a_reused_songid(self):
        live = {
            "songid": 3,
            "file": "http://jelly:8096/Audio/%s/stream.flac?static=true" % JID,
            "title": "Act One",
        }
        rebound = rebind_track(_track(songid=21711), lambda _id: live)
        assert rebound.songid == 3
        assert rebound.title == "Act One"

    def test_updates_a_plugin_path_after_a_mode_change(self):
        live_file = (
            "plugin://plugin.video.kofin/lib/%s/stream.flac?mode=play&id=%s&dbid=3"
            % (JID, JID)
        )
        live = {"songid": 3, "file": live_file}
        rebound = rebind_track(_track(), lambda _id: live)
        assert rebound.file == live_file
        assert rebound.songid == 3

    def test_drops_songid_when_the_item_has_left_the_library(self):
        rebound = rebind_track(_track(), lambda _id: None)
        assert rebound.songid is None
        assert rebound.file == _track().file

    def test_keeps_the_record_when_the_lookup_fails(self):
        original = _track()
        assert rebind_track(original, lambda _id: LOOKUP_FAILED) == original

    def test_leaves_a_local_file_alone(self):
        local = _track(file="/music/track.flac", songid=12)
        assert rebind_track(local, lambda _id: {"songid": 1}) == local

    def test_apply_library_hit_keeps_recorded_display_fields(self):
        live = {
            "type": "song",
            "songid": 3,
            "file": "http://x/Audio/%s/stream.mp3" % JID,
            "title": "Server Title",
            "album": "Server Album",
        }
        applied = apply_library_hit(_track(), live)
        assert applied.title == "Act One"
        assert applied.album == "The Album"
        assert applied.songid == 3
