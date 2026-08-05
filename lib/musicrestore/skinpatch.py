"""Put the "Recent queues" button on a skin's music categories row.

The row's click behaviour belongs to the skin. Everything an addon can feed
into ``library://music/`` arrives as a folder — ``CLibraryDirectory`` creates
every node item with ``isFolder=true`` — and Kodi's default action for a
folder widget item is ``ActivateWindow`` (``CExecString::Parse``). The pop-up
needs an ``<onclick>``, and only skin XML can declare one. So, on the user's
say-so from the addon settings, the skin is edited directly — the same move
KodiSeerr uses to put its Request button into Embuary Info.

Estuary already has the extension point. ``WidgetListCategories`` merges
``MovieSubmenuItems`` / ``TVShowSubmenuItems`` static items into the movie and
TV categories rows — a control with two ``<content>`` blocks gets a
``CMultiProvider``, concatenated in declaration order — and Contuary inherits
the same include. The patch adds the missing music variant: a
``MusicSubmenuItems`` include holding one ``RunScript`` item, the hook that
mounts it in the categories panel, the parameter default that keeps it off
every other row, and ``additional_music_items=true`` on the music row's call
site in ``Home.xml``.

Idempotent per file (the addon id is the marker), all-or-nothing per run (no
file is written unless every anchor in every unpatched file matched), and
reversible: each injected line carries a marker comment and
:func:`remove_text` strips exactly those, byte-exact.

A skin update overwrites the change; it is re-applied from the same settings
button, as with KodiSeerr. When the skin lives in Kodi's read-only system
directory (bundled Estuary on Linux), the skin is first copied under
``special://home/addons/`` and the copy is patched: ``CAddonMgr::FindAddons``
scans the home directory last and a later find wins unless the earlier
version is strictly higher, so the copy shadows the bundled skin at equal
versions — and a Kodi upgrade that ships a newer skin simply out-versions the
patch rather than breaking.
"""

import os
import re
import shutil
from typing import Dict, List, NamedTuple, Optional, Tuple

import xbmc
import xbmcgui
import xbmcvfs

from . import logging as log, settings

SUPPORTED_SKINS = ("skin.contuary", "skin.estuary")

INCLUDES_FILE = "xml/Includes_Home.xml"
HOME_FILE = "xml/Home.xml"

MARKER = "script.music.restore"
LINE_MARK = "<!-- script.music.restore -->"
BLOCK_BEGIN = "<!-- script.music.restore:begin -->"
BLOCK_END = "<!-- script.music.restore:end -->"

APPLIED = 30019
PRESENT = 30020
UNSUPPORTED = 30021
ANCHOR_LOST = 30022
IO_ERROR = 30023
REMOVED = 30024
NOT_PRESENT = 30025
COPIED_NOTE = 30026
UPDATE_NOTE = 30027

FALLBACK = {
    APPLIED: "The Recent queues button is now on the music categories row.",
    PRESENT: "The button is already in place.",
    UNSUPPORTED: (
        "The running skin is not one this can edit. "
        "Estuary and Contuary are supported."
    ),
    ANCHOR_LOST: (
        "The skin's files do not look as expected, so nothing was changed. "
        "A skin update may have moved the places this edit anchors to."
    ),
    IO_ERROR: "The skin could not be edited: {0}",
    REMOVED: "The button was taken out of the skin.",
    NOT_PRESENT: "There was no button to take out.",
    COPIED_NOTE: (
        "Kodi's own copy of the skin is read-only, so an editable copy was "
        "placed in your user add-ons and the button added there. Kodi now "
        "uses that copy; a Kodi upgrade with a newer skin returns to the "
        "original — and to no button."
    ),
    UPDATE_NOTE: (
        "A skin update removes the button; if it disappears, run this again."
    ),
}


class Insertion(NamedTuple):
    """One anchored insertion. ``{i}`` in the template is the anchor's indent."""

    name: str
    anchor: "re.Pattern[str]"
    template: str


