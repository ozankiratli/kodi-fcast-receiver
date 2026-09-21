import sys
import json
import socket
import time
from itertools import count
from threading import Thread
from typing import List, Optional
import xbmcgui
import xbmc
import selectors
from urllib.parse import urlencode, urlparse
from pathlib import Path

from .FCastSession import Event, FCastSession, FCAST_VERSION
from .FCastPackets import *
from .FCastHTTPServer import FCastHTTPServer
from .player import FCastPlayer
from .image_viewer import ImageViewer
from . import image_cache
from .playlist import Playlist, is_playlist, parse_playlist
from . import settings
from .util import log, notify, debounce, addonversion
from .mdns import register as mdns_register, unregister as mdns_unregister

session_threads: List[Thread] = []
sessions: List[FCastSession] = []

# Constants
FCAST_HOST = ''
FCAST_PORT = 46899
FCAST_BUFFER_SIZE = 32000

# What a sender that has left the network looks like from this end: no FIN, no
# RST, nothing to read, and a connection that would otherwise hold its thread
# and its place in the broadcast list for as long as Kodi runs. These hand the
# question to the kernel, which is the only party that can see that nothing is
# coming back.
#
# KEEPALIVE_* covers a connection with nothing outstanding. USER_TIMEOUT_MS
# covers one with data already written, which during playback is nearly always
# -- position updates go out twenty times a second.
KEEPALIVE_IDLE = 20       # seconds of silence before the first probe
KEEPALIVE_INTERVAL = 5    # seconds between probes
KEEPALIVE_COUNT = 3       # probes unanswered before the peer is written off
USER_TIMEOUT_MS = 20000   # how long written data may sit unacknowledged

# How long to leave a player alone after asking it to stop. Kodi tears the
# player down on its own thread, and a stream whose source has gone off the
# network holds that teardown open for as long as the socket takes to give up.
# Every player call made meanwhile queues behind the same lock, so polling
# through it reports nothing that has changed and parks one more thread inside
# Kodi for the duration.
STOP_SETTLE = 30.0

plugin_handle = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None

player_thread: Optional[Thread] = None

# HTTP Server to stream manifest files
http_server: Optional[FCastHTTPServer] = None

# Player needs to be a global so it stays in scope and doesn't get GC'd
player: Optional[FCastPlayer] = None

# Used to queue up seeks
seeks: list[float] = []

# The Play request currently on screen. Senders that connect later, and the
# other senders already connected, are told about it so every remote shows the
# same thing.
current_play_message: Optional[PlayMessage] = None

# Last volume published to senders, so the poll only speaks up on a change.
last_volume: Optional[float] = None

# Where the sender that asked for what is on screen connected from. A sender
# that serves the media itself takes the stream with it when it leaves, and
# this is what says whether the sender that just went away was that one.
current_play_peer: Optional[str] = None

# When we last asked Kodi to stop, so the poll can stay out of a teardown that
# has not finished. Zero means nothing is pending. See STOP_SETTLE.
stop_requested_at: float = 0.0

# The queue a sender handed over, if any. None when playing a single item.
playlist: Optional[Playlist] = None

# Identifies picture requests, so a download that finishes after the next one
# was asked for knows it has been superseded and drops what it fetched. The
# counter is stepped from the connection threads and from the player thread,
# hence itertools.count: next() on it is a single C call, where += would be a
# read and a write with room in between.
image_requests = count(1)
image_request: int = 0

# The picture that is on screen or on its way to it, so a sender re-sending
# the one already up does not make us tear the viewer down and rebuild it.
image_url: Optional[str] = None
image_pending: bool = False

# How often to tell Kodi a picture on screen counts as the box being in use.
# Kodi's shortest screensaver delay is a minute, so this has room to spare.
WAKE_INTERVAL = 30.0
last_wake: float = 0.0

# Pictures bypass the player entirely, so they get their own viewer. The
# lambdas defer resolving the callbacks, which are defined further down.
image_viewer = ImageViewer(on_closed=lambda: on_image_closed(),
                           on_expired=lambda: on_image_expired())

