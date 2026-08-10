# Restore Music Queue

A Kodi add-on that keeps a list of stopped music queues, allowing you to restore music playback to a previous state. Inspired by the Finamp Restore Now Playing feature.

Kodi throws a music queue away without ceremony. Start an album while another is playing and the first one is gone. Start a video and the tracks survive but Kodi forgets where you were. Quit and everything goes. This records each of those moments and offers them back.

## Usage

In [Contuary](https://github.com/kontell/skin.contuary), there's a button in the categories row of the music main menu section.

Otherwise run it from Add-ons -> Program add-ons. Both open the same pop-up.

![Screenshot 1](resources/screenshot.jpg)

### Settings

| Setting | Default |     |
| --- | --- | --- |
| Queues to keep | 20  | Oldest drops off when full |
| Restore the last queue when Kodi starts | off |     |
| Start restored queues paused | off | On: loads at the right point and waits for play |
| Restore from the start of the track | off | On: right track, but from 0:00 rather than mid-track |

## What gets recorded

A queue is saved whenever it is taken away:

|     |     |
| --- | --- |
| A new album or playlist replaces it | `Playlist.OnClear` |
| A video interrupts it | `Player.OnStop` — the tracks survive, the position does not |
| Playback is stopped, or the queue ends | `Player.OnStop` |
| Kodi quits | `System.OnQuit` |

The history is a log, not a set. Play an album twice and it appears twice, at whatever point each listen reached. The only thing suppressed is an exact  
repeat — same tracks, same track playing, same second — which is one event being reported twice rather than a second listen.

History lives at  
`special://profile/addon_data/script.music.restore/queues.json`

## Adding button to Estuary

Contuary ships the button. Stock Estuary does not, and this add-on no longer edits skins to put it there — so on Estuary it is four edits by hand, all in the skin's own `xml/` directory.

If Kodi's copy of Estuary is read-only (the usual case on Linux, under `special://xbmc/addons/`), copy the whole `skin.estuary` directory to `special://home/addons/` first and edit the copy. Kodi scans the home directory last and a later find wins at equal versions, so the copy shadows the bundled skin — and a Kodi upgrade shipping a newer Estuary out-versions it, which takes the button away again.

**1–3. `xml/Includes_Home.xml`.** In the `WidgetListCategories` include, add a parameter default beside the two that are already there:

```xml
<param name="additional_music_items">false</param>
```

then, just below the `TVShowSubmenuItems` line inside the same include, the hook that mounts it:

```xml
<include condition="$PARAM[additional_music_items]" content="MusicSubmenuItems" />
```

then, after the whole `TVShowSubmenuItems` include ends, the include itself:

```xml
<include name="MusicSubmenuItems">
    <content>
        <item>
            <label>$ADDON[script.music.restore 30010]</label>
            <onclick>RunScript(script.music.restore)</onclick>
            <thumb>special://home/addons/script.music.restore/resources/button.png</thumb>
            <visible>System.AddonIsEnabled(script.music.restore)</visible>
        </item>
    </content>
</include>
```

**4. `xml/Home.xml`.** On the music `WidgetListCategories` call site — the one whose `content_path` is `library://music/` — switch the row on:

```xml
<param name="additional_music_items" value="true"/>
```

Then `ReloadSkin()`, or restart Kodi. The button appears at the head of the music categories row.

The `<onclick>` is the point of all this: everything an add-on feeds into `library://music/` arrives as a folder, and Kodi's default action for a folder widget item is `ActivateWindow`. Only skin XML can override that, which is why the pop-up cannot be opened from a plain library node.