INSERTIONS: Dict[str, List[Insertion]] = {
    INCLUDES_FILE: [
        Insertion(
            "categories parameter default",
            re.compile(
                r'(?P<indent>[ \t]*)<param name="additional_tvshow_items">'
                r"false</param>"
            ),
            '{i}<param name="additional_music_items">false</param>' + LINE_MARK,
        ),
        Insertion(
            "categories hook",
            re.compile(
                r"(?P<indent>[ \t]*)<include "
                r'condition="\$PARAM\[additional_tvshow_items\]" '
                r'content="TVShowSubmenuItems" />'
            ),
            '{i}<include condition="$PARAM[additional_music_items]" '
            'content="MusicSubmenuItems" />' + LINE_MARK,
        ),
        Insertion(
            "MusicSubmenuItems include",
            re.compile(
                r'(?P<indent>[ \t]*)<include name="TVShowSubmenuItems">'
                r".*?</include>",
                re.DOTALL,
            ),
            "{i}" + BLOCK_BEGIN + "\n"
            '{i}<include name="MusicSubmenuItems">\n'
            "{i}\t<content>\n"
            "{i}\t\t<item>\n"
            "{i}\t\t\t<label>$ADDON[script.music.restore 30010]</label>\n"
            "{i}\t\t\t<onclick>RunScript(script.music.restore)</onclick>\n"
            "{i}\t\t\t<thumb>special://home/addons/script.music.restore/"
            "resources/button.png</thumb>\n"
            "{i}\t\t\t<visible>System.AddonIsEnabled(script.music.restore)"
            "</visible>\n"
            "{i}\t\t</item>\n"
            "{i}\t</content>\n"
            "{i}</include>\n"
            "{i}" + BLOCK_END,
        ),
    ],
    HOME_FILE: [
        Insertion(
            "music row parameter",
            re.compile(
                r'(?P<indent>[ \t]*)<param name="content_path" '
                r'value="library://music/"/>'
            ),
            '{i}<param name="additional_music_items" value="true"/>' + LINE_MARK,
        ),
    ],
}

# Removal is textual rather than anchored: anything we wrote carries a marker,
# so what gets stripped is exactly what apply wrote, wherever it has drifted.
_BLOCK_RE = re.compile(
    r"\n?[ \t]*" + re.escape(BLOCK_BEGIN) + r".*?" + re.escape(BLOCK_END),
    re.DOTALL,
)
_LINE_RE = re.compile(r"\n?[^\n]*" + re.escape(LINE_MARK))


def is_patched(text: str) -> bool:
    return MARKER in text


def needs_patch(text: str) -> bool:
    """False when the edit is already there — ours, or the skin's own.

    A skin release that adopts the music-items hook natively would collide
    with a second ``MusicSubmenuItems`` definition; an unmarked
    ``additional_music_items`` means the skin already does this itself.
    """
    return MARKER not in text and "additional_music_items" not in text


def apply_text(text: str, insertions: List[Insertion]) -> Tuple[str, List[str]]:
    """Insert every block after its anchor. Returns (new text, missing anchors).

    The caller must not write the result when anything is missing — a partial
    patch (the hook without the include it mounts) would break the skin.
    """
    missing: List[str] = []
    for insertion in insertions:
        match = insertion.anchor.search(text)
        if match is None:
            missing.append(insertion.name)
            continue
        rendered = insertion.template.format(i=match.group("indent"))
        text = text[: match.end()] + "\n" + rendered + text[match.end() :]
    return text, missing


def remove_text(text: str) -> str:
    """Strip every marked line and block. Inverse of :func:`apply_text`."""
    text = _BLOCK_RE.sub("", text)
    return _LINE_RE.sub("", text)