def get_current_play_data() -> Optional[PlayMessage]:
    """What is on screen right now, or None if nothing is.

    For the Initial handshake, where a sender connecting mid-playback needs to
    know what is showing. Gated on the player rather than cleared on stop, so
    it cannot go stale if playback ends by a route that does not run through
    us - and on the viewer as well as the player, because a picture never
    reaches the player at all. Asking only the player meant every PlayUpdate
    sent while a picture was up carried nothing, and a sender connecting to a
    receiver with a photo on screen was told it was idle.
    """
    if player and player.isPlaying():
        return current_play_message
    if image_viewer.is_showing:
        return current_play_message
    return None

def get_last_play_data() -> Optional[PlayMessage]:
    """The most recent Play request, playing or not.

    Media events fire *after* the player has torn down, so this must not be
    gated on isPlaying() the way the handshake's view is - senders match the
    item in a MediaItemEnd against their own queue entry, and a null item
    tells them nothing.
    """
    return current_play_message

def broadcast_play_update() -> None:
    for session in list(sessions):
        session.send_play_update(get_current_play_data())

def stop_pending() -> bool:
    """Whether a stop we asked for has yet to take effect.

    Kodi answers a stop on its own thread and holds the player while it does,
    so every call made in the meantime -- isPlaying() and getTime() twenty
    times a second among them -- waits on the same lock. When the source is a
    machine that has gone off the network that wait is however long its socket
    takes to give up, so this keeps the poll out of it. It clears as soon as
    Kodi reports the stop, which for anything still reachable is immediate.
    """
    if not stop_requested_at:
        return False
    if player and not player.owns_playback:
        return False
    return time.time() - stop_requested_at < STOP_SETTLE

def stop_playback() -> None:
    """Ask Kodi to stop what is playing, and abandon any queue behind it.

    On a thread of its own, because the builtin is handed to Kodi's
    application thread and waits for it to be taken -- which is exactly what
    a thread with a connection to answer for must not do.
    """
    global playlist, stop_requested_at
    playlist = None
    stop_requested_at = time.time()
    Thread(target=lambda: xbmc.executebuiltin('PlayerControl(Stop)'),
           daemon=True).start()

def check_player():
    global player
    log("Starting player thread")
    monitor = xbmc.Monitor()
    ticks = 0
    while not monitor.abortRequested():
        if (not stop_pending() and player and player.owns_playback
                and player.isPlaying()):
            try:
                # Update the current time if it has changed
                if int(player.getTime()) != player.prev_time:
                    player.onPlayBackTimeChanged()
            except Exception:
                # isPlaying() can still be true a moment after playback ends,
                # and getTime() then raises rather than returning.
                pass

        # Volume needs polling too, but once a second is plenty - the
        # position above is what needs the 20Hz.
        ticks += 1
        if ticks % 4 == 0:
            image_viewer.poll()
        if ticks >= 20:
            ticks = 0
            check_volume()
            keep_awake()

        if monitor.waitForAbort(0.05):
            break
    log("Exiting player thread")

# Containers that mean "adaptive streaming manifest" rather than a media file.
# Senders are inconsistent about which spelling they use, so match them all.
HLS_CONTAINERS = frozenset([
    'application/vnd.apple.mpegurl',
    'application/x-mpegurl',
    'application/mpegurl',
    'audio/mpegurl',
    'audio/x-mpegurl',
])
DASH_CONTAINERS = frozenset([
    'application/dash+xml',
    'application/xml+dash',
])

# Still pictures, which take a different path entirely - see image_viewer.
# Mirrors the formats the reference receiver accepts.
IMAGE_CONTAINERS = frozenset([
    'image/apng',
    'image/avif',
    'image/bmp',
    'image/gif',
    'image/x-icon',
    'image/jpeg',
    'image/png',
    'image/svg+xml',
    'image/vnd.microsoft.icon',
    'image/webp',
])
IMAGE_EXTENSIONS = frozenset([
    '.apng', '.avif', '.bmp', '.gif', '.ico', '.jpeg', '.jpg', '.jpe',
    '.jif', '.jfif', '.png', '.svg', '.webp',
])

# What inputstream.adaptive itself expects to see.
ISA_MIME_TYPES = {'hls': 'application/x-mpegURL', 'mpd': 'application/dash+xml'}

def is_image(container: Optional[str], url: str = "") -> bool:
    """Whether this play request is a still picture rather than a stream."""
    normalized = (container or '').split(';')[0].strip().lower()
    if normalized in IMAGE_CONTAINERS:
        return True
    if normalized:
        return False
    return Path(urlparse(url).path).suffix.lower() in IMAGE_EXTENSIONS

def stream_type(container: Optional[str], url: str = "") -> Optional[str]:
    """Classify a play request as 'hls', 'mpd', or None for direct playback.

    Prefer the container the sender declared; fall back to the URL extension
    for senders that omit it.
    """
    normalized = (container or '').split(';')[0].strip().lower()
    if normalized in HLS_CONTAINERS:
        return 'hls'
    if normalized in DASH_CONTAINERS:
        return 'mpd'

    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix == '.m3u8':
        return 'hls'
    if suffix == '.mpd':
        return 'mpd'
    return None

def encode_headers(headers) -> str:
    """Render FCast headers into the urlencoded form Kodi and ISA both expect.

    Senders attach these because the CDN requires them - Grayjay sends the
    Referer and User-Agent that googlevideo URLs are issued against, and the
    request 403s without them.
    """
    if not headers or not isinstance(headers, dict):
        return ''
    return urlencode({str(k): str(v) for k, v in headers.items()})

def apply_inputstream(play_item: xbmcgui.ListItem, kind: str, headers: str) -> None:
    """Route a manifest through inputstream.adaptive."""
    play_item.setContentLookup(False)
    play_item.setMimeType(ISA_MIME_TYPES[kind])
    play_item.setProperty('inputstream', 'inputstream.adaptive')
    # Deprecated on Kodi 21 and removed on Kodi 22, where ISA infers the type
    # from the mime type set above instead. Harmless to keep while we still
    # support Omega; drop it once Kodi 21 is no longer a target.
    play_item.setProperty('inputstream.adaptive.manifest_type', kind)
    play_item.setProperty('inputstream.adaptive.stream_selection_type', 'adaptive')
    if headers:
        play_item.setProperty('inputstream.adaptive.manifest_headers', headers)
        play_item.setProperty('inputstream.adaptive.stream_headers', headers)

def broadcast_playback_state(state: PlayBackState, position: float = 0.0) -> None:
    message = PlayBackUpdateMessage(int(position), state, speed=1.0)
    for session in list(sessions):
        session.send_playback_update(message)

def on_image_closed() -> None:
    """The picture viewer went away, so senders should stop showing it."""
    global image_url
    log("Image viewer closed")
    # The file stays cached: closing the viewer is often followed by the same
    # picture being cast again, and that should not mean downloading it again.
    image_url = None
    broadcast_playback_state(PlayBackState.IDLE)

def image_show_duration() -> float:
    """How long the picture about to be shown should stay up, in seconds.

    Only a picture that came from a playlist gets a countdown. One cast on its
    own stays until a sender stops it or someone closes it from the Kodi UI:
    there is nothing to move on to, and FCast's own receiver treats it the
    same way - it starts the showDuration timer only for playlist items.

    Within a playlist the sender's showDuration wins, since it knows what it
    queued, unless the user has said their own duration should override it.
    A sender that names no duration gets the user's, which is what stops a
    picture holding up the rest of the queue indefinitely.
    """
    if playlist is None:
        return 0.0

    item = playlist.current
    sent = float(item.showDuration or 0.0) if item is not None else 0.0
    if sent > 0 and not settings.image_duration_overrides_sender():
        return sent
    return settings.image_duration()

def on_image_expired() -> None:
    """The picture has been up for its full duration, so the queue moves on."""
    log("Picture duration elapsed")

    # The same event a video sends when it plays out, and for the same reason:
    # senders match the item against their own queue entry.
    item = media_item_from_play_message(get_last_play_data())
    for session in list(sessions):
        session.send_media_event(EventType.MEDIA_ITEM_END, item)

    if advance_playlist():
        return

    # Unlike the video path, Idle goes to every sender here rather than only
    # the ones the event could not reach. The queue that has just run out is
    # ours, so there is none left on the sender for Idle to stand down, and
    # the viewer is about to be closed - anything but Idle would be a lie.
    image_viewer.close()
    broadcast_playback_state(PlayBackState.IDLE)

def cancel_pending_image() -> None:
    """Abandon a picture that is still downloading.

    A download that lands after a video has started would put the picture
    viewer straight back over the top of it.
    """
    global image_request, image_url, image_pending
    image_request = next(image_requests)
    image_url = None
    image_pending = False

