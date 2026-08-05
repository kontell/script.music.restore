# Restore playlist (music) — implementation investigation

Verified against the running instance: **Kodi 22.0 BETA1 "Piers"** (version code
21.90.801), skin.contuary, on this box. Source references are to
`/media/minipie/bluecon/ref/kodi-piers-full`.

Everything in "Verified behaviour" below was observed live over the JSON-RPC
notification stream (TCP 9090) and with two throwaway probe add-ons, both since
removed. Nothing here is inferred from documentation alone.

---

## 1. Verdict

Build it as **one add-on with two extension points** — `xbmc.service` (the
recorder) and `xbmc.python.script` (the pop-up). `script.skin.contuary` is the
local precedent for the script half; `plugin.audio.koshelf` proves the
two-extension-points-in-one-addon shape.

No `pluginsource` extension, no browsable root: the add-on has exactly two entry
points, both landing on the same pop-up (§7.2).

The feature does not exist anywhere. The nearest things are `kodi-resume`
(single state, restore-on-startup only) and "Last Played" (a list of played
*items*, not queues). Neither keeps a history of queues with per-queue position
and tick.

One premise in the brief needs correcting before anything is designed — see
§3.2. **Starting a video does not remove the music queue.** It orphans it. That
is a different signal and needs different handling.

---

## 2. Prior art

| Thing | What it does | Why it isn't this |
|---|---|---|
| `kodi-resume` (Matt Huisman) | Polls state to sqlite, restores on Kodi start | One slot, overwritten continuously. No history, no user-facing list, no per-queue choice. Krypton-era API (`xbmc.abortRequested`), unmaintained. |
| `plugin.video.last_played` / "Last Viewed/Played" | Lists recently played *items* | Item history, not queue history. No position/tick, nothing to restore into a playlist. |
| Kodi core | `Playlist.Save` doesn't exist in JSON-RPC; queue is discarded on quit | Forum consensus (tid=355300, tid=355312) is that this needs an add-on and none exists. |
| Finamp "Restore Now Playing" | Keeps the last 60 queues, restore manually or on startup | The UX model being copied. Not Kodi. |

`kodi-resume` is still worth reading for one thing: `xr_json.py`'s
library-id-if-possible / file-path-otherwise item encoding is the right call and
worth keeping. Its one-JSON-RPC-request-per-track playlist loading is not —
`Playlist.Add` takes an array (§6).

---

## 3. Verified behaviour — what actually signals "the queue went away"

This is the part that determines the whole architecture, so it was tested rather
than assumed.

### 3.1 A new album replacing the current one

Observed trace (music playing, position 2, 1:10 into the track, then album B is
opened):

```
22.56  Player.OnSeek        song 1153, time 1:10
24.59  Playlist.OnClear     {"playlistid": 0}          ← old queue destroyed
24.59  Playlist.OnAdd  × 8  {"playlistid": 0, ...}     ← new queue, same tick
24.74  Player.OnStop        {"end": false, item: song 1153}
24.75  Player.OnPlay        {item: song 1270}
```

`Playlist.OnClear` fires, and it fires **before** `Player.OnStop`. Confirmed in
source: `CPlayList::Clear()` announces only when the list was non-empty
(`xbmc/playlists/PlayList.cpp:44`), which is why the *first* album of a session
produces no `OnClear`.

### 3.2 A video starting while music plays — **the queue is NOT removed**

```
 9.97  Playlist.OnClear     {"playlistid": 1}   ← the VIDEO playlist, not music
11.38  Playlist.OnAdd       {"playlistid": 1, movie 3566}
11.56  Player.OnStop        {"end": false, item: song 1270}
11.57  Player.OnPlay        {playerid: 1, movie 3566}
```

`Playlist.GetItems(playlistid=0)` still returned all 8 songs — during the video
and after it stopped. `VideoGUIUtils.cpp:358` only clears `TYPE_VIDEO`.

What is actually lost is `m_iCurrentSong` and the elapsed tick:
`CPlayListPlayer::OnMessage(GUI_MSG_PLAYBACK_STOPPED)` calls `Reset()` and sets
the current playlist to `TYPE_NONE` (`xbmc/PlayListPlayer.cpp:104-115`). The
tracks survive; *where you were* does not.

