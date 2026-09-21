from enum import Enum
import inspect
import json
import socket
import struct
import time
from typing import Any, Callable, Dict, List, Optional

import xbmc

from .FCastPackets import *
from .util import log, addonname, addonversion

class SessionState(int, Enum):
    IDLE = 0
    WAITING_FOR_LENGTH = 1
    WAITING_FOR_DATA = 2
    DISCONNECTED = 3

class OpCode(int, Enum):
    NONE = 0
    PLAY = 1
    PAUSE = 2
    RESUME = 3
    STOP = 4
    SEEK = 5
    PLAYBACK_UPDATE = 6
    VOLUME_UPDATE = 7
    SET_VOLUME = 8
    PLAYBACK_ERROR = 9
    SET_SPEED = 10
    VERSION = 11
    PING = 12
    PONG = 13
    INITIAL = 14
    PLAYUPDATE=15
    SETPLAYLISTITEM = 16
    SUBSCRIBEEVENT = 17
    UNSUBSCRIBEEVENT = 18
    EVENT = 19

class Event(str, Enum):
    PLAY = "play"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    SEEK = "seek"
    SET_VOLUME = "set_volume"
    SET_SPEED = "set_speed"
    SET_PLAYLIST_ITEM = "set_playlist_item"

LENGTH_BYTES = 4
MAXIMUM_PACKET_LENGTH = 32000

# How long output may sit with a socket that will not take it before the peer
# is written off. A sender that walks off the network acknowledges nothing
# further, so the socket stops accepting writes while never reporting an error,
# and at twenty position updates a second there is always something waiting to
# be written. This is what turns that silence into a decision, on any platform
# and whatever size the packets happen to be.
#
# Well past any hiccup: the control channel carries under two kilobytes a
# second, so a peer that has absorbed none of it for this long is not a slow
# peer, it is an absent one. On Linux the socket itself reaches the same
# conclusion sooner -- see TCP_USER_TIMEOUT where the connection is accepted.
SEND_STALL_TIMEOUT = 30.0

# Highest protocol version this receiver implements. v3 is required rather
# than optional: senders drive their own queue from the MediaItemEnd event,
# and events only exist from v3, so a v2 receiver can never tell a sender that
# an item finished in a way it will act on.
#
# Not yet implemented from v3: key events, and the forwardCache/backwardCache
# preloading hints on playlists.
FCAST_VERSION = 3


def message_from_fields(message_class, fields: dict):
    """Build a message object, dropping fields this receiver does not know.

    Senders on a newer protocol version add fields freely - v3 added `volume`
    and `metadata` to PlayMessage alone. Passing those straight into the
    constructor raises TypeError, which used to kill the whole session.
    """
    accepted = inspect.signature(message_class).parameters
    unknown = sorted(set(fields) - set(accepted))
    if unknown:
        log(f"Ignoring unknown {message_class.__name__} fields: {', '.join(unknown)}")

    return message_class(**{k: v for k, v in fields.items() if k in accepted})


def message_from_json(message_class, body: bytes):
    """As message_from_fields, from a JSON object body."""
    fields = json.loads(body)
    if not isinstance(fields, dict):
        raise ValueError(f"expected a JSON object, got {type(fields).__name__}")

    return message_from_fields(message_class, fields)

