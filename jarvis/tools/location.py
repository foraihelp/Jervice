"""Approximate location via IP-based geolocation.

This is a desktop PC with no real GPS hardware, so "where am I" can only be
answered by asking a geolocation service to guess from the machine's public
IP address -- typically accurate to city level, not GPS-precise, and can be
wrong when on a VPN or a mobile carrier's network. Both the tool description
(registry.py) and the text returned here say so explicitly, rather than
implying a precision this doesn't have.

Uses ipapi.co's free JSON endpoint (no API key required for personal-use
volumes) via urllib directly, same pattern as web.py's wikipedia_lookup --
no need for an extra HTTP client dependency for one well-behaved REST call.
"""

from __future__ import annotations

import json
import logging
import urllib.request

logger = logging.getLogger("jarvis.tools.location")

_USER_AGENT = "Jarvis-Assistant (local personal use)"
_REQUEST_TIMEOUT = 8


def get_current_location() -> str:
    """Looks up an approximate location (city/region/country, plus rough
    coordinates) via IP-based geolocation. Never raises -- network/service
    failures come back as a plain string the model can relay honestly."""
    try:
        req = urllib.request.Request("https://ipapi.co/json/", headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Location lookup failed: %s", exc)
        return "I couldn't determine your location right now -- the lookup service didn't respond."

    if data.get("error"):
        reason = data.get("reason", "unknown error")
        logger.warning("Location lookup returned an error: %s", reason)
        return f"I couldn't determine your location: {reason}."

    city = data.get("city")
    region = data.get("region")
    country = data.get("country_name")
    lat = data.get("latitude")
    lon = data.get("longitude")

    place = ", ".join(p for p in (city, region, country) if p) or "an unknown location"
    coords = f" (approximately {lat}, {lon})" if lat is not None and lon is not None else ""

    return (
        f"Based on your internet connection, you appear to be near {place}{coords}. "
        "This is IP-based, not real GPS -- it's usually only accurate to city level, "
        "and can be off if you're using a VPN."
    )