So this case is signalled by **`Player.OnStop` with `item.type == "song"`**, not
by `Playlist.OnClear`. Note `Player.OnStop`'s payload carries no `playerid` —
only `{"end": bool, "item": {...}}` — so audio-vs-video is distinguished by
`item.type`.

### 3.3 The full trigger taxonomy

| Event | Signal | Queue contents at that moment |
|---|---|---|
| New album/playlist replaces current | `Playlist.OnClear{playlistid:0}` | **Already gone** |
| Video (or anything) interrupts music | `Player.OnStop{item.type:"song", end:false}` | Intact, position lost |
| User stops playback | `Player.OnStop{item.type:"song", end:false}` | Intact, position lost |
| Queue reaches the end | `Player.OnStop{end:true}` on last track | Intact, position lost |
| Kodi quits | `System.OnQuit` | Discarded on exit |

All five rows are in scope (§12.2). Rows 2–4 share one signal, so capturing any
of them captures all of them. Row 5 is what makes restore-on-startup (§12.3)
work, and it costs one extra `System.OnQuit` handler.

### 3.4 Querying on `OnClear` does not work — a shadow copy is mandatory

Announcements are queued and delivered on a worker thread
(`CAnnouncementManager::Process`), so a Python handler runs *after* Kodi has
already applied the change. A probe service that queried inside the `OnClear`
handler got:

```
[clearprobe] OnClear -> Player.GetProperties={"playlistid":0, "position":0,
                        "time":{"minutes":1,"seconds":12}}  (88ms)
[clearprobe] OnClear -> Playlist.GetItems size=8
```

The outgoing queue had **10** tracks and was at **position 2**. What came back
was the *incoming* queue's 8 tracks and `position: 0`. Only `time` was still
correct, and only because the player thread hadn't stopped yet — a ~150 ms race,
not something to build on.

**Conclusion: the service must continuously mirror the queue state.** By the time
you are told it's gone, it is gone.

### 3.5 Notification payloads are not sufficient to rebuild a queue

`Playlist.OnAdd` carries `{"item": {"type": "song", "id": 1174}}` for library
songs — usable. But `CopyMusicTagInfoToObject`
(`xbmc/interfaces/AnnouncementManager.cpp:182`) emits **no file path** when the
db id is 0; a non-library track arrives as
`{"type":"song","title":…,"album":…,"artist":…}` and an untagged item as
`{"type":"unknown"}`.

So the shadow has to be refreshed with `Playlist.GetItems`, not accumulated from
`OnAdd` payloads. `OnAdd` is the *trigger* for a refresh, not the data source.

---

## 4. Capture architecture (recommended)

A single `xbmc.service` holding three pieces of live state:

```
shadow_items     ← Playlist.GetItems(0), refreshed on a ~250ms trailing debounce
                   after any Playlist.OnAdd / OnRemove burst for playlistid 0
shadow_position  ← from the tick loop
shadow_tick      ← from the tick loop
```

**Tick loop.** While `Player.GetActivePlayers` shows an audio player, sample
`Player.GetProperties(playerid=0, ["position","time"])` about once a second.
That single call is the source of truth for both; a ≤1 s stale tick is
irrelevant when resuming a song. Cheaper and far more robust than trying to
derive position from `OnPlay`/`OnSeek` events.

**Commit.** On `Playlist.OnClear{playlistid:0}` or `Player.OnStop{type:"song"}`,
write `{shadow_items, shadow_position, shadow_tick, timestamp}` to history and
reset the shadow. Do the *decision* in the handler and the *disk write* on a
worker — `onNotification` blocks Kodi's announcement thread.

**Debounce sizing.** A 10-track album fires 10 separate `OnAdd` notifications
(`CPlayList::Add(CFileItemList)` loops over `Add(item)`), all within ~10 ms.
250 ms comfortably coalesces the burst, and the next `OnClear` is seconds away at
minimum. The only lossy edge case is queueing a track and replacing the queue
inside the same 250 ms window — acceptable.