class FCastSession:

    buffer: bytes = bytes()
    packet_length: int = 0
    client: Optional[socket.socket] = None
    state: SessionState = SessionState.DISCONNECTED
    # The address this connection came from. Used to decide whether a sender
    # that has gone away was also the one serving what is on screen.
    peer: str = ''
    # Output the socket has not taken yet, and when any of it last moved.
    # See flush().
    outbox: bytearray
    last_progress_at: float = 0.0
    # What the sender announced, and what the two of us settled on. Until a
    # Version message arrives, assume the oldest version that has one.
    peer_version: int = 1
    protocol_version: int = 1

    # Whether the v3 Initial handshake has been completed for this session.
    sent_initial: bool = False
    # EventType values this sender asked to be told about.
    subscribed_events: set

    __listeners: Dict[str, List[Callable[[Any, Any], Any]]] = {}

    def __init__(self, client: socket.socket, get_play_data=None, peer: str = ''):
        self.__listeners = {}
        self.client = client
        self.peer = peer
        self.outbox = bytearray()
        self.last_progress_at = time.time()
        self.state = SessionState.WAITING_FOR_LENGTH
        self.sent_initial = False
        self.subscribed_events = set()
        # Lets a newly connected sender learn what is already playing, without
        # this module needing to know how playback state is tracked.
        self.__get_play_data = get_play_data
        #send initial version message
        self.__send(OpCode.VERSION, VersionMessage(version=FCAST_VERSION))


    def close(self):
        if self.client:
            try:
                self.client.close()
            except OSError:
                # Already gone. Nothing here is worth failing a teardown for.
                pass
        self.client = None
        self.outbox = bytearray()
        self.state = SessionState.DISCONNECTED

    @property
    def is_connected(self) -> bool:
        """Whether this session still has a socket worth talking to.

        The thread reading the connection watches this, so anything that gives
        up on the peer -- here or in flush() -- ends that thread too.
        """
        return self.client is not None

    def disconnect(self, reason: str) -> None:
        """Give up on this session, saying why."""
        log(f"Session with {self.peer or 'sender'} ended: {reason}", xbmc.LOGINFO)
        self.close()

    def send_playback_update(self, value: PlayBackUpdateMessage):
        self.__send(OpCode.PLAYBACK_UPDATE, value)

    def send_volume_update(self, value: VolumeUpdateMessage):
        self.__send(OpCode.VOLUME_UPDATE, value)

    def send_playback_error(self, value: PlaybackErrorMessage):
        self.__send(OpCode.PLAYBACK_ERROR, value)

    def is_subscribed_to(self, event_type: int) -> bool:
        return event_type in self.subscribed_events

    def send_media_event(self, event_type: int, item: Optional[MediaItem]) -> bool:
        """Fire a media event at this sender. Returns True if it was sent.

        Only subscribers get events, per the protocol. Senders act on these:
        Grayjay advances its own queue when it receives MediaItemEnd, which is
        how the next video starts playing.
        """
        if self.protocol_version < 3 or not self.is_subscribed_to(event_type):
            return False

        self.__send(OpCode.EVENT, EventMessage(MediaItemEvent(event_type, item)))
        return True

    def send_play_update(self, play_data: Optional[PlayMessage]):
        """Tell this sender what is now playing, so multiple senders stay in sync."""
        if self.protocol_version < 3:
            return
        self.__send(OpCode.PLAYUPDATE, PlayUpdateMessage(
            playData=summarize_play_message(play_data)
        ))

    def sendOpCode(self,opcode:OpCode):
        self.__send(opcode)

    def __send(self, opcode: OpCode, message = None):

        def default(o):
            # Drop unset fields rather than sending explicit nulls. The
            # protocol treats absent and null alike, the reference receiver
            # omits them, and it keeps big messages under the 32KB ceiling.
            return {k: v for k, v in o.__dict__.items() if v is not None}

        if not self.client:
            return

        # Measured on the encoded bytes rather than on the string they came
        # from. The two agree today, because json.dumps escapes everything
        # outside ASCII, so this fixes nothing on its own -- it removes the
        # dependency. A header that understates its body by a single byte
        # desynchronizes the sender, and that is too much to rest on a default
        # argument somewhere else.
        body = json.dumps(message, default=default).encode("utf-8") if message else b""
        # The length covers the opcode byte as well as the body.
        packet = struct.pack("<IB", len(body) + 1, opcode.value) + body

        if not self.outbox:
            # Output starts waiting now, so the stall clock runs from here
            # rather than from whenever this session last had something to say.
            self.last_progress_at = time.time()
        self.outbox += packet
        self.flush()

    def flush(self) -> None:
        """Write as much of the pending output as the socket will accept.

        send() on a non-blocking socket writes what fits and reports how much
        that was, which is not always the whole packet. Keeping the remainder
        for the next call is what holds the sender's stream in frame: a
        truncated packet desynchronizes it exactly the way a truncated read
        desynchronized us before the reassembly fix. Nothing used to keep it,
        and nothing noticed, because the socket only fills when the peer stops
        reading -- which is the case this is about.

        Nothing fits at all once the peer has stopped acknowledging anything,
        which is what a sender that changed network looks like from here. So
        output that has not moved for SEND_STALL_TIMEOUT is the answer to "is
        this peer still there", on platforms where the socket itself will not
        say so for hours.
        """
        if not self.client or not self.outbox:
            return

        try:
            while self.outbox:
                sent = self.client.send(self.outbox)
                if not sent:
                    break
                del self.outbox[:sent]
                self.last_progress_at = time.time()
        except (BlockingIOError, InterruptedError):
            # The socket will take no more for now. What is left goes out on a
            # later call; this is not an error and must not end the session.
            pass
        except OSError as e:
            self.disconnect(f"send failed: {e}")
            return

        if self.outbox and time.time() - self.last_progress_at > SEND_STALL_TIMEOUT:
            self.disconnect(
                f"{len(self.outbox)} bytes unsent for "
                f"{SEND_STALL_TIMEOUT:.0f}s, peer is not reading")

    def process_bytes(self, received_bytes: bytes):
        if not received_bytes or len(received_bytes) <= 0:
            return
        
        if self.state == SessionState.WAITING_FOR_LENGTH:
            self.__handle_length_bytes(received_bytes)
        elif self.state == SessionState.WAITING_FOR_DATA:
            self.__handle_packet_bytes(received_bytes)
        else:
            raise Exception("Data received is unhandled in current session state %s" % self.state)
        
    def __handle_length_bytes(self, received_bytes: bytes):
        # Take only what the length field still needs. The buffer may already
        # hold part of it from a previous chunk, and reading a full four bytes
        # regardless would swallow packet data that follows it.
        bytes_to_read = min(LENGTH_BYTES - len(self.buffer), len(received_bytes))
        bytes_remaining = len(received_bytes) - bytes_to_read

        self.buffer += received_bytes[:bytes_to_read]

        if len(self.buffer) >= LENGTH_BYTES:
            self.state = SessionState.WAITING_FOR_DATA
            self.packet_length = struct.unpack("<I", self.buffer[:4])[0]
            self.buffer = bytes()

            if self.packet_length > MAXIMUM_PACKET_LENGTH:
                self.close()
                raise Exception("Packet length %d exceeds maximum packet length %d" % (self.packet_length, MAXIMUM_PACKET_LENGTH))
            
            if bytes_remaining > 0:
                self.__handle_packet_bytes(received_bytes[bytes_to_read:])

    def __handle_packet_bytes(self, received_bytes: bytes):
        # Same as above: only take the bytes this packet is still missing, so
        # a packet that arrives split across reads does not eat into the next.
        bytes_to_read = min(self.packet_length - len(self.buffer), len(received_bytes))
        bytes_remaining = len(received_bytes) - bytes_to_read

        self.buffer += received_bytes[:bytes_to_read]

        # Packet fully received
        if len(self.buffer) >= self.packet_length:

            self.__handle_packet()

            self.state = SessionState.WAITING_FOR_LENGTH
            self.packet_length = 0
            self.buffer = bytes()

            # If there are more bytes to read, treat them as a new packet
            if bytes_remaining > 0:
                self.__handle_length_bytes(received_bytes[bytes_to_read:])

    def on(self, event: Event, callback: Callable[[Any, Any], Any]):
        if event not in self.__listeners:
            self.__listeners[event] = []
        self.__listeners[event].append(callback)

    def __emit(self, event: str, body = None):
        if event in self.__listeners:
            for listener in self.__listeners[event]:
                listener(self, body)

    def __handle_packet(self):

        raw_opcode = struct.unpack("<B", self.buffer[:1])[0]
        body = self.buffer[1:] if len(self.buffer) > 1 else None

        try:
            opcode = OpCode(raw_opcode)
        except ValueError:
            # Opcodes are added with every protocol revision. An unrecognised
            # one means the sender is newer than us, not that the stream is
            # corrupt, so skip the packet and keep the session up.
            log(f"Ignoring packet with unknown opcode {raw_opcode}")
            return

        try:
            self.__dispatch(opcode, body)
        except Exception as e:
            # One bad packet must not tear down a working connection.
            log(f"Error handling {opcode.name} packet: {e}", xbmc.LOGWARNING)

    def __dispatch(self, opcode: OpCode, body: Optional[bytes]):

        if opcode == OpCode.PLAY:
            self.__emit(Event.PLAY, message_from_json(PlayMessage, body) if body else None)
        elif opcode == OpCode.PAUSE:
            self.__emit(Event.PAUSE)
        elif opcode == OpCode.RESUME:
            self.__emit(Event.RESUME)
        elif opcode == OpCode.STOP:
            self.__emit(Event.STOP)
        elif opcode == OpCode.SEEK:
            self.__emit(Event.SEEK, message_from_json(SeekMessage, body) if body else None)
        elif opcode == OpCode.SET_VOLUME:
            self.__emit(Event.SET_VOLUME, message_from_json(SetVolumeMessage, body) if body else None)
        elif opcode == OpCode.SET_SPEED:
            self.__emit(Event.SET_SPEED, message_from_json(SetSpeedMessage, body) if body else None)
        elif opcode == OpCode.PING:
            self.__send(OpCode.PONG)
        elif opcode == OpCode.SETPLAYLISTITEM:
            self.__emit(Event.SET_PLAYLIST_ITEM,
                        message_from_json(SetPlaylistItemMessage, body) if body else None)
        elif opcode == OpCode.VERSION:
            self.__handle_version(body)
        elif opcode == OpCode.INITIAL:
            sender = message_from_json(InitialSenderMessage, body) if body else None
            if sender:
                log(
                    f"Sender identified as {sender.displayName or '?'} "
                    f"({sender.appName or '?'} {sender.appVersion or '?'})"
                )
        elif opcode == OpCode.SUBSCRIBEEVENT:
            self.__update_subscription(body, subscribe=True)
        elif opcode == OpCode.UNSUBSCRIBEEVENT:
            self.__update_subscription(body, subscribe=False)
        else:
            # Everything else is either a receiver-to-sender message we should
            # never receive, or a v3 feature (Initial, playlists, event
            # subscription) we do not implement. Both are safe to drop.
            log(f"Ignoring {opcode.name} packet, unsupported at protocol v{FCAST_VERSION}")

    def __update_subscription(self, body: Optional[bytes], subscribe: bool):
        if not body:
            return

        message = message_from_json(SubscribeEventMessage, body)
        event_type = (message.event or {}).get("type")
        if event_type is None:
            return

        if subscribe:
            self.subscribed_events.add(event_type)
        else:
            self.subscribed_events.discard(event_type)

        try:
            name = EventType(event_type).name
        except ValueError:
            name = f"type {event_type}"
        log(f"Sender {'subscribed to' if subscribe else 'unsubscribed from'} {name}")

    def __handle_version(self, body: Optional[bytes]):
        if not body:
            return

        message = message_from_json(VersionMessage, body)
        self.peer_version = int(message.version)
        self.protocol_version = min(self.peer_version, FCAST_VERSION)
        # No reply here: both parties announce once on connect, which we
        # already did from __init__. Echoing a second Version message confuses
        # senders that treat it as a renegotiation.
        log(
            f"Client speaks protocol v{self.peer_version}, "
            f"session negotiated to v{self.protocol_version}"
        )

        # v3 onwards, both parties follow the version exchange with an Initial
        # message carrying their identity and current state.
        if self.protocol_version >= 3 and not self.sent_initial:
            self.sent_initial = True
            play_data = self.__get_play_data() if self.__get_play_data else None
            self.__send(OpCode.INITIAL, InitialReceiverMessage(
                displayName=f"Kodi - {socket.gethostname()}",
                appName=addonname,
                appVersion=addonversion,
                playData=summarize_play_message(play_data),
            ))