def handle_image(message: PlayMessage, headers: str = "") -> None:
    """Put a still picture on screen and report it as playing.

    There is no player to ask about state afterwards, so the Playing update
    goes out here and the matching Idle comes from on_image_closed.
    """
    global current_play_message, image_request, image_url, image_pending

    if already_showing(message.url):
        # Senders re-send the picture that is up, and acting on that would
        # tear the viewer down and build it again for the same photo: a
        # flash, a re-download, and Kodi's window sound, every time.
        log("Already showing this picture, leaving it alone")
        current_play_message = message
        # It still counts as the item being played, so a queue holding the
        # same photo twice running does not come to a halt on it.
        image_viewer.restart_countdown(image_show_duration())
        broadcast_playback_state(PlayBackState.PLAYING)
        broadcast_play_update()
        return

    if player and player.isPlaying():
        xbmc.executebuiltin('PlayerControl(Stop)')

    url = message.url
    if headers:
        # Same convention Kodi uses for media URLs.
        url = f'{url}|{headers}'

    current_play_message = message
    notify('Showing image ...')

    image_request = next(image_requests)
    image_url = message.url
    duration = image_show_duration()

    if settings.preload_images():
        # Off the connection thread: the sender has more to say to us than
        # this, and a photo takes as long as it takes.
        request = image_request
        image_pending = True
        Thread(target=lambda: show_downloaded_image(request, message, url, duration),
               daemon=True).start()
    else:
        image_viewer.show(url, duration=duration)

    broadcast_playback_state(PlayBackState.PLAYING)
    broadcast_play_update()

def already_showing(url: Optional[str]) -> bool:
    """Whether this picture is on screen already, or on its way there.

    Not the same as "we showed it last": once the viewer has been closed the
    same picture has to be shown again, which is what a sender asking for it
    a second time means.
    """
    return bool(url) and url == image_url and (image_viewer.is_showing or image_pending)

def show_downloaded_image(request: int, message: PlayMessage, url: str,
                          duration: float) -> None:
    """Fetch a picture, then show it - leaving the last one up until it lands."""
    global image_pending

    try:
        path = image_cache.fetch(message.url, message.headers)

        if request != image_request:
            # Something else was cast while this was downloading, and it has
            # the screen now. The file stays: it is worth having next time.
            log("Discarding a picture that arrived too late")
            return

        image_viewer.show(path or url, duration=duration)
    except Exception as e:
        # This runs on its own thread, where an escaping exception is only
        # ever seen as a stack trace with nothing to attach it to.
        log(f"Could not show {message.url}: {e}", xbmc.LOGERROR)
    finally:
        if request == image_request:
            image_pending = False

def handle_play(session: FCastSession, message = None):
    log(f"Client request play")

    if not message:
        return

    global playlist, current_play_peer
    # Noted here rather than in play_message, so that the items a playlist
    # goes on to start stay attributed to the sender that handed it over.
    current_play_peer = session.peer if session else None
    if is_playlist(message):
        start_playlist(message)
        return

    # A single item supersedes whatever queue was running.
    playlist = None
    play_message(message)

def start_playlist(message: PlayMessage) -> None:
    """Take over a queue the sender handed us and start at its offset."""
    global playlist

    try:
        content = parse_playlist(message)
    except Exception as e:
        log(f"Could not read playlist: {e}", xbmc.LOGWARNING)
        notify('Could not read playlist', xbmcgui.NOTIFICATION_ERROR)
        return

    if content is None or not content.items:
        notify('Playlist is empty', xbmcgui.NOTIFICATION_WARNING)
        return

    playlist = Playlist(content)
    log(f"Playing playlist of {len(playlist)} items from index {playlist.index}")
    play_message(playlist.play_message())

def advance_playlist() -> bool:
    """Start the next queued item. True if something was started.

    Called when an item finishes. Returning False lets the caller report that
    playback is over.
    """
    global playlist

    if playlist is None:
        return False

    if playlist.advance() is None:
        log("End of playlist")
        playlist = None
        return False

    log(f"Playlist advancing to item {playlist.index}")
    play_message(playlist.play_message())
    return True

def handle_set_playlist_item(session: FCastSession, message: SetPlaylistItemMessage):
    log(f"Client request playlist item {message.itemIndex}")

    if playlist is None:
        return
    if playlist.select(int(message.itemIndex)) is None:
        return
    play_message(playlist.play_message())

