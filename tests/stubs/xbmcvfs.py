"""Minimal xbmcvfs stand-in."""

import tempfile

# Tests override this to point special:// paths somewhere disposable.
temp_dir = tempfile.gettempdir()


def translatePath(path):
    if path.startswith("special://temp"):
        return temp_dir
    return path
