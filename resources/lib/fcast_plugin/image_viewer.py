"""Display cast images through Kodi's picture viewer.

Images do not go through xbmc.Player, so none of its callbacks fire for them
and playback state has to be tracked here instead. Senders still expect the
usual Playing/Idle reporting, which is what the callbacks here are for.

Previously an image was handed to the video player, which treated it as a
broken stream: it appeared for a few milliseconds and the player closed again.
"""

import time

import xbmc
import xbmcgui

from .util import log

# From xbmc/guilib/WindowIDs.h. Note it is 12007, not the 12005 used for
# fullscreen video.
WINDOW_SLIDESHOW = 12007

# ShowPicture is asynchronous, so the window is not up the instant it returns.
# Allow this long before concluding it failed to open.
OPEN_TIMEOUT = 5.0


class ImageViewer:
    """Shows one image at a time and reports when it goes away."""

    def __init__(self, on_closed=None):
        self._showing = False
        self._confirmed = False
        self._opened_at = 0.0
        self._on_closed = on_closed

    @property
    def is_showing(self) -> bool:
        return self._showing

    def show(self, url: str) -> None:
        self.close()

        log(f"Showing image {url}")
        # Kodi's own picture viewer, so scaling, rotation and the background
        # all behave the way they do for local pictures, in any skin.
        xbmc.executebuiltin(f'ShowPicture({url})')

        self._showing = True
        self._confirmed = False
        self._opened_at = time.time()

    def close(self) -> None:
        if not self._showing:
            return

        # Only send the action when the viewer is genuinely on screen. Back is
        # a global input action, and firing it blind would hit whatever else
        # happens to be focused.
        if self._on_screen():
            xbmc.executebuiltin('Action(Back)')

        self._showing = False
        self._confirmed = False

    def poll(self) -> None:
        """Notice the viewer being dismissed from the Kodi UI."""
        if not self._showing:
            return

        if self._on_screen():
            self._confirmed = True
            return

        if not self._confirmed:
            # Still opening, or it never opened at all.
            if time.time() - self._opened_at < OPEN_TIMEOUT:
                return
            log("Image viewer did not open")

        self._showing = False
        self._confirmed = False
        if self._on_closed:
            self._on_closed()

    def _on_screen(self) -> bool:
        try:
            return xbmcgui.getCurrentWindowId() == WINDOW_SLIDESHOW
        except Exception:
            return False
