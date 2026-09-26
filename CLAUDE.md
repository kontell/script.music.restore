# CLAUDE.md

`script.music.restore` records a music queue when Kodi takes it away and offers it back from a pop-up. `default.py` is the pop-up. `service.py` is the recorder, and it runs for the whole Kodi session. Product copy is `README.md`.

## Commands

```bash
tox                           # black, mypy, pytest — what CI runs
pytest tests/unit -q
black --check --diff .
mypy
tools/dev-install.sh          # rsync into ~/.kodi/addons and bounce the service
tools/build.py [OUTDIR]       # install zip (default ./dist)
```

Live capture and restore are not in CI. They need a real Kodi. Do not put a host, a port, or a credential in this repo.

## Layout

| Path | Role |
| --- | --- |
| `lib/musicrestore/recorder.py` | Watches playback and writes history |
| `lib/musicrestore/restore.py` | Puts a saved queue back |
| `lib/musicrestore/rebind.py` | Decides whether a stored song id still names that track |
| `lib/musicrestore/presenter.py` | The pop-up |
| `lib/musicrestore/history.py` | Read and write `queues.json` |
| `tests/unit/` | Unit tests. No Kodi process |

History lives at `special://profile/addon_data/script.music.restore/queues.json`.
The dialog reads `queue_previews.json` beside it, a small rebuildable index, and
loads the full history only when a row is selected.

## Restoring a queue

`restore.py` validates and fully tags the current and next tracks before starting playback. It applies `StartOffset` to the current track, starts those two items, then builds the rest of the queue while the player runs.

The remaining tracks are mapped to current library song IDs through a revision-aware cache, an indexed read-only SQLite query, or paged JSON-RPC scans. Native `Playlist.Add` supplies library metadata for the following tracks; individual `Playlist.Insert` requests prepend the earlier tracks and preserve the playing position. Unresolved items are repaired from the saved record. The recorder keeps the saved queue as its shadow until the build finishes, so an interrupted build does not replace history with a partial queue.

For general Kodi behavior, see `../kodi-drive/README.md` and the kodi-drive skills.

## What not to add

No hosts, ports, account names, or credentials. No logs. A log line from a real Kodi often contains a full stream URL.