**Double-fire guard.** Replacing album A with album B fires `OnClear` (commit A)
and then `Player.OnStop` ~150 ms later — by which point the shadow is either
empty or already refilling with B. So the `OnStop` handler must not commit
unless the shadow is non-empty *and* carries a position established by the tick
loop. After a commit, mark the shadow spent until the next `Player.OnPlay`
re-establishes one. This is a state guard, not deduplication — it stops one user
action producing two rows.

**Dedup — deliberately minimal.** The brief is explicit that "if an album is
played and stopped multiple times it will appear multiple times on the list", so
the history is an append-only log of removal events, not a set of distinct
queues. The only suppression rule is therefore the narrowest one that is
obviously safe:

> Skip the append if the incoming snapshot matches the newest entry on **track
> list, position, and tick**.

Identical in all three means nothing happened between the two captures — a
genuine duplicate, never a distinct listen. Any real difference in where you got
to produces a new row, which is what was asked for. §9's row format already
distinguishes them on screen ("Track 3 of 10" vs "Track 7 of 10").

This matters more than it looks, because restoring does **not** remove the entry
(§12.7): a restored queue that is later interrupted is captured again, so the
same album legitimately appears twice at two different positions. That is the
specified behaviour, not a bug — but it does mean the 20-entry cap fills faster
than it would with a move-to-front rule. The cap is a setting (§11).

---

## 5. Storage

A JSON file at `special://profile/addon_data/script.music.restore/queues.json`.
20 entries × ~50 tracks is a few hundred KB and is written only on queue
removal — sqlite (as `kodi-resume` uses) buys nothing here and costs schema
migration.

Per-track encoding, following `kodi-resume`'s approach:

```json
{"songid": 1292}                       // library item — survives path changes
{"file": "plugin://…/track.flac"}      // everything else
```

Store enough denormalised display metadata per queue (title, artist, album,
track count, art path) to render the list **without** hitting the music db — a
song deleted from the library shouldn't blank out a history row, and the list
should open instantly.

---

## 6. Restore

Two mechanisms, both tested live.

**Playlist + position — works.**

```
Playlist.Clear(0)
Playlist.Add(0, [{"songid":1270}, {"songid":1163}, …])   ← array, one call
Player.Open({"playlistid":0, "position":2}, {"repeat":"all","shuffled":false})
```

Landed on position 2, correct track, `repeat` applied. `Playlist.Add` accepting
an array is worth noting — `kodi-resume` issues one request per track.

**The tick — `options.resume` does NOT work here.** Passing
`{"resume":{"minutes":1,"seconds":30}}` to `Player.Open` was silently ignored;
playback started at 0:00. Confirmed in source: the `playlistid` branch of
`CPlayerOperations::Open` posts `TMSG_MEDIA_PLAY` and never calls
`HandleResumeOption` — that option only applies to the single-item and PVR
branches.

**Use `StartOffset` instead — verified working.** Build the playlist through the
Python API and set the property on the resume track:

```python
li = xbmcgui.ListItem(label=song["title"], offscreen=True)
li.setPath(song["file"])
li.setProperty("StartOffset", "90.0")        # seconds
pl.add(song["file"], li)
xbmc.Player().play(pl, startpos=2)
```

Probe result: started at position 2 and the clock read 1:34 four seconds in —
i.e. playback *began* at 1:30. No seek call, **no audible blip from the track
start**, and none of `kodi-resume`'s mute-seek-unmute dance.

Chain: `ListItem.setProperty("startoffset")` → `setStartOffsetRaw` →
`item->SetStartOffset()` (`interfaces/legacy/ListItem.cpp:229`) →
`PAPlayer.cpp:316 starttime`. `CPlayListPlayer::Play` only zeroes the offset
when it equals `STARTOFFSET_RESUME`, so an explicit value survives.

Trade-off: this route means constructing `ListItem`s (a music-db lookup per
track) rather than firing one `Playlist.Add` array. For ≤50 tracks that is
cheap, and it is the only way to land on the tick cleanly.

---

## 7. UI surfaces

### 7.1 The pop-up list — `Dialog().select(…, useDetails=True)`

The brief asks for a pop-up and for multi-line rows. Both are available, and
skin.contuary already renders them well.