def current_item_index() -> Optional[int]:
    """Playlist position for PlaybackUpdate, or None when not playing a queue."""
    return playlist.index if playlist is not None else None

def play_message(message = None):
    play_item: Optional[xbmcgui.ListItem] = None
    url: str = ""

    if not message:
        return

    headers = encode_headers(message.headers)

    # Pictures are not streams and must not reach the video player, which
    # renders them for a few milliseconds and then closes.
    if message.url and is_image(message.container, message.url):
        handle_image(message, headers)
        return

    if message.url:
        url = message.url
        kind = stream_type(message.container, url)

        play_item = xbmcgui.ListItem(path=url)

        if kind:
            log(f'Detected {kind.upper()} stream in URL')
            apply_inputstream(play_item, kind, headers)
        else:
            log('Detected URL')
            if headers:
                # Direct playback has no header property, so Kodi takes them
                # appended to the URL itself.
                url = f'{url}|{headers}'
            if message.container:
                play_item.setContentLookup(False)
                play_item.setMimeType(message.container)
            else:
                play_item.setContentLookup(True)

    elif message.content:
        kind = stream_type(message.container)
        if kind == 'mpd':
            log('Detected DASH stream')

            if http_server:
                http_server.set_content(message.container, message.content)
                url = f'http://{http_server.get_host()}:{http_server.get_port()}/manifest'

                # Basing this off what the YouTube addon does to enable dash
                play_item = xbmcgui.ListItem(path=url)
                apply_inputstream(play_item, kind, headers)
        else:
            notify(f'Unhandled content container {message.container}')

    if player and play_item:
        global current_play_message
        # The picture viewer sits above the video window, so a picture left on
        # screen hides the video entirely rather than being replaced by it.
        cancel_pending_image()
        image_viewer.close()
        notify('Starting player ...')
        play_item.setPath(url)
        start_time = float(message.time) if message.time else 0.0
        current_play_message = message

        def do_play():
            global stop_requested_at
            if player.isPlaying():
                # Marked as ours so the 20Hz poll stays out of the teardown,
                # which is the one thread that must keep running through it.
                stop_requested_at = time.time()
                xbmc.executebuiltin('PlayerControl(Stop)')
                timeout = 50  # 5 seconds max
                while player.isPlaying() and timeout > 0:
                    xbmc.sleep(100)
                    timeout -= 1
            stop_requested_at = 0.0
            player.start_time = start_time
            # From here the callbacks Kodi sends us are about our own cast.
            player.owns_playback = True
            player.play(item=url, listitem=play_item)
            # Every other connected sender needs to know what this one started.
            broadcast_play_update()

        Thread(target=do_play).start()

def do_seek():
    global player, seeks

    # we are only interested in the last consecutive seek, so we skip the first one if there are more than one
    if len(seeks) > 1:
        seeks.pop(0)
    elif len(seeks) > 0:
        # Last seek in the queue, seek to it
        if player:
            player.seekTime(seeks.pop(0))

def handle_seek(session: FCastSession, message = None):
    global player, seeks

    if not message:
        return

    log(f"Client request seek to {message.time}")
    # Send FCastMessage so the client's seek bar position updates better
    session.send_playback_update(PlayBackUpdateMessage(
        message.time,
        PlayBackState.PAUSED if (player and player.is_paused) else PlayBackState.PLAYING,
    ))

    # Append this seek to the seeks "queue"
    seeks.append(float(message.time))
    # Ensure that player.seekTime is called with a low frequency. This prevents Kodi from freezing
    debounce(do_seek, 0.15)()

def handle_stop(session: FCastSession, message = None):
    global player, playlist
    log(f"Client request stop")
    playlist = None
    cancel_pending_image()
    # Ask the viewer first: it reports whether there was a picture to
    # dismiss, so a stale is_showing cannot swallow the request.
    if image_viewer.close():
        broadcast_playback_state(PlayBackState.IDLE)
        return
    if player:
        stop_playback()

def handle_pause(session: FCastPlayer, message = None):
    global player
    log(f"Client request pause")
    # A picture answers this itself, by holding its countdown. Handing it to
    # the player instead would pause whatever Kodi happened to be playing,
    # which while a picture is up is not ours.
    if image_viewer.pause():
        broadcast_playback_state(PlayBackState.PAUSED)
        return
    if player:
        player.doPause()

