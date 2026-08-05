"""Logging that carries the addon id, so the log is greppable."""

from typing import Any

import xbmc

PREFIX = "[script.music.restore]"


def info(message: str, *args: Any) -> None:
    xbmc.log("%s %s" % (PREFIX, message % args if args else message), xbmc.LOGINFO)


def debug(message: str, *args: Any) -> None:
    xbmc.log("%s %s" % (PREFIX, message % args if args else message), xbmc.LOGDEBUG)


def error(message: str, *args: Any) -> None:
    xbmc.log("%s %s" % (PREFIX, message % args if args else message), xbmc.LOGERROR)
