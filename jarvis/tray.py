"""System tray icon: shows Jarvis is running, lets you mute/unmute the mic
and quit cleanly, without needing a console window open."""

from __future__ import annotations

import logging
import threading

from PIL import Image, ImageDraw

logger = logging.getLogger("jarvis.tray")


def _make_icon_image(muted: bool) -> Image.Image:
    size = 64
    color = (150, 150, 150) if muted else (0, 153, 255)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, size - 2, size - 2), fill=color)
    draw.text((size // 2 - 6, size // 2 - 12), "J", fill=(255, 255, 255))
    return img


class TrayApp:
    """Wraps pystray. `muted` is a threading.Event the audio loop checks
    before recording, so the mute toggle in the tray menu takes effect
    immediately without restarting anything."""

    def __init__(self, on_quit, on_show=None):
        import pystray

        self.muted = threading.Event()
        self._on_quit = on_quit
        self._on_show = on_show
        menu_items = []
        if on_show is not None:
            menu_items.append(pystray.MenuItem("Show Jarvis", self._show, default=True))
        menu_items.append(pystray.MenuItem("Mute microphone", self._toggle_mute, checked=lambda item: self.muted.is_set()))
        menu_items.append(pystray.MenuItem("Quit Jarvis", self._quit))

        self._icon = pystray.Icon(
            "jarvis",
            _make_icon_image(False),
            "Jarvis (listening)",
            menu=pystray.Menu(*menu_items),
        )

    def _show(self, icon, item):
        if self._on_show is not None:
            self._on_show()

    def _toggle_mute(self, icon, item):
        if self.muted.is_set():
            self.muted.clear()
            icon.icon = _make_icon_image(False)
            icon.title = "Jarvis (listening)"
        else:
            self.muted.set()
            icon.icon = _make_icon_image(True)
            icon.title = "Jarvis (muted)"

    def _quit(self, icon, item):
        logger.info("Quit requested from tray menu.")
        icon.stop()
        self._on_quit()

    def run(self) -> None:
        """Blocks the calling thread running the tray event loop. Call this
        from the main thread (required on Windows/macOS)."""
        self._icon.run()