`xbmcgui.Dialog().select()` and `.multiselect()` both accept `ListItem`s and
both honour `useDetails` (`interfaces/legacy/Dialog.cpp`). With `useDetails=True`
Kodi switches to `CONTROL_DETAILED_LIST` (id 6), which in
`skin.contuary/xml/Includes_DialogSelect.xml` is:

- a 110×110 thumbnail — use the album art of the resume track
- `ListItem.Label` — `font14`, single line, 60 px
- `ListItem.Label2` — a **`textbox`**, `font12` grey, 67 px → **wraps to ~2 lines**

So a row can be:

```
[art]  Back in Black — AC/DC
       Track 3 of 10 · 7 unplayed
       2 hours ago · stopped at 1:10
```

`multiselect()` gets the same layout, but with removal dropped (§12.6) there is
now only one dialog: `select()`.

If that isn't enough control, the fallback is a `WindowXMLDialog` with the
add-on's own XML — full freedom, but then you own the layout across skins.
Recommendation: start with `useDetails=True`; it is one line of code and it
already looks right here.

### 7.2 Entry points — no add-on root

Removal is gone, and with it the three-entry root (§12.6). The add-on has no
browsable directory at all, so **`xbmc.python.pluginsource` is not needed** —
`xbmc.python.script` is the whole UI surface.

Two entry points, both landing on the same `select()` pop-up:

1. **The widget button** — `RunScript(script.music.restore)` from the music
   categories widget (§8.1). The primary route.
2. **Running the add-on** — Add-ons → Program add-ons → *Restore Music Queue*
   executes `default.py`, which shows the same pop-up. Free with the
   `xbmc.python.script` extension; `script.skin.contuary` is the local precedent
   (`<provides>executable</provides>`).

Settings are reached the standard way — the add-on's **Configure** button in the
add-on browser, or `Addon.OpenSettings(script.music.restore)`. There is no way to
put a button on a `select()` dialog from Python: `CGUIDialogSelect` does have
`CONTROL_EXTRA_BUTTON` (id 5) and `CONTROL_EXTRA_BUTTON2` (id 8), but neither
is exposed through `xbmcgui.Dialog().select()`, whose only parameters are
`(heading, list, autoclose, preselect, useDetails)`. The alternative — a
trailing "Settings…" row in the list — pollutes every restore interaction to
save one trip to the add-on browser for three toggles set once. Not worth it.

**Empty state.** The widget button is now the only visible trace of the add-on,
so it should hide when there is nothing to restore. The service can publish a
count on the home window:

```python
xbmcgui.Window(10000).setProperty("MusicRestore.Count", str(len(history)))
```

and the skin item conditions on it:

```xml
<visible>System.AddonIsEnabled(script.music.restore) +
         !String.IsEqual(Window(Home).Property(MusicRestore.Count),0)</visible>
```

The same property can feed the label (`Recent queues (7)`) if that's wanted.

### 7.3 The rejected alternative — a directory listing with context menus

Worth recording why this was considered and dropped.

Instead of a pop-up, *Restore recent queue* could be a **plugin directory
listing** of the recent queues: the skin's own list layout and art, sorting, and
a per-row **context menu**, so "remove" becomes a context action rather than a
second root entry and a second list.

It does not work with a pop-up, and the pop-up is what the brief asks for.
`CGUIDialogSelect::OnMessage` responds only to `ACTION_SELECT_ITEM` and
`ACTION_MOUSE_LEFT_CLICK` (`xbmc/dialogs/GUIDialogSelect.cpp:96`) — there is no
context-menu handling in the select dialog, and `ListItem.addContextMenuItems()`

### 7.3 The rejected alternative — a directory listing with context menus

Worth recording why this was considered and dropped.

Instead of a pop-up, *Restore recent queue* could be a **plugin directory
listing** of the recent queues: the skin's own list layout and art, sorting, and
a per-row **context menu**, so "remove" becomes a context action rather than a
second root entry and a second list.

It does not work with a pop-up, and the pop-up is what the brief asks for.
`CGUIDialogSelect::OnMessage` responds only to `ACTION_SELECT_ITEM` and
`ACTION_MOUSE_LEFT_CLICK` (`xbmc/dialogs/GUIDialogSelect.cpp:96`) — there is no
context-menu handling in the select dialog, and `ListItem.addContextMenuItems()`
is inert outside a real directory window. The two are mutually exclusive.

Even setting that aside, a context menu acts on **one row at a time**, so
pruning five stale queues would be five separate menus.

Moot as of §12.6 — removal is gone entirely — but it is also the reason the
`pluginsource` extension can be dropped. Nothing else needed a `plugin://` path
except §8.2, which is now foreclosed for the same reason.

---

## 8. Skin integration — the "Recent queues" node

The music categories widget is `Home.xml:199`, a `WidgetListCategories` include
fed by `content_path = library://music/`. Two ways in.

### 8.1 Static item injected into the widget (recommended)

The skin **already has this pattern**. `Includes_Home.xml` defines
`MovieSubmenuItems` / `TVShowSubmenuItems` — a `<content>` block of literal
`<item>`s gated on `System.AddonIsEnabled(...)` — and the movies call site
(`Home.xml:57`) passes `additional_movie_items="true"`.

It works because `IListProvider::Create` builds a `CMultiProvider` when a control
has more than one `<content>` child, merging static items with the directory
listing in declaration order (`guilib/listproviders/`).

So: add a `MusicSubmenuItems` include and an `additional_music_items` param,
then at `Home.xml:199`:

```xml
<item>
    <label>Recent queues</label>
    <onclick>RunScript(script.music.restore)</onclick>
    <thumb>icons/music-history.png</thumb>
    <visible>System.AddonIsEnabled(script.music.restore) +
             !String.IsEqual(Window(Home).Property(MusicRestore.Count),0)</visible>
</item>
```

`<onclick>` takes any builtin, so this gives a **true pop-up** with no window
transition. Static items land *before* the library nodes; if it should sort
last, put the include after the `<content target=…>` line.

No argument is passed — with removal dropped there is only one action, so
`RunScript(script.music.restore)` and running the add-on from the add-on browser
are literally the same call (§7.2).

### 8.2 Kodi library node — no longer available

`~/.kodi/userdata/library/music/` already exists here as a full copy of the
defaults **plus one custom node** — `continuelisteaning.xml`, pointing at
`plugin://plugin.audio.koshelf/?action=continue_listening/`. So this route is
already in use in this setup and demonstrably works with plugin paths.

Adding `recentqueues.xml` alongside it would surface the node in **every** skin,
not just contuary.

Two caveats:
- `CLibraryDirectory::GetNode` uses userdata **instead of** `system/library/`,
  never merged (`filesystem/LibraryDirectory.cpp:165`). Fine here — the copy
  already exists — but it means the custom set has to be maintained against Kodi
  upgrades.
- A node's `<path>` must be a real directory. It **cannot** run a builtin, so
  this route cannot produce a pop-up — it navigates into a plugin listing. That
  is §7.3's model, not the brief's.

**§8.1 it is.** §8.2 is now closed off by §12.6: a node needs a `plugin://`
path, and the add-on no longer has one. Recorded here because it is the one
thing dropping `pluginsource` costs — if the button should ever appear in a skin
other than contuary, the `pluginsource` extension and a directory listing come
back with it, and the pop-up goes.

---

## 9. List content

**Title.** No playlist-name infolabel exists — Kodi exposes only
`Playlist.Length/Position/Random/Repeat` (`GUIInfoLabels.h:426-429`), and
`CPlayList::m_strPlayListName` is not reachable over JSON-RPC. So derive from
contents, per the brief:

1. all tracks share one album → `"<Album> — <Artist>"`
2. all tracks share one artist → `"<Artist> — N tracks"`
3. otherwise → `"<first track title> + N more"`

Optional refinement: capture `Container.FolderPath` at snapshot time (as
`kodi-resume` does). If it's `special://musicplaylists/Foo.m3u`, "Foo" is a
better title than anything derivable from contents. Cheap, and it's the only way
to recover a real playlist name.

**Counts.** Total is `len(items)`. "Unplayed" is `total - position - 1` for
linear playback. Shuffle makes this approximate — Kodi doesn't expose per-item
played flags over JSON-RPC. Either accept the approximation or label it
"remaining", which is accurate either way.

**Time since.** Store an absolute epoch and format relatively at render time
("2 hours ago"). Kodi's `$LOCALIZE` has no relative-time helper — this is
add-on-side formatting.

