# Restore Music Queue

A Kodi add-on that keeps the music queues you were part-way through, and puts
one back on the right track at the right second.

Kodi throws a music queue away without ceremony. Start an album while another
is playing and the first one is gone. Start a video and the tracks survive but
Kodi forgets where you were, which amounts to the same thing. Quit and
everything goes. This records each of those moments and offers them back.

## Using it

**Recent queues** — in Contuary, a button in the music row on the home screen;
otherwise run the add-on from *Add-ons → Program add-ons*. Both open the same
pop-up:

```
[art]  Back in Black — AC/DC
       Track 3 of 10 · 7 unplayed
       2 hours ago · stopped at 1:10
```

Pick one and it loads and resumes. The entry stays on the list — restoring is
not destructive, so choosing the wrong one costs nothing.

### Settings

| Setting | Default | |
|---|---|---|
| Queues to keep | 20 | Oldest drops off when full |
| Restore the last queue when Kodi starts | off | |
| Start restored queues paused | off | On: loads at the right point and waits for play |

## What gets recorded

A queue is saved whenever it is taken away:

| | |
|---|---|
| A new album or playlist replaces it | `Playlist.OnClear` |
| A video interrupts it | `Player.OnStop` — the tracks survive, the position does not |
| Playback is stopped, or the queue ends | `Player.OnStop` |
| Kodi quits | `System.OnQuit` |

The history is a log, not a set. Play an album twice and it appears twice, at
whatever point each listen reached. The only thing suppressed is an exact
repeat — same tracks, same track playing, same second — which is one event
being reported twice rather than a second listen.

## How it works

Three findings shaped this, all measured against a running Kodi rather than
read off a wiki. `docs/INVESTIGATION.md` has the traces.

**The queue is already gone when you are told about it.** Announcements are
delivered on a worker thread, so a handler that answers `Playlist.OnClear` by
calling `Playlist.GetItems` gets the *incoming* queue and a reset position. So
the service mirrors the queue continuously and commits what it already holds.

**Notification payloads are not enough to rebuild a queue.** `Playlist.OnAdd`
carries a library id for library songs, but a track without one arrives with no
file path at all. The shadow is refreshed with `Playlist.GetItems` on a
debounce; the notifications only say *when*.

**`Player.Open` ignores `options.resume` for a playlist.** It lands on the right
track and starts at 0:00 — the playlist branch never reaches
`HandleResumeOption`. Setting `StartOffset` on the resume track's `ListItem`
does work, and opens the file already seeked, so there is no audible jump from
the beginning.

## Layout

```
default.py            the pop-up
service.py            the recorder
lib/musicrestore/
    recorder.py       shadow state, capture triggers
    restore.py        rebuild the playlist, resume at the tick
    presenter.py      the select dialog
    history.py        the JSON store
    model.py          Track / QueueRecord
    naming.py         row wording (pure, unit-tested)
    settings.py       addon settings
    jsonrpc.py        JSON-RPC helper
```

History lives at
`special://profile/addon_data/script.music.restore/queues.json`.

## Development

```bash
pip install -r requirements-dev.txt
black --check --diff .
mypy
pytest tests/unit -q
python3 tools/build.py dist      # install zip
```

## Licence

GPL-3.0-only.