def handle_resume(session: FCastPlayer, message = None):
    global player
    log(f"Client request resume")
    if image_viewer.resume():
        broadcast_playback_state(PlayBackState.PLAYING)
        return
    if player:
        player.doResume()

def kodi_jsonrpc(method: str, params: Optional[dict] = None):
    """Call Kodi's JSON-RPC. Returns the result, or None if the call failed."""
    request = {"jsonrpc": "2.0", "method": method, "id": 1}
    if params:
        request["params"] = params

    try:
        response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    except Exception as e:
        log(f"JSON-RPC {method} failed: {e}", xbmc.LOGWARNING)
        return None

    if "error" in response:
        log(f"JSON-RPC {method} error: {response['error']}", xbmc.LOGWARNING)
        return None
    return response.get("result")

def get_kodi_volume() -> Optional[float]:
    """Kodi's volume as the 0-1 float FCast uses, or None if unavailable.

    Muted reads as zero: senders have no separate mute concept, so anything
    else would show a volume level while nothing is audible.
    """
    result = kodi_jsonrpc("Application.GetProperties",
                          {"properties": ["volume", "muted"]})
    if not isinstance(result, dict):
        return None
    if result.get("muted"):
        return 0.0
    return result.get("volume", 0) / 100.0

def broadcast_volume_update(volume: float) -> None:
    message = VolumeUpdateMessage(volume)
    for session in list(sessions):
        session.send_volume_update(message)

def keep_awake() -> None:
    """Tell Kodi the box is in use while a picture is on screen.

    Kodi only counts the picture viewer as activity when it is running a
    slideshow of its own: CGUIWindowSlideShow::Process resets the screensaver
    behind `if (m_bSlideShow ...)`, and a single picture put up with
    ShowPicture does not set that. So the idle timer keeps running underneath
    a cast photo, and when it expires the screensaver - or whatever a skin
    does on idle - tries to come to the front, is refused because the picture
    viewer is a modal dialog, and on some skins that refusal is audible.

    Input.ExecuteAction resets the screensaver timer for us
    (CInputOperations::handleScreenSaver). ACTION_NOOP is what it sounds like:
    the window manager hands it to the picture viewer, which ignores it.
    """
    global last_wake

    if not image_viewer.is_showing or not settings.keep_awake_for_pictures():
        return

    now = time.time()
    if now - last_wake < WAKE_INTERVAL:
        return

    last_wake = now
    kodi_jsonrpc("Input.ExecuteAction", {"action": "noop"})

def check_volume() -> None:
    """Publish volume changes. Kodi offers no callback for this, so poll."""
    global last_volume

    volume = get_kodi_volume()
    if volume is None or volume == last_volume:
        return

    last_volume = volume
    broadcast_volume_update(volume)

def handle_volume(session: FCastSession, message: SetVolumeMessage):
    global last_volume
    log(f"Client request set volume at {message.volume}")

    volume = max(0.0, min(1.0, float(message.volume)))
    if kodi_jsonrpc("Application.SetVolume", {"volume": int(round(volume * 100))}) is None:
        return

    # Record it here as well as in the poll, so the change we just made is not
    # then echoed back to every sender as if someone else had made it.
    last_volume = volume

# NOTE: For SetTempo (fine-grained speed) to work, "Sync playback to display"
# must be enabled: Settings -> Player -> Videos -> Sync playback to display

TEMPO_MIN = 0.8
TEMPO_MAX = 1.5

def handle_speed(session: FCastSession, message: SetSpeedMessage):
    global player
    speed = message.speed
    log(f"Client request set speed at {speed}")

    if not (player and player.isPlaying()):
        return

    try:
        result = json.loads(xbmc.executeJSONRPC(json.dumps({
            "jsonrpc": "2.0",
            "method": "Player.GetActivePlayers",
            "id": 1
        })))
        players = result.get("result", [])
        if not players:
            log("No active players found for SetSpeed")
            return
        player_id = players[0]["playerid"]

        clamped = min(max(TEMPO_MIN, speed), TEMPO_MAX)
        response = xbmc.executeJSONRPC(json.dumps({
            "jsonrpc": "2.0",
            "method": "Player.SetTempo",
            "params": {"playerid": player_id, "tempo": clamped},
            "id": 1
        }))
        log(f"Player.SetTempo({clamped}) response: {response}")

    except Exception as e:
        log(f"Error setting speed: {e}")

