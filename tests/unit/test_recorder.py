"""Placing the playing track in the shadow queue.

``Recorder._locate`` exists because ``xbmc.PlayList(n).getposition()`` answers
for whichever playlist the player is on rather than the one it was asked for.
These build a recorder without touching Kodi — only the three fields
``_locate`` reads are set — and drive it through the sequences that matter.
"""

from typing import List, Optional

from musicrestore.model import Track
from musicrestore.recorder import Recorder

ALBUM = [
    "https://jelly/Audio/1/stream.flac",
    "https://jelly/Audio/2/stream.flac",
    "https://jelly/Audio/3/stream.flac",
    "https://jelly/Audio/4/stream.flac",
]


def _recorder(files: List[str], position: int = 0, tick: float = 0.0) -> Recorder:
    """A recorder holding ``files``, with no Kodi objects constructed."""
    recorder = Recorder.__new__(Recorder)
    recorder._items = [Track(file=path, title=path[-10:]) for path in files]
    recorder._position = position
    recorder._tick = tick
    return recorder


def _locate(
    recorder: Recorder, playing: str, position: int, tick: float = 0.0
) -> Optional[int]:
    return recorder._locate(playing, position, tick)


class TestSteadyPlayback:
    def test_an_unmoved_position_is_taken_as_it_is(self):
        recorder = _recorder(ALBUM, position=2, tick=30.0)
        assert _locate(recorder, ALBUM[2], 2, 31.0) == 2

    def test_the_next_track_moves_the_position_on(self):
        recorder = _recorder(ALBUM, position=2, tick=200.0)
        assert _locate(recorder, ALBUM[3], 3, 0.5) == 3

    def test_skipping_back_is_followed(self):
        recorder = _recorder(ALBUM, position=3, tick=90.0)
        assert _locate(recorder, ALBUM[0], 0, 0.4) == 0

    def test_a_queue_adopted_mid_play_finds_the_track(self):
        # A service restart leaves the shadow at 0 with music already on
        # track 3; the reported position is right and is believed.
        recorder = _recorder(ALBUM, position=0, tick=0.0)
        assert _locate(recorder, ALBUM[2], 2, 45.0) == 2


class TestAnotherPlaylistTakesOver:
    def test_a_video_starting_does_not_move_the_music_position(self):
        # Kodi 22: opening a video sets the playlist player's index to 0 while
        # the music is still playing. The file says otherwise.
        recorder = _recorder(ALBUM, position=3, tick=128.0)
        assert _locate(recorder, ALBUM[3], 0, 129.0) == 3

    def test_the_position_survives_repeated_bogus_samples(self):
        recorder = _recorder(ALBUM, position=3, tick=128.0)
        for tick in (129.0, 130.0, 131.0):
            index = _locate(recorder, ALBUM[3], 0, tick)
            assert index == 3
            recorder._position = index or 0
            recorder._tick = tick

    def test_an_unplaceable_file_rejects_a_backwards_jump(self):
        # A plugin that resolves to some other URL: the file cannot be found,
        # so the backwards jump with the clock still running is what gives the
        # takeover away.
        recorder = _recorder(ALBUM, position=3, tick=128.0)
        assert _locate(recorder, "plugin://resolved/elsewhere", 0, 129.0) is None

    def test_an_unplaceable_file_still_allows_a_real_skip_back(self):
        # Skipping back really does start the track again, so the clock resets.
        recorder = _recorder(ALBUM, position=3, tick=128.0)
        assert _locate(recorder, "plugin://resolved/elsewhere", 0, 0.3) == 0


class TestOutOfRange:
    def test_an_empty_shadow_places_nothing(self):
        assert _locate(_recorder([]), "anything", 0) is None

    def test_a_position_past_the_end_is_dropped(self):
        recorder = _recorder(ALBUM, position=1)
        assert _locate(recorder, "plugin://resolved/elsewhere", 9, 5.0) is None


class TestDuplicateTracks:
    def test_the_copy_nearest_the_last_position_wins(self):
        files = [ALBUM[0], ALBUM[1], ALBUM[0], ALBUM[2]]
        recorder = _recorder(files, position=3, tick=60.0)
        # Track 1 is queued twice; a bogus 0 must not pick the first copy.
        assert _locate(recorder, ALBUM[0], 0, 61.0) == 2
