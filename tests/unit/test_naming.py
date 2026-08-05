"""Row wording. Pure functions, so no Kodi needed."""

from musicrestore.model import QueueRecord, Track
from musicrestore.naming import clock, queue_detail, queue_title, time_ago

HOUR = 3600.0
DAY = 24 * HOUR


def track(title: str, artist: str = "", album: str = "", songid: int = 0) -> Track:
    return Track(
        file="/music/%s.flac" % title.lower().replace(" ", "-"),
        title=title,
        artist=artist,
        album=album,
        songid=songid or None,
    )


class TestQueueTitle:
    def test_single_album_names_album_and_artist(self):
        record = QueueRecord(
            tracks=[
                track("Hells Bells", "AC/DC", "Back in Black"),
                track("Shoot to Thrill", "AC/DC", "Back in Black"),
            ]
        )
        assert queue_title(record) == "Back in Black — AC/DC"

    def test_single_album_many_artists_names_album_alone(self):
        record = QueueRecord(
            tracks=[
                track("Song A", "Someone", "A Compilation"),
                track("Song B", "Someone Else", "A Compilation"),
            ]
        )
        assert queue_title(record) == "A Compilation"

    def test_one_artist_across_albums_names_artist_and_count(self):
        record = QueueRecord(
            tracks=[
                track("Hells Bells", "AC/DC", "Back in Black"),
                track("Go Down", "AC/DC", "Let There Be Rock"),
                track("Jailbreak", "AC/DC", "'74 Jailbreak"),
            ]
        )
        assert queue_title(record) == "AC/DC — 3 tracks"

    def test_mixture_leads_with_the_first_track(self):
        record = QueueRecord(
            tracks=[
                track("Hells Bells", "AC/DC", "Back in Black"),
                track("Debaser", "Pixies", "Doolittle"),
                track("Blue Monday", "New Order", "Substance"),
            ]
        )
        assert queue_title(record) == "Hells Bells + 2 more"

    def test_single_track_is_just_its_title(self):
        record = QueueRecord(tracks=[track("Debaser", "Pixies", "Doolittle")])
        # One track is also one album, so the album form wins — which reads
        # better than "Debaser + 0 more".
        assert queue_title(record) == "Doolittle — Pixies"

    def test_untagged_tracks_fall_back_to_a_count(self):
        record = QueueRecord(
            tracks=[Track(file="/music/a.flac"), Track(file="/music/b.flac")]
        )
        assert queue_title(record) == "2 tracks"

    def test_untagged_but_titled_leads_with_the_title(self):
        record = QueueRecord(
            tracks=[
                Track(file="/a.flac", title="Track One"),
                Track(file="/b.flac", title="Track Two"),
            ]
        )
        assert queue_title(record) == "Track One + 1 more"

    def test_empty_queue_does_not_raise(self):
        assert queue_title(QueueRecord()) == "0 tracks"

    def test_translator_is_used_when_it_returns_something(self):
        record = QueueRecord(tracks=[track("A", "Artist", "Album")])
        assert queue_title(record, lambda _id: "{1} :: {0}") == "Artist :: Album"

    def test_blank_translation_falls_back_to_english(self):
        record = QueueRecord(tracks=[track("A", "Artist", "Album")])
        assert queue_title(record, lambda _id: "") == "Album — Artist"


class TestTimeAgo:
    def test_under_a_minute(self):
        assert time_ago(1000.0, now=1030.0) == "just now"

    def test_exactly_one_minute_is_singular(self):
        assert time_ago(1000.0, now=1000.0 + 60) == "a minute ago"

    def test_minutes(self):
        assert time_ago(1000.0, now=1000.0 + 25 * 60) == "25 minutes ago"

    def test_one_hour_is_singular(self):
        assert time_ago(0.0, now=HOUR) == "an hour ago"

    def test_hours(self):
        assert time_ago(0.0, now=5 * HOUR) == "5 hours ago"

    def test_one_day_is_yesterday(self):
        assert time_ago(0.0, now=DAY) == "yesterday"

    def test_days(self):
        assert time_ago(0.0, now=5 * DAY) == "5 days ago"

    def test_two_weeks_switches_to_weeks(self):
        assert time_ago(0.0, now=21 * DAY) == "3 weeks ago"

    def test_a_clock_skew_into_the_future_does_not_go_negative(self):
        assert time_ago(2000.0, now=1000.0) == "just now"


class TestClock:
    def test_under_a_minute(self):
        assert clock(9) == "0:09"

    def test_minutes_and_seconds(self):
        assert clock(70.4) == "1:10"

    def test_past_an_hour(self):
        assert clock(3725) == "1:02:05"

    def test_negative_is_clamped(self):
        assert clock(-5) == "0:00"


class TestQueueDetail:
    def _record(self, position=2, total=10, tick=70.0, saved=0.0):
        return QueueRecord(
            tracks=[track("T%d" % i, "A", "Album") for i in range(total)],
            position=position,
            tick=tick,
            saved=saved,
        )

    def test_two_lines_with_counts_and_when(self):
        detail = queue_detail(self._record(), now=2 * HOUR)
        assert detail == "Track 3 of 10 · 7 unplayed[CR]2 hours ago · stopped at 1:10"

    def test_last_track_has_nothing_unplayed(self):
        detail = queue_detail(self._record(position=9), now=2 * HOUR)
        assert detail.startswith("Track 10 of 10 · 0 unplayed")

    def test_a_barely_started_track_omits_the_stop_point(self):
        detail = queue_detail(self._record(tick=0.4), now=2 * HOUR)
        assert detail == "Track 3 of 10 · 7 unplayed[CR]2 hours ago"

    def test_the_line_break_is_kodi_markup(self):
        assert "[CR]" in queue_detail(self._record(), now=2 * HOUR)