def configure_keepalive(conn: socket.socket) -> None:
    """Have the kernel give up on a peer that has stopped answering.

    Nothing else will. A sender that changes network -- Wi-Fi to cellular, or
    between access points -- leaves the connection half open: the socket stays
    established, recv() reports only that there is nothing to read, and the
    session, its thread and its place in the broadcast list survive for as long
    as Kodi runs.

    Everything past SO_KEEPALIVE is platform-specific and simply absent from
    the socket module where the platform does not have it, so each is set only
    if it is there. Kodi runs on more than Linux, and an AttributeError here
    would take down the connection at the moment it was accepted.
    """
    try:
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError as e:
        log(f"Could not enable keepalive: {e}", xbmc.LOGWARNING)
        return

    for name, value in (('TCP_KEEPIDLE', KEEPALIVE_IDLE),
                        ('TCP_KEEPINTVL', KEEPALIVE_INTERVAL),
                        ('TCP_KEEPCNT', KEEPALIVE_COUNT),
                        ('TCP_USER_TIMEOUT', USER_TIMEOUT_MS)):
        option = getattr(socket, name, None)
        if option is None:
            continue
        try:
            conn.setsockopt(socket.IPPROTO_TCP, option, value)
        except OSError as e:
            log(f"Could not set {name}: {e}", xbmc.LOGWARNING)

def media_served_by(peer: Optional[str]) -> bool:
    """Whether the stream on screen is coming from this sender's own machine.

    A sender that hands Kodi a public URL leaves nothing behind when it
    disconnects, and playback carries on: a phone going to sleep is not a
    reason to stop the film. A sender serving the media itself is the other
    case entirely. Once it is gone the stream is gone with it, and what is
    left is a player reading an address that will never answer again --
    which is the state this add-on used to sit in indefinitely.

    Matched against the address the connection came from, so only a URL naming
    an address literal can answer this. Naming a host would mean resolving it,
    and a name lookup is not something to do on the way out of a connection,
    so those are left alone.
    """
    if not peer or not current_play_message or not current_play_message.url:
        return False
    return (urlparse(current_play_message.url).hostname or '') == peer

def on_sender_lost(session: FCastSession, peer: str) -> None:
    """Wind up a session whose sender has gone, and let go of its stream."""
    if player:
        player.removeSession(session)
    # Also directly: the player holds this same list, but it is the list that
    # every broadcast walks, and a session left in it when the player happened
    # not to be up would be written to for the rest of the Kodi session.
    if session in sessions:
        sessions.remove(session)
    session.close()

    if any(other.peer == peer and other.is_connected for other in list(sessions)):
        # The same machine is already back on a new connection: it changed
        # network rather than left, and what is playing is still wanted.
        log(f"{peer} is connected again, leaving playback alone")
        return

    if not (player and player.owns_playback and media_served_by(peer)):
        return

    log(f"Stopping playback: {peer} was serving it and is gone", xbmc.LOGINFO)
    notify('Sender disconnected, stopping', xbmcgui.NOTIFICATION_WARNING)
    stop_playback()
    broadcast_playback_state(PlayBackState.IDLE)

