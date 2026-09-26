"""The store: duplicate suppression, ordering, the cap, and surviving junk."""

import json
from io import StringIO

import pytest

from musicrestore import history, naming
from musicrestore.model import QueueRecord, Track


def record(*, keys=("a", "b"), position=0, tick=0.0, saved=0.0) -> QueueRecord:
    return QueueRecord(
        tracks=[Track(file="/%s.flac" % key) for key in keys],
        position=position,
        tick=tick,
        saved=saved,
    )


@pytest.fixture
def store(monkeypatch):
    """An in-memory stand-in for the JSON file on disk."""

    class Store:
        def __init__(self) -> None:
            self.content = None
            self.writes = 0

    fake = Store()

    def read_raw():
        if fake.content is None:
            return {}
        try:
            data = json.loads(fake.content)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def save(records):
        fake.writes += 1
        fake.content = json.dumps(
            {"version": 1, "queues": [r.to_dict() for r in records]}
        )
        return True

    monkeypatch.setattr(history, "_read_raw", read_raw)
    monkeypatch.setattr(history, "save", save)
    return fake


class TestIsDuplicate:
    def test_no_previous_entry_is_never_a_duplicate(self):
        assert history.is_duplicate(record(), None) is False

    def test_identical_capture_is_a_duplicate(self):
        first = record(position=2, tick=70.0)
        second = record(position=2, tick=70.0)
        assert history.is_duplicate(second, first) is True

    def test_a_different_position_is_a_second_listen(self):
        # The brief is explicit: playing and stopping an album twice should
        # leave two rows.
        assert history.is_duplicate(record(position=5), record(position=2)) is False

    def test_a_different_queue_is_not_a_duplicate(self):
        assert history.is_duplicate(record(keys=("a", "c")), record()) is False

    def test_sub_second_drift_still_counts_as_the_same_moment(self):
        first = record(position=2, tick=70.0)
        second = record(position=2, tick=70.4)
        assert history.is_duplicate(second, first) is True

    def test_more_than_a_second_apart_is_a_new_capture(self):
        first = record(position=2, tick=70.0)
        second = record(position=2, tick=72.0)
        assert history.is_duplicate(second, first) is False

    def test_track_order_matters(self):
        assert history.is_duplicate(record(keys=("b", "a")), record()) is False


class TestAdd:
    def test_newest_first(self, store):
        history.add(record(keys=("a",), saved=1.0), keep=20)
        history.add(record(keys=("b",), saved=2.0), keep=20)
        saved = history.load()
        assert [r.tracks[0].file for r in saved] == ["/b.flac", "/a.flac"]

    def test_duplicate_is_rejected_and_not_written(self, store):
        assert history.add(record(position=2, tick=70.0), keep=20) is True
        writes = store.writes
        assert history.add(record(position=2, tick=70.0), keep=20) is False
        assert store.writes == writes
        assert len(history.load()) == 1

    def test_cap_trims_the_oldest(self, store):
        for index in range(8):
            history.add(record(keys=("t%d" % index,), saved=float(index)), keep=5)
        saved = history.load()
        assert len(saved) == 5
        assert saved[0].tracks[0].file == "/t7.flac"
        assert saved[-1].tracks[0].file == "/t3.flac"

    def test_shrinking_the_cap_trims_on_the_next_write(self, store):
        for index in range(6):
            history.add(record(keys=("t%d" % index,)), keep=20)
        history.add(record(keys=("new",)), keep=3)
        assert len(history.load()) == 3

    def test_only_the_newest_entry_is_checked_for_duplicates(self, store):
        # An older identical row is left alone: the log records every removal,
        # and restoring does not consume the entry it came from.
        history.add(record(keys=("a",), position=1, tick=10.0), keep=20)
        history.add(record(keys=("b",), position=0, tick=0.0), keep=20)
        assert history.add(record(keys=("a",), position=1, tick=10.0), keep=20) is True
        assert len(history.load()) == 3


class TestLoad:
    def test_missing_store_is_an_empty_history(self, store):
        assert history.load() == []

    def test_corrupt_json_is_an_empty_history(self, store):
        store.content = "{not json"
        assert history.load() == []

    def test_unexpected_shape_is_an_empty_history(self, store):
        store.content = json.dumps({"queues": "nope"})
        assert history.load() == []

    def test_trackless_entries_are_dropped(self, store):
        store.content = json.dumps(
            {
                "version": 1,
                "queues": [{"tracks": []}, {"tracks": [{"file": "/a.flac"}]}],
            }
        )
        assert len(history.load()) == 1

    def test_count_matches_load(self, store):
        history.add(record(keys=("a",)), keep=20)
        history.add(record(keys=("b",)), keep=20)
        assert history.count() == 2


class TestPreviews:
    def test_preview_keeps_the_rendered_row_and_store_precision(self):
        saved = QueueRecord(
            tracks=[
                Track(file="/a", title="First", artist="Artist", album="Album"),
                Track(
                    file="/b",
                    title="Second",
                    artist="Artist",
                    album="Album",
                    thumb="art",
                ),
            ],
            position=1,
            tick=42.123456,
            saved=123456.123456,
        )
        preview = history._preview(saved)
        loaded = QueueRecord.from_dict(saved.to_dict())
        assert preview.saved == loaded.saved
        assert preview.tick == loaded.tick
        assert naming.preview_title(preview) == naming.queue_title(loaded)
        assert naming.preview_detail(preview, now=123500) == naming.queue_detail(
            loaded, now=123500
        )
        assert history._preview(loaded) == preview

    def test_valid_index_does_not_load_full_history(self, monkeypatch):
        preview = history._preview(record(saved=1.0))
        data = json.dumps(
            {"version": 1, "source": [100, 200], "queues": [preview.to_dict()]}
        )
        monkeypatch.setattr(history, "_fingerprint", lambda: (100, 200))
        monkeypatch.setattr(history.xbmcvfs, "File", lambda path: StringIO(data))
        monkeypatch.setattr(
            history,
            "load",
            lambda: pytest.fail("full track history was loaded"),
        )
        assert history.load_previews() == [preview]

    def test_stale_index_is_rebuilt(self, monkeypatch):
        preview = history._preview(record(saved=1.0))
        data = json.dumps(
            {"version": 1, "source": [100, 199], "queues": [preview.to_dict()]}
        )
        written = []
        monkeypatch.setattr(history, "_fingerprint", lambda: (100, 200))
        monkeypatch.setattr(history.xbmcvfs, "File", lambda path: StringIO(data))
        monkeypatch.setattr(history, "load", lambda: [record(saved=2.0)])
        monkeypatch.setattr(history, "_write_previews", written.append)
        result = history.load_previews()
        assert [item.saved for item in result] == [2.0]
        assert len(written) == 1

    def test_nonlocal_store_still_opens_without_index(self, monkeypatch):
        monkeypatch.setattr(history, "_fingerprint", lambda: None)
        monkeypatch.setattr(history, "load", lambda: [record(saved=3.0)])
        assert [item.saved for item in history.load_previews()] == [3.0]

    def test_selection_survives_a_new_capture_while_dialog_is_open(self, monkeypatch):
        selected = record(keys=("old",), saved=1.0)
        newer = record(keys=("new",), saved=2.0)
        monkeypatch.setattr(history, "load", lambda: [newer, selected])
        assert history.resolve_preview(history._preview(selected)) is selected
