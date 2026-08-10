"""Addon settings, read fresh each time.

Kodi caches an Addon instance's settings from when it was constructed, so a new
``xbmcaddon.Addon()`` per read is what makes a change in the settings dialog
take effect without a service restart.
"""

import xbmcaddon

ADDON_ID = "script.music.restore"

# Bounds mirror resources/settings.xml; applied here too so a hand-edited
# settings.xml cannot make the history unbounded.
MIN_KEEP = 5
MAX_KEEP = 100
DEFAULT_KEEP = 20


def _addon() -> xbmcaddon.Addon:
    return xbmcaddon.Addon(ADDON_ID)


def keep() -> int:
    """How many queues to hold in the history."""
    try:
        value = _addon().getSettingInt("keep")
    except Exception:  # noqa: BLE001 - unset or malformed settings.xml
        return DEFAULT_KEEP
    return max(MIN_KEEP, min(MAX_KEEP, value))


def restore_on_startup() -> bool:
    """Put the newest queue back when Kodi starts."""
    try:
        return bool(_addon().getSettingBool("restoreOnStartup"))
    except Exception:  # noqa: BLE001
        return False


def start_paused() -> bool:
    """Load a restored queue paused at its saved position instead of playing."""
    try:
        return bool(_addon().getSettingBool("startPaused"))
    except Exception:  # noqa: BLE001
        return False


def from_track_start() -> bool:
    """Start the resume track at 0:00 rather than where it was interrupted."""
    try:
        return bool(_addon().getSettingBool("fromTrackStart"))
    except Exception:  # noqa: BLE001
        return False


def icon() -> str:
    """Full path to the addon's declared icon, for the restore toast."""
    try:
        return str(_addon().getAddonInfo("icon"))
    except Exception:  # noqa: BLE001
        return ""


def localised(string_id: int) -> str:
    return str(_addon().getLocalizedString(string_id))
