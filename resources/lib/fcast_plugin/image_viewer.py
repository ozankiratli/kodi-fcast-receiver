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

    @property
    def is_on_screen(self) -> bool:
        return self._on_screen()

    def show(self, url: str) -> None:
        # Deliberately no close() first. ShowPicture delivers
        # GUI_MSG_SHOW_PICTURE straight to the slideshow window, so calling it
        # again while the viewer is open swaps the picture in place. Closing
        # and reopening drops back to the UI behind for a frame, which is what
        # made changing image to image flash dark.
        already_open = self._showing and self._on_screen()

        log(f"Showing image {url}")
        # Kodi's own picture viewer, so scaling, rotation and the background
        # all behave the way they do for local pictures, in any skin.
        xbmc.executebuiltin(f'ShowPicture({url})')

        self._showing = True
        if not already_open:
            self._confirmed = False
            self._opened_at = time.time()

    def close(self) -> bool:
        """Dismiss the picture viewer. Returns True if there was one to dismiss.

        ACTION_STOP, not Back. Stop is what Kodi binds to X by default, which
        is the action that actually exits the picture viewer, and it is safe
        to send when nothing is showing - it stops playback that is not
        happening. Back would navigate whatever is focused instead.

        Deliberately acts on either our own state or the window actually being
        up, so a sender's Stop still works if the two have drifted apart.
        """
        on_screen = self._on_screen()
        if not self._showing and not on_screen:
            return False

        log(f"Closing image viewer (tracked={self._showing}, on screen={on_screen})")
        xbmc.executebuiltin('Action(Stop)')

        self._showing = False
        self._confirmed = False
        return True

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
        """Whether Kodi's picture viewer is currently up.

        Checks the dialog id as well as the window id. CGUIWindowSlideShow
        derives from CGUIDialog despite its name and its 12xxx id, so it is
        the *dialog* that reports 12007 and getCurrentWindowId() returns
        whatever is underneath. Asking only the window meant this was always
        False: the poll then decided the viewer had failed to open, told
        senders playback was idle five seconds in, and left close() with
        nothing to act on when a sender asked to stop.
        """
        try:
            return WINDOW_SLIDESHOW in (
                xbmcgui.getCurrentWindowId(),
                xbmcgui.getCurrentWindowDialogId(),
            )
        except Exception:
            return False
