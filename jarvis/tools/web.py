"""Opens URLs / web searches in the user's default browser.

Note: this does NOT give Jarvis live web search / browsing ability -- it can
only open a browser tab. If you want Jarvis to actually read web pages or
search results, wire in a search API (e.g. Brave Search API, Tavily) and
add a matching tool here.
"""

from __future__ import annotations

import logging
import webbrowser
from urllib.parse import quote_plus

logger = logging.getLogger("jarvis.tools.web")


def open_url(url: str) -> str:
    url = url.strip()
    if not url:
        return "No URL given."
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url} in your browser."


def web_search(query: str) -> str:
    query = query.strip()
    if not query:
        return "No search query given."
    url = f"https://www.google.com/search?q={quote_plus(query)}"
    webbrowser.open(url)
    return f"Opened a browser search for '{query}'."
