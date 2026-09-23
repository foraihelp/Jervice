"""Web tools.

Two different kinds of tool live here, and the distinction matters:

- `open_url` is the ONLY tool in this module allowed to call
  `webbrowser.open()`. It's for when the user explicitly wants a page
  opened -- "open github.com", "open my email" -- and is a visible,
  disruptive action (it interrupts whatever the user's doing to bring a
  browser window forward).
- `web_search` and `wikipedia_lookup` are informational lookups: the user
  asked a question, not asked for a browser tab. Both run entirely in the
  background and return real text content for the model to read and
  summarize in its spoken reply -- neither may fall back to
  `webbrowser.open()` on failure. An informational query that comes up
  empty should be answered honestly ("I couldn't find anything for
  that"), not silently turned into a surprise browser popup, which is a
  jarring, disruptive experience for what looked like a normal
  conversational question with a spoken answer.

`web_search` uses the `ddgs` package (the actively maintained project
formerly known as `duckduckgo_search`) for real DuckDuckGo results.
An earlier version used DuckDuckGo's "instant answer" API
(api.duckduckgo.com) instead -- that API turned out to be effectively
dead for real-world queries (it returns a placeholder test stub,
`{"meta": {"id": "just_another_test", ...}}`, for ordinary searches
rather than an actual answer, confirmed by inspecting the raw response).
`ddgs` scrapes DuckDuckGo's actual search results instead, which is what
still works.

`wikipedia_lookup` uses the standard library's urllib directly (no need
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
_MAX_SEARCH_RESULTS = 5


def open_url(url: str) -> str:
    """Opens a URL in the browser. Only for when the user explicitly asks
    to open a site/link -- NOT for answering a question. See web_search/
    wikipedia_lookup for informational lookups, which answer in text
    instead of interrupting the user with a browser window."""
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
    """Runs a real DuckDuckGo search (via `ddgs`, entirely in the
    background -- no browser window) and returns the top results' title +
    snippet as text for the model to summarize. Never opens a browser,
    including on failure: an empty/failed search returns a plain text
    message the model can relay honestly, since silently popping open a
    browser in response to what looked like a normal question would be a
    jarring surprise for the user."""
    query = query.strip()
    if not query:
        return "No search query given."

    try:
        from ddgs import DDGS

        results = DDGS().text(query, max_results=_MAX_SEARCH_RESULTS)
        lines = [f"{r['title']}: {r['body']}" for r in (results or []) if r.get("body")]
        if lines:
            return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Web search failed for %r: %s", query, exc)

    return f"I couldn't find any search results for '{query}'."


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
