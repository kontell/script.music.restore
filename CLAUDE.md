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

## Restoring a queue

`Player.Open` on a playlist ignores the resume option and starts at 0:00. Set `StartOffset` on the track that is starting. The audio player reads that as its start time.

Queue the files with `playlist.add(url)` and fill in only the track that is starting before `play`. A `ListItem` per track before play is several calls into Kodi, and each call drops and retakes the interpreter lock. On a slow device that dominates the wait. Titles, art, year, genre, play count and song ids for the other tracks are applied after playback has started, from the next track to the end and then from the top up to the one that started.

Do not replace that loop with one `Playlist.Add` of the file paths. For a music playlist that call looks every file up in the library before it returns, and a Jellyfin URL misses the lookup, so Kodi then tries to read the tag from the file. That was slower than the Python adds. Do not pass song ids to `Playlist.Add` either: that path sorts the list by track number.

`setInfo("music", ...)` is deprecated, and it is the call that marks the music tag loaded. The `InfoTagMusic` setters do not. An unloaded tag makes Kodi open the music database, fail to find a Jellyfin URL, and then try to read the tag from the file, once per queued track.

A kofin library repair reuses song ids, so a saved id can now belong to a different track. Check the id with `AudioLibrary.GetSongDetails` and keep it only when the returned file still contains the same Jellyfin item id. The path scan is the fallback for a miss or a reused id, not the first call for every track. Year, genre and play count come from that same row onto the tag. The saved thumb and fanart stay the ones from the record.

## What not to add

No hosts, ports, account names, or credentials. No logs. A log line from a real Kodi often contains a full stream URL.