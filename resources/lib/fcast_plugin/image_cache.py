"""Fetch a picture to local storage before it goes on screen.

Kodi's picture viewer resets the slide the moment it is told to show
something new (GUI_MSG_SHOW_PICTURE does Reset, Add, RunSlideShow), so the
picture already up disappears before the next one has arrived and the screen
is black for as long as the download takes. Fetching it here first means the
picture on screen stays until the next is ready to draw.

The file is written with the extension its own bytes call for, and that is not
cosmetic: Kodi chooses the image decoder from the extension, turning it into a
mime type in ImageFactory::CreateLoader. A cached file with the wrong
extension - or none - decodes as nothing and shows as a black screen, which is
what an earlier attempt at this did. Bytes we cannot identify are not cached
at all; the URL goes to Kodi instead, exactly as before.
"""

import os
from typing import Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import xbmcvfs

from .util import log

CACHE_DIR = 'special://temp/service.fcast.receiver/'

FETCH_TIMEOUT = 20
# Enough for a phone camera's full-size photo, and a ceiling on what a sender
# can make us write to a device whose storage may be a memory card.
MAX_BYTES = 64 * 1024 * 1024
CHUNK = 64 * 1024

# What the first bytes of a picture look like. Trusting these rather than the
# container the sender declared also rejects the other thing a URL can return
# with a 200: an HTML error page, which would otherwise be cached as a picture
# and displayed as a black screen.
SIGNATURES = (
    (b'\xff\xd8\xff', '.jpg'),
    (b'\x89PNG\r\n\x1a\n', '.png'),
    (b'GIF87a', '.gif'),
    (b'GIF89a', '.gif'),
    (b'BM', '.bmp'),
    (b'\x00\x00\x01\x00', '.ico'),
)


def extension_for(head: bytes) -> Optional[str]:
    """The extension these bytes call for, or None if they are not a picture."""
    for signature, extension in SIGNATURES:
        if head.startswith(signature):
            return extension

    # Both are RIFF containers, told apart by the tag at byte 8.
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return '.webp'
    # ISO base media, which is how AVIF and HEIC arrive.
    if head[4:8] == b'ftyp':
        brand = head[8:12]
        if brand in (b'avif', b'avis'):
            return '.avif'
        if brand in (b'heic', b'heix', b'mif1', b'msf1'):
            return '.heic'
    return None


def directory() -> str:
    path = xbmcvfs.translatePath(CACHE_DIR)
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
    return path


def fetch(url: str, headers=None, name: str = 'picture') -> Optional[str]:
    """Download a picture and return its local path, or None to use the URL.

    None is not a failure the caller needs to report: it means show the URL
    the way we always have and let Kodi do the fetching.

    `name` must be different for each picture. Two photos are quite often both
    called image.jpg, and reusing the path would overwrite the file Kodi is
    reading to draw what is on screen.
    """
    path = None
    try:
        request = Request(url, headers={str(k): str(v)
                                        for k, v in (headers or {}).items()})
        with urlopen(request, timeout=FETCH_TIMEOUT) as response:
            head = response.read(32)
            extension = extension_for(head)
            if extension is None:
                log(f"Not caching {url}: {head[:8]!r} is not a picture we know")
                return None

            path = os.path.join(directory(), f'{name}{extension}')
            written = len(head)
            with open(path, 'wb') as picture:
                picture.write(head)
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_BYTES:
                        raise ValueError(f"picture exceeds {MAX_BYTES} bytes")
                    picture.write(chunk)

        log(f"Cached {url} as {path} ({written} bytes)")
        return path
    except HTTPError as e:
        # An HTTPError is itself the response, holding the socket, so it has
        # to be closed rather than just logged.
        log(f"Could not cache {url}: {e}")
        e.close()
        remove(path)
        return None
    except Exception as e:
        log(f"Could not cache {url}: {e}")
        remove(path)
        return None


def remove(path: Optional[str]) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def clear(keep: Optional[str] = None) -> None:
    """Throw away cached pictures, apart from one still on screen.

    Also run at start-up: a crash or a pulled plug leaves the last picture
    behind, and nothing else ever cleans this directory out.
    """
    try:
        path = directory()
        for entry in os.listdir(path):
            entry = os.path.join(path, entry)
            if entry != keep:
                remove(entry)
    except Exception as e:
        log(f"Could not clear the picture cache: {e}")
