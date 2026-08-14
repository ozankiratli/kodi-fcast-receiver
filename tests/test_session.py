"""FCast wire protocol: framing, opcode tolerance and version negotiation."""

import json
import random
import socket
import struct
import unittest

from context import fcast_plugin  # noqa: F401  (sets up sys.path)
from fcast_plugin.FCastSession import (
    FCAST_VERSION,
    Event,
    FCastSession,
    OpCode,
    SessionState,
    message_from_json,
)
from fcast_plugin.FCastPackets import PlayMessage


def packet(opcode, body=None):
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    return struct.pack("<IB", len(raw) + 1, opcode) + raw


class SessionHarness:
    """Drives a FCastSession over a socketpair and records emitted events."""

    def __init__(self):
        self.receiver_sock, self.sender_sock = socket.socketpair()
        self.session = FCastSession(self.receiver_sock)
        self.events = []
        for event in Event:
            self.session.on(event, self._record(event))

    def _record(self, event):
        return lambda session, message: self.events.append((event, message))

    def feed(self, data, chunk_size=None):
        if chunk_size is None:
            self.session.process_bytes(data)
            return
        for i in range(0, len(data), chunk_size):
            self.session.process_bytes(data[i:i + chunk_size])

    def sent(self):
        self.sender_sock.setblocking(False)
        try:
            return self.sender_sock.recv(65536)
        except BlockingIOError:
            return b""

    def close(self):
        self.receiver_sock.close()
        self.sender_sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class TestFraming(unittest.TestCase):
    """Packets must survive arriving in arbitrarily sized reads.

    Reading a fixed four bytes for the length prefix regardless of what the
    buffer already held used to drop payload bytes, desynchronising the stream
    and killing the connection. It only ever worked when each packet happened
    to arrive as exactly one read.
    """

    STREAM_MESSAGES = [
        (OpCode.SEEK, {"time": 10}),
        (OpCode.PLAY, {"container": "video/mp4", "url": "https://example/v.mp4",
                       "metadata": {"type": 0, "title": "T" * 300}}),
        (OpCode.SEEK, {"time": 20}),
        (OpCode.PAUSE, None),
        (OpCode.SEEK, {"time": 30}),
    ]
    EXPECTED = [Event.SEEK, Event.PLAY, Event.SEEK, Event.PAUSE, Event.SEEK]

    def setUp(self):
        self.stream = b"".join(packet(op, body) for op, body in self.STREAM_MESSAGES)

    def replay(self, chunks):
        with SessionHarness() as harness:
            for chunk in chunks:
                harness.session.process_bytes(chunk)
            return [event for event, _ in harness.events]

    def test_every_fixed_chunk_size(self):
        for size in range(1, len(self.stream) + 1):
            with self.subTest(chunk_size=size):
                chunks = [self.stream[i:i + size] for i in range(0, len(self.stream), size)]
                self.assertEqual(self.replay(chunks), self.EXPECTED)

    def test_random_splits(self):
        rng = random.Random(1234)
        for run in range(500):
            chunks, pos = [], 0
            while pos < len(self.stream):
                size = rng.randint(1, 40)
                chunks.append(self.stream[pos:pos + size])
                pos += size
            with self.subTest(run=run):
                self.assertEqual(self.replay(chunks), self.EXPECTED)

    def test_oversized_packet_is_rejected(self):
        with SessionHarness() as harness:
            with self.assertRaises(Exception):
                harness.session.process_bytes(struct.pack("<IB", 999999, OpCode.PAUSE))
            self.assertEqual(harness.session.state, SessionState.DISCONNECTED)


class TestOpcodeTolerance(unittest.TestCase):
    """Unknown and unsupported opcodes are dropped, never fatal.

    A v3 sender may put Initial or SubscribeEvent on the wire before it has
    processed our v2 announcement. Tearing the session down over that is what
    made newer senders look broken.
    """

    def test_unsupported_and_unknown_opcodes_keep_session_alive(self):
        with SessionHarness() as harness:
            harness.feed(b"".join([
                packet(OpCode.VERSION, {"version": 3}),
                packet(OpCode.INITIAL, {"displayName": "Pixel", "appName": "Grayjay"}),
                packet(OpCode.SUBSCRIBEEVENT, {"event": {"type": 3, "keys": ["ArrowLeft"]}}),
                packet(OpCode.SETPLAYLISTITEM, {"itemIndex": 2}),
                packet(42, {"from": "a future protocol version"}),
                packet(OpCode.PAUSE),
            ]), chunk_size=7)

            self.assertNotEqual(harness.session.state, SessionState.DISCONNECTED)
            self.assertEqual([event for event, _ in harness.events], [Event.PAUSE])

    def test_malformed_body_does_not_kill_session(self):
        with SessionHarness() as harness:
            body = b"{not json"
            harness.feed(struct.pack("<IB", len(body) + 1, OpCode.SEEK) + body)
            harness.feed(packet(OpCode.PAUSE))

            self.assertNotEqual(harness.session.state, SessionState.DISCONNECTED)
            self.assertEqual([event for event, _ in harness.events], [Event.PAUSE])

    def test_ping_is_answered_with_pong(self):
        with SessionHarness() as harness:
            harness.sent()  # discard the Version we announce on connect
            harness.feed(packet(OpCode.PING))

            reply = harness.sent()
            self.assertEqual(struct.unpack("<IB", reply[:5]), (1, OpCode.PONG))


class TestVersionNegotiation(unittest.TestCase):

    def test_version_announced_once_on_connect(self):
        with SessionHarness() as harness:
            sent = harness.sent()
            size, opcode = struct.unpack("<IB", sent[:5])
            self.assertEqual(opcode, OpCode.VERSION)
            self.assertEqual(json.loads(sent[5:4 + size]), {"version": FCAST_VERSION})
            self.assertEqual(len(sent), 4 + size)

    def test_no_echo_when_sender_announces(self):
        with SessionHarness() as harness:
            harness.sent()
            harness.feed(packet(OpCode.VERSION, {"version": 3}))
            self.assertEqual(harness.sent(), b"")

    def test_session_downgrades_to_our_version(self):
        with SessionHarness() as harness:
            harness.feed(packet(OpCode.VERSION, {"version": 3}))
            self.assertEqual(harness.session.peer_version, 3)
            self.assertEqual(harness.session.protocol_version, FCAST_VERSION)

    def test_session_follows_an_older_sender_down(self):
        with SessionHarness() as harness:
            harness.feed(packet(OpCode.VERSION, {"version": 1}))
            self.assertEqual(harness.session.protocol_version, 1)


class TestMessageFromJson(unittest.TestCase):

    def test_unknown_fields_are_dropped(self):
        message = message_from_json(PlayMessage, json.dumps({
            "container": "video/mp4",
            "url": "https://example/v.mp4",
            "volume": 0.8,
            "metadata": {"type": 0, "title": "Test"},
            "someFutureField": True,
        }).encode())

        self.assertEqual(message.url, "https://example/v.mp4")
        self.assertEqual(message.volume, 0.8)
        self.assertFalse(hasattr(message, "someFutureField"))

    def test_non_object_body_is_rejected(self):
        with self.assertRaises(ValueError):
            message_from_json(PlayMessage, b"[1, 2, 3]")


if __name__ == "__main__":
    unittest.main()
