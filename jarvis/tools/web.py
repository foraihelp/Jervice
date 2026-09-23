"""Web tools: opening URLs/browser searches (no network call, just launches
the OS browser), plus actual live lookups that return real content back to
the model -- a real DuckDuckGo web search (via the `ddgs` package) and a
Wikipedia summary.

An earlier version of web_search() used DuckDuckGo's "instant answer" API
(api.duckduckgo.com) instead of `ddgs` -- that API turned out to be
effectively dead for real-world queries (it returns a placeholder test
stub, `{"meta": {"id": "just_another_test", ...}}`, for ordinary searches
rather than an actual answer, confirmed by inspecting the raw response).
`ddgs` scrapes DuckDuckGo's actual search results instead, which is what
still works.

Wikipedia's lookup uses the standard library's urllib directly (no need
for a whole search library just for one well-behaved REST API) and never
raises -- network failures come back as a plain string the model can
react to.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

logger = logging.getLogger("jarvis.tools.web")

_USER_AGENT = "Jarvis-Assistant (local personal use)"
_REQUEST_TIMEOUT = 8
_MAX_SEARCH_RESULTS = 3


def open_url(url: str) -> str:
    url = url.strip()
    if not url:
        return "No URL given."
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url} in your browser."


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def web_search(query: str) -> str:
    """Runs a real DuckDuckGo search and returns the top few results'
    title + snippet as text, for the model to read/summarize. Falls back
    to opening a full browser search if the search library itself fails
    (e.g. DuckDuckGo blocking the request), so this tool is never less
    useful than just opening a browser tab."""
    query = query.strip()
    if not query:
        return "No search query given."

    try:
        from ddgs import DDGS

        results = DDGS().text(query, max_results=_MAX_SEARCH_RESULTS)
        if results:
            lines = [f"{r['title']}: {r['body']}" for r in results if r.get("body")]
            if lines:
                return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Web search failed for %r: %s", query, exc)

    webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}")
    return (
        f"I couldn't get search results for '{query}' directly, so I opened "
        "a full browser search for it instead."
    )


def wikipedia_lookup(query: str) -> str:
    """Fetches a short summary of a Wikipedia article via Wikipedia's
    public REST API. Good for "who/what is X" questions; not a general
    search -- `query` should be close to an actual article title."""
    query = query.strip()
    if not query:
        return "No topic given."

    title = urllib.parse.quote(query.replace(" ", "_"))
    try:
        data = _get_json(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return f"I couldn't find a Wikipedia article for '{query}'."
        logger.warning("Wikipedia lookup failed for %r: %s", query, exc)
        return f"I couldn't reach Wikipedia right now: {exc}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wikipedia lookup failed for %r: %s", query, exc)
        return f"I couldn't reach Wikipedia right now: {exc}"

    if data.get("type") == "disambiguation":
        return f"'{query}' could mean several things on Wikipedia -- try being more specific."

    extract = (data.get("extract") or "").strip()
    return extract if extract else f"Wikipedia has an article titled '{query}' but no summary text."