# Connection handler thread function
def connection_handler(conn: socket.socket, addr):
    global player, http_server

    monitor = xbmc.Monitor()
    # Above debug level deliberately, along with the disconnect below and the
    # listening line in main(). Everything else this add-on logs is LOGDEBUG,
    # so with Kodi's debug logging off it said nothing whatsoever - including
    # when something had gone wrong. These few lines are what a plain log
    # needs to answer "is it running, and is anything talking to it".
    log("Connection from %s" % addr[0], xbmc.LOGINFO)
    notify("Connection from %s" % addr[0])

    configure_keepalive(conn)
    session = FCastSession(conn, get_play_data=get_current_play_data,
                           peer=addr[0])

    session.on(Event.PLAY, handle_play)
    session.on(Event.STOP, handle_stop)
    session.on(Event.PAUSE, handle_pause)
    session.on(Event.RESUME, handle_resume)
    session.on(Event.SEEK, handle_seek)
    session.on(Event.SET_VOLUME, handle_volume)
    session.on(Event.SET_SPEED, handle_speed)
    session.on(Event.SET_PLAYLIST_ITEM, handle_set_playlist_item)

    # So the sender's volume control starts out in the right place.
    volume = get_kodi_volume()
    if volume is not None:
        session.send_volume_update(VolumeUpdateMessage(volume))

    # Allow Kodi to send playback update packets to this client
    if player:
        player.addSession(session)

    # Receive data from the client and process it. The session's own view of
    # whether it still has a peer ends this too: a socket the kernel has timed
    # out, or output that has backed up because nothing is reading it, are both
    # ways for a sender to be gone that recv() will never report.
    while not monitor.abortRequested() and session.is_connected:
        try:
            buff = conn.recv(FCAST_BUFFER_SIZE)
            if not buff:
                # A zero-length read on a non-blocking socket means the peer
                # closed. Without this the thread spins until Kodi exits, and
                # every reconnect leaks another one.
                log("Client %s disconnected" % addr[0], xbmc.LOGINFO)
                break
            session.process_bytes(buff)
        except BlockingIOError:
            # Normal behavior. Prevents blocking
            pass
        except OSError as e:
            # What the kernel says once it has given up on the peer, and what
            # a reset connection looks like. Neither is a fault of ours.
            log("Connection from %s lost: %s" % (addr[0], e), xbmc.LOGINFO)
            break
        except Exception as e:
            log(str(e), xbmc.LOGERROR)
            break

        # Anything the socket would not take earlier goes out here. Without
        # this, a packet held back by a full socket buffer would wait for the
        # next one to be sent -- which on a session that has gone quiet never
        # comes, and the peer is left with half a packet.
        session.flush()

        if monitor.waitForAbort(0.05):
            break

    on_sender_lost(session, addr[0])
    log("Connection closed from %s" % addr[0], xbmc.LOGINFO)
    notify("Connection closed from %s" % addr[0])

def main():
    global player, sessions, session_threads, player_thread, http_server

    notify("Starting FCast receiver ...")
    # List of active sessions

    # Create a socket for the FCast receiver
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Non-blocking, and left that way. settimeout() put a minute back on it,
    # which accept() would then sit out whenever the selector reported a
    # connection that was gone again by the time we asked for it -- and a
    # connection reset in that gap is exactly what a sender changing network
    # produces. No other sender could connect for the length of that wait.
    s.setblocking(False)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    player = FCastPlayer(sessions, get_play_data=get_last_play_data,
                         on_media_ended=advance_playlist,
                         get_item_index=current_item_index)
    player_thread = Thread(target=check_player)
    player_thread.start()

    # Create HTTP server to stream manifest files
    http_server = FCastHTTPServer()
    http_server.start()

    # Pictures cached by a previous run are worth keeping - the same photos
    # tend to be cast again - but not without a ceiling on them.
    image_cache.prune()

    try:
        s.bind((FCAST_HOST, FCAST_PORT))
        s.listen()
        mdns_register(port=FCAST_PORT, protocol_version=FCAST_VERSION)
    except:
        notify("Bind failed", xbmcgui.NOTIFICATION_ERROR)
        s.close()
        exit()

    # Set up event listener that detects for a new socket connection
    selector = selectors.DefaultSelector()
    selector.register(s, selectors.EVENT_READ, data=None)

    log(f"version {addonversion} listening on port {FCAST_PORT}, "
        f"FCast protocol v{FCAST_VERSION}", xbmc.LOGINFO)
    notify("Server listening on port %d" % FCAST_PORT, timeout=1000)

    monitor = xbmc.Monitor()
    # Loop for new connections
    while not monitor.abortRequested():
        events = selector.select(timeout=0)

        # Check for connections
        for key, mask in events:
            if key.data is None:
                try:
                    conn, addr = s.accept()
                except OSError as e:
                    # The pending connection was gone by the time we asked for
                    # it. Nothing to do but carry on listening.
                    log(f"Accept failed: {e}", xbmc.LOGWARNING)
                    continue
                conn.setblocking(False)
                # Create a new thread for the connection
                t = Thread(target=connection_handler, args=(conn, addr))
                session_threads.append(t)
                t.start()

        # Remove dead threads from sessions list on every timeout or other exception
        session_threads = [t for t in session_threads if t.is_alive()]

        if monitor.waitForAbort(0.250):
            break

    mdns_unregister()
    s.close()

    http_server.stop()

    log("Server stopped", xbmc.LOGINFO)
    notify("Server stopped")
    exit()

if __name__ == '__main__':
    main()
