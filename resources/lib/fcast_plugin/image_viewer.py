"""Display cast images through Kodi's picture viewer.

Images do not go through xbmc.Player, so none of its callbacks fire for them
and playback state has to be tracked here instead. Senders still expect the
usual Playing/Idle reporting, which is what the callbacks here are for.

Previously an image was handed to the video player, which treated it as a
broken stream: it appeared for a few milliseconds and the player closed again.
"""

import os
import threading
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import xbmc
import xbmcgui
import xbmcvfs

from .util import log

# From xbmc/guilib/WindowIDs.h. Note it is 12007, not the 12005 used for
# fullscreen video.
WINDOW_SLIDESHOW = 12007

# ShowPicture is asynchronous, so the window is not up the instant it returns.
# Allow this long before concluding it failed to open.
OPEN_TIMEOUT = 5.0

DOWNLOAD_TIMEOUT = 15
MAX_IMAGE_BYTES = 64 * 1024 * 1024

CONTENT_TYPE_EXTENSIONS = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'image/bmp': '.bmp',
    'image/avif': '.avif',
    'image/apng': '.apng',
    'image/svg+xml': '.svg',
}


class ImageViewer:
    """Shows one image at a time and reports when it goes away.

    Pictures are fetched to local storage before being handed to Kodi. Kodi
    blanks the viewer while it loads a picture, so pointing it straight at a
    remote URL means the screen is already dark for the whole download. Doing
    the fetch here leaves whatever is currently on screen up until the next
    image is ready to appear.
    """

    def __init__(self, on_closed=None, cache_dir=None):
        self._showing = False
        self._confirmed = False
        self._opened_at = 0.0
        self._on_closed = on_closed
        self._cache_dir = cache_dir
        self._cached_path = None
        # Bumped by every show() and close(), so a download that is no longer
        # wanted can tell that it has been superseded.
        self._generation = 0
        self._lock = threading.Lock()
        self._worker = None

    @property
    def is_showing(self) -> bool:
        return self._showing

    @property
    def is_on_screen(self) -> bool:
        return self._on_screen()

    def show(self, url: str, headers=None) -> None:
        with self._lock:
            self._generation += 1
            generation = self._generation

        self._worker = threading.Thread(
            target=self._fetch_and_display, args=(url, headers, generation), daemon=True)
        self._worker.start()

    def close(self) -> bool:
        """Dismiss the picture viewer. Returns True if there was one to dismiss.

        ACTION_STOP, not Back. Stop is what Kodi binds to X by default, which
        is the action that actually exits the picture viewer, and it is safe
        to send when nothing is showing - it stops playback that is not
        happening. Back would navigate whatever is focused instead.

        Deliberately acts on either our own state or the window actually being
        up, so a sender's Stop still works if the two have drifted apart.
        """
        with self._lock:
            # Cancels any download still in flight.
            self._generation += 1

        on_screen = self._on_screen()
        if not self._showing and not on_screen:
            self._discard_cached()
            return False

        log(f"Closing image viewer (tracked={self._showing}, on screen={on_screen})")
        xbmc.executebuiltin('Action(Stop)')

        self._showing = False
        self._confirmed = False
        self._discard_cached()
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
        self._discard_cached()
        if self._on_closed:
            self._on_closed()

    def _fetch_and_display(self, url: str, headers, generation: int) -> None:
        path = None
        try:
            path = self._download(url, headers, generation)
        except Exception as e:
            # Kodi can fetch it itself; the transition is just less smooth.
            log(f"Could not pre-fetch image, letting Kodi load it directly: {e}")

        if self._superseded(generation):
            self._remove(path)
            return

        self._display(path or url, generation)

    def _display(self, path: str, generation: int) -> None:
        # Deliberately no close() first. ShowPicture delivers
        # GUI_MSG_SHOW_PICTURE straight to the slideshow window, so calling it
        # again while the viewer is open swaps the picture in place. Closing
        # and reopening drops back to the UI behind for a frame, which is what
        # made changing image to image flash dark.
        already_open = self._showing and self._on_screen()

        log(f"Showing image {path}")
        # Kodi's own picture viewer, so scaling, rotation and the background
        # all behave the way they do for local pictures, in any skin.
        xbmc.executebuiltin(f'ShowPicture({path})')

        with self._lock:
            if generation != self._generation:
                return
            previous, self._cached_path = self._cached_path, (
                path if path.startswith(self._temp_dir()) else None)

        self._showing = True
        if not already_open:
            self._confirmed = False
            self._opened_at = time.time()

        # Only once the replacement is on screen, so the old file is never
        # pulled out from under a picture still being displayed.
        if previous and previous != path:
            self._remove(previous)

    def _download(self, url: str, headers, generation: int) -> str:
        request = Request(url, headers={str(k): str(v) for k, v in (headers or {}).items()})
        with urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
            content_type = (response.headers.get('Content-Type') or '').split(';')[0].strip()

        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"image exceeds {MAX_IMAGE_BYTES} bytes")
        if self._superseded(generation):
            raise ValueError("superseded before the download finished")

        path = os.path.join(
            self._temp_dir(),
            f"fcast-image-{generation}{self._extension(url, content_type)}")
        with open(path, 'wb') as handle:
            handle.write(data)
        return path

    def _extension(self, url: str, content_type: str) -> str:
        if content_type in CONTENT_TYPE_EXTENSIONS:
            return CONTENT_TYPE_EXTENSIONS[content_type]
        suffix = os.path.splitext(urlparse(url).path)[1].lower()
        # Kodi picks its decoder from the extension, so give it something.
        return suffix if suffix else '.jpg'

    def _temp_dir(self) -> str:
        if self._cache_dir is None:
            self._cache_dir = xbmcvfs.translatePath('special://temp/')
        return self._cache_dir

    def _superseded(self, generation: int) -> bool:
        with self._lock:
            return generation != self._generation

    def _discard_cached(self) -> None:
        with self._lock:
            path, self._cached_path = self._cached_path, None
        self._remove(path)

    def _remove(self, path) -> None:
        if not path:
            return
        try:
            os.remove(path)
        except OSError:
            pass

    def _on_screen(self) -> bool:
        try:
            return xbmcgui.getCurrentWindowId() == WINDOW_SLIDESHOW
        except Exception:
            return False
