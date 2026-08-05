"""Thin wrapper over ``xbmc.executeJSONRPC``.

Every call returns the ``result`` object, or ``None`` if the call failed. The
recorder runs on Kodi's announcement thread for part of its life and during
shutdown for the rest, so nothing here is allowed to raise: a JSON-RPC failure
at abort time must not stop the final snapshot being written.
"""

import json
from typing import Any, Dict, Optional

import xbmc

from . import logging as log


def call(method: str, **params: Any) -> Optional[Any]:
    """Run a JSON-RPC method. Returns its ``result``, or None on any failure."""
    request = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    try:
        raw = xbmc.executeJSONRPC(request)
        response: Dict[str, Any] = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 - see module docstring
        log.error("JSON-RPC %s raised: %s", method, exc)
        return None

    if "error" in response:
        log.error("JSON-RPC %s failed: %s", method, response["error"])
        return None
    return response.get("result")
