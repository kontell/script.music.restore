"""The queue pop-up, including the toasts around a restore."""

from typing import Any, List, Optional, Tuple

from musicrestore.model import QueuePreview, QueueRecord, Track
from musicrestore.presenter import notify_blocked, show


class _Addon:
    def __init__(self, strings: Optional[dict] = None) -> None:
        self.strings = strings or {}

    def getAddonInfo(self, _key: str) -> str:
        return "/icon.png"

    def getSettingBool(self, _key: str) -> bool:
        return False

    def getLocalizedString(self, string_id: int) -> str:
        return str(self.strings.get(string_id, ""))


class _Dialog:
    def __init__(self, notes: List[Tuple[str, str]]) -> None:
        self.notes = notes

    def select(self, _heading: str, _rows: List[Any], useDetails: bool = False) -> int:
        return 0

    def notification(
        self,
        heading: str,
        message: str,
        icon: str = "",
        time: int = 0,
        sound: bool = True,
    ) -> None:
        self.notes.append((heading, message))

    def ok(self, _heading: str, _message: str) -> None:
        return None


def test_blocked_toast_uses_the_fallback_copy(monkeypatch: Any) -> None:
    notes: List[Tuple[str, str]] = []
    monkeypatch.setattr("musicrestore.presenter.xbmcgui.Dialog", lambda: _Dialog(notes))
    monkeypatch.setattr("musicrestore.presenter.settings.addon", lambda: _Addon())

    notify_blocked()

    assert notes == [("Restore blocked", "A restore is already in progress.")]


def test_blocked_toast_uses_translated_copy(monkeypatch: Any) -> None:
    notes: List[Tuple[str, str]] = []
    monkeypatch.setattr("musicrestore.presenter.xbmcgui.Dialog", lambda: _Dialog(notes))
    monkeypatch.setattr(
        "musicrestore.presenter.settings.addon",
        lambda: _Addon({30014: "Bloqueado", 30015: "Ya hay una restauración."}),
    )

    notify_blocked()

    assert notes == [("Bloqueado", "Ya hay una restauración.")]


def test_a_busy_dialog_restore_toasts_blocked_and_not_restoring(
    monkeypatch: Any, tmp_path: Any
) -> None:
    import musicrestore.restore as restore_mod

    monkeypatch.setattr(
        restore_mod, "_restore_lock_path", lambda: str(tmp_path / "restore.lock.db")
    )
    notes: List[Tuple[str, str]] = []
    preview = QueuePreview(
        saved=1.0,
        position=0,
        total=1,
        tick=0.0,
        finished=False,
        thumb="",
        title_id=30033,
        title_args=("1",),
    )
    record = QueueRecord(
        tracks=[Track(file="http://example/a")], position=0, tick=0.0, saved=1.0
    )
    monkeypatch.setattr("musicrestore.presenter.xbmcgui.Dialog", lambda: _Dialog(notes))
    monkeypatch.setattr("musicrestore.presenter.settings.addon", lambda: _Addon())
    monkeypatch.setattr(
        "musicrestore.presenter.history.load_previews", lambda: [preview]
    )
    monkeypatch.setattr(
        "musicrestore.presenter.history.resolve_preview", lambda _preview: record
    )

    with restore_mod._restore_lock() as acquired:
        assert acquired
        show()

    assert notes == [("Restore blocked", "A restore is already in progress.")]
