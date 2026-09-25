"""Thin wrapper over ``xbmc.executeJSONRPC``.

Every call returns the ``result`` object, or ``None`` if the call failed. The
recorder runs on Kodi's announcement thread for part of its life and during
shutdown for the rest, so nothing here is allowed to raise: a JSON-RPC failure
at abort time must not stop the final snapshot being written.
"""

import json
import time
from typing import Any, Dict, Optional, Tuple

import xbmc

from . import logging as log


def invoke(
    method: str, **params: Any
) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """Run a JSON-RPC method. Returns ``(result, error)``.

    ``error`` is the JSON-RPC error object, or None when the call returned a
    result. A transport failure is reported as an error object too, so callers
    can tell "Kodi answered, and the id is gone" from "Kodi could not be asked"
    without treating both as an empty result. Missing songs are expected during
    a rebind, so response contents are never logged. The GetSongs timing line
    reports only durations and response size.
    """
    serializing = time.perf_counter()
    request = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    serialized = time.perf_counter()
    try:
        raw = xbmc.executeJSONRPC(request)
        executed = time.perf_counter()
        response: Dict[str, Any] = json.loads(raw)
        decoded = time.perf_counter()
    except Exception as exc:  # noqa: BLE001 - see module docstring
        if method == "AudioLibrary.GetSongs":
            log.info(
                "JSON-RPC GetSongs failed after %.0fms",
                (time.perf_counter() - serializing) * 1000.0,
            )
        return None, {"message": str(exc)}

    if method == "AudioLibrary.GetSongs":
        log.info(
            "JSON-RPC GetSongs encode %.0fms, Kodi call %.0fms, decode %.0fms,"
            " response %d bytes",
            (serialized - serializing) * 1000.0,
            (executed - serialized) * 1000.0,
            (decoded - executed) * 1000.0,
            len(raw),
        )

    if "error" in response:
        error = response["error"]
        return None, error if isinstance(error, dict) else {"message": str(error)}
    return response.get("result"), None


def call(method: str, **params: Any) -> Optional[Any]:
    """Run a JSON-RPC method. Returns its ``result``, or None on any failure."""
    result, error = invoke(method, **params)
    if error is not None:
        log.error("JSON-RPC %s failed: %s", method, error)
        return None
    return result
