"""Thin wrapper over ``xbmc.executeJSONRPC``.

Every call returns the ``result`` object, or ``None`` if the call failed. The
recorder runs on Kodi's announcement thread for part of its life and during
shutdown for the rest, so nothing here is allowed to raise: a JSON-RPC failure
at abort time must not stop the final snapshot being written.
"""

import json
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
    without treating both as an empty result. Does not log: a missing song is
    an expected answer during a rebind, and the caller decides whether it is
    worth a line.
    """
    request = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    try:
        raw = xbmc.executeJSONRPC(request)
        response: Dict[str, Any] = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 - see module docstring
        return None, {"message": str(exc)}

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