def _skin_root(skin_id: str) -> Tuple[Optional[str], bool]:
    """Where the skin's files should be edited. Returns (path, copied).

    ``copied`` is True when the bundled read-only skin was first duplicated
    into the user's addon directory so there is something writable to edit.
    """
    home = xbmcvfs.translatePath("special://home/addons/%s/" % skin_id)
    if os.path.isdir(home):
        return home, False

    system = xbmcvfs.translatePath("special://xbmc/addons/%s/" % skin_id)
    if not os.path.isdir(system):
        return None, False
    if os.access(os.path.join(system, HOME_FILE), os.W_OK):
        return system, False

    try:
        # symlinks=True: Debian packages the skin's fonts as symlinks into the
        # system font packages, some of them dangling. Following them fails on
        # the dangling ones; copying them as links reproduces exactly the state
        # the skin already runs with.
        shutil.copytree(system, home, symlinks=True)
    except OSError as exc:
        log.error("could not copy %s to %s: %s", system, home, exc)
        # Never leave a partial copy: at equal versions it would shadow the
        # real skin with files missing.
        shutil.rmtree(home, ignore_errors=True)
        return None, False
    log.info("copied read-only skin %s to %s", skin_id, home)
    return home, True


def _apply(skin_id: str) -> Tuple[str, str]:
    """Returns (status, detail). Detail only carries error text."""
    if skin_id not in SUPPORTED_SKINS:
        return "unsupported", skin_id

    root, copied = _skin_root(skin_id)
    if root is None:
        return "write_error", skin_id

    # Read and patch everything first; write only when every file worked out.
    pending: List[Tuple[str, str]] = []
    lost: List[str] = []
    for rel, insertions in INSERTIONS.items():
        path = os.path.join(root, rel)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            return "read_error", str(exc)
        if not needs_patch(text):
            continue  # this file survived; a skin update may have reset others
        new_text, missing = apply_text(text, insertions)
        if missing:
            lost.extend("%s (%s)" % (name, rel) for name in missing)
            continue
        pending.append((path, new_text))

    if lost:
        log.error("skin anchors not found: %s", "; ".join(lost))
        return "anchor_lost", "; ".join(lost)
    if not pending:
        return "present", ""

    for path, new_text in pending:
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(new_text)
        except OSError as exc:
            return "write_error", str(exc)
        log.info("patched %s", path)

    if copied:
        # The copy is a new directory as far as Kodi's addon scan goes.
        xbmc.executebuiltin("UpdateLocalAddons()")
        return "applied_copy", ""
    return "applied", ""


def _remove(skin_id: str) -> Tuple[str, str]:
    if skin_id not in SUPPORTED_SKINS:
        return "unsupported", skin_id

    root, _ = _skin_root(skin_id)
    if root is None:
        return "not_present", ""

    changed = False
    for rel in INSERTIONS:
        path = os.path.join(root, rel)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            continue
        if not is_patched(text):
            continue
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(remove_text(text))
        except OSError as exc:
            return "write_error", str(exc)
        log.info("unpatched %s", path)
        changed = True

    return ("removed", "") if changed else ("not_present", "")


def _text(string_id: int) -> str:
    return settings.localised(string_id) or FALLBACK[string_id]


def run_interactive(action: str) -> None:
    """Entry point for the two settings buttons (RunScript with an argument)."""
    skin_id = xbmc.getSkinDir()
    status, detail = _remove(skin_id) if action == "remove" else _apply(skin_id)

    lines = {
        "applied": [APPLIED, UPDATE_NOTE],
        "applied_copy": [APPLIED, COPIED_NOTE],
        "present": [PRESENT],
        "unsupported": [UNSUPPORTED],
        "anchor_lost": [ANCHOR_LOST],
        "read_error": [IO_ERROR],
        "write_error": [IO_ERROR],
        "removed": [REMOVED],
        "not_present": [NOT_PRESENT],
    }[status]
    message = "\n".join(_text(line) for line in lines)
    if status in ("read_error", "write_error"):
        message = message.format(detail)

    xbmcgui.Dialog().ok("Restore Music Queue", message)

    # Only after the user has seen the outcome — a reload closes dialogs.
    if status in ("applied", "applied_copy", "removed"):
        xbmc.executebuiltin("ReloadSkin()")