---

## 10. Icons

`tools/` holds two Material Symbols SVGs (24 px, `viewBox="0 -960 960 960"`,
fill `#e3e3e3`): `music_history` and `music_off`.

Only **`music_history`** is needed now — it's the widget button. `music_off` was
for the *Remove from recent queue* entry, which no longer exists (§12.6). Leave
it in `tools/` in case removal comes back.

**Kodi cannot render SVG.** It needs converting to PNG. The skin's convention is
256×256 RGBA (`DefaultAddonLibrary.png` etc.); the categories widget draws at
189×120 with `aspectratio=keep`, so square 256×256 is right. `inkscape`,
`convert`, and `magick` are all installed on this box.

Keep `#e3e3e3` — the skin tints via `colordiffuse`, so a light-grey source is
correct.

The add-on also wants its own `resources/icon.png` (256×256) for the add-on
browser, now that running it from there is a real entry point (§7.2).

---

## 11. Settings

A `resources/settings.xml` is needed — decisions 3 and 4 below both call for
one. Once it exists, the history cap costs one more line, so it may as well be
exposed rather than hardcoded.

| Setting | Type | Default |
|---|---|---|
| Number of queues to keep | int, 5–100 | 20 |
| Restore last queue on startup | bool | off |
| Start restored queues paused | bool | off (i.e. play immediately) |

"Start paused" is the inverse of Finamp's "Automatically Play Restored Queues"
— phrased so the default is the unchecked box.

Reached via the add-on's **Configure** button in the add-on browser, or
`Addon.OpenSettings(script.music.restore)`. There is no root entry for it and no
way to hang a button off the `select()` dialog — see §7.2.

**Startup restore mechanics.** The `xbmc.service` extension runs at Kodi start,
so restore-on-startup is just "apply the newest history entry once, at service
init". Wait for the music db to be available before resolving `songid`s.

Restoring does not consume the entry (§12.7), so each session adds one row for
the restored queue when Kodi quits. That is self-limiting rather than runaway:
if nothing was played, the `System.OnQuit` snapshot matches the entry exactly on
list, position and tick, and §4's dedup drops it. Only sessions where you
actually listened add a row — which is the point of the log.

---

## 12. Decisions

Settled:

1. **Add-on id — `script.music.restore`.** Matches the directory, and now also
   matches the extension point: with decision 6 it really is a script add-on.
2. **Capture on stop as well as on clear.** Required for the video-interrupt
   case (§3.2), which has no `Playlist.OnClear` at all. The §4 dedup rule is what
   stops a pause-and-walk-away from accumulating near-identical rows.
3. **Restore-on-startup is a setting**, default off. See §11.
4. **Restored queues start playing immediately**, with a setting to load paused
   at the saved position instead.
5. **Context-menu removal was never available anyway.** `CGUIDialogSelect`
   handles only `ACTION_SELECT_ITEM` and `ACTION_MOUSE_LEFT_CLICK`
   (`xbmc/dialogs/GUIDialogSelect.cpp:96`); `ListItem.addContextMenuItems()` is
   inert outside a real directory window. Pop-up and context menu are mutually
   exclusive (§7.3).
6. **Removal is dropped entirely.** No *Remove from recent queue*, no
   `multiselect()`, no add-on root, no `pluginsource` extension. The add-on is
   `xbmc.python.script` + `xbmc.service`, with two entry points onto one pop-up
   (§7.2). Costs: the `library://music/` node route (§8.2) and the `music_off`
   icon (§10).

7. **Restoring does not consume the history entry.** The entry stays where it
   is. The list is an append-only log of removal events, consistent with the
   brief's "every instance of a playlist being removed will be recorded".

   Consequence: a restored queue that is later interrupted is captured again, so
   the same album can appear twice — once at the position it was restored from,
   once at the position it reached. Intended, and legible in the list, since
   each row shows its own track position and time (§9).

   Two things follow. The §4 dedup rule stays minimal — suppress only on an
   exact match of track list *and* position *and* tick, so genuinely distinct
   listens are never merged. And the 20-entry cap fills faster than it would
   with a consuming or move-to-front rule; it is a setting for that reason
   (§11), and raising it is the remedy if the list churns too quickly.
