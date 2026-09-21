"""What happens to a session when the sender stops being there.

A sender that changes network - Wi-Fi to cellular, or between access points -
does not close its connection, because it is not there to close it. No FIN, no
RST: the socket stays established, recv() reports only that there is nothing to
read, and the write side quietly stops being acknowledged. Every case here is
that one, at the point where the add-on has to notice it without being told.
"""

import json
import socket
import time
import unittest
from unittest import mock

from context import fcast_plugin  # noqa: F401  (sets up sys.path)
from fcast_plugin import main
from fcast_plugin import FCastSession as session_module
from fcast_plugin.FCastSession import (
    SEND_STALL_TIMEOUT,
    FCastSession,
    OpCode,
)
from fcast_plugin.FCastPackets import (
    PlayBackState,
    PlayBackUpdateMessage,
    PlayMessage,
    VolumeUpdateMessage,
)
from test_session import decode_packets

import xbmc
import xbmcgui


class ChokedSocket:
    """A socket that takes only so much at a time, or nothing at all.

    What a real one does once its peer stops reading: send() takes what fits
    and reports how much that was, rather than taking all of it or raising.
    """

    def __init__(self, chunk=None, budget=None):
        self.received = bytearray()
        # Bytes accepted per send. None means "all of it".
        self.chunk = chunk
        # Bytes it will take in total before it is full for good. None means
        # no limit. This is the difference between a peer that is slow and a
        # peer that has stopped: the first keeps taking, the second stops.
        self.budget = budget
        # Sends that report the socket as momentarily full before any go out.
        self.blocked = 0
        self.closed = False

    def send(self, data):
        if self.closed:
            raise OSError("socket is closed")
        if self.blocked > 0:
            self.blocked -= 1
            raise BlockingIOError(11, "Resource temporarily unavailable")

        taken = len(data) if self.chunk is None else min(self.chunk, len(data))
        if self.budget is not None:
            taken = min(taken, self.budget)
            self.budget -= taken
        if taken <= 0:
            raise BlockingIOError(11, "Resource temporarily unavailable")

        self.received += bytes(data[:taken])
        return taken

    def close(self):
        self.closed = True


def playback_update():
    return PlayBackUpdateMessage(1, PlayBackState.PLAYING)


class TestSendingToASlowPeer(unittest.TestCase):
    """A socket that will not take a whole packet must not lose the rest."""

    def test_a_packet_the_socket_only_part_takes_is_finished_later(self):
        # Seven bytes a time: every packet this session sends spans several
        # calls, which is what a nearly full socket buffer produces.
        sock = ChokedSocket(chunk=7)
        session = FCastSession(sock, peer="192.168.1.9")
        session.send_volume_update(VolumeUpdateMessage(0.5))

        for _ in range(200):
            session.flush()
            if not session.outbox:
                break

        self.assertEqual(session.outbox, bytearray())
        # Truncate any packet and this raises or returns nonsense: the length
        # prefix of one packet would be read out of the body of another.
        opcodes = [opcode for opcode, _ in decode_packets(bytes(sock.received))]
        self.assertEqual(opcodes, [OpCode.VERSION, OpCode.VOLUME_UPDATE])

    def test_a_socket_that_is_briefly_full_does_not_end_the_session(self):
        sock = ChokedSocket()
        session = FCastSession(sock, peer="192.168.1.9")
        sock.blocked = 2

        session.send_volume_update(VolumeUpdateMessage(0.5))
        self.assertTrue(session.is_connected)

        for _ in range(5):
            session.flush()

        self.assertTrue(session.is_connected)
        opcodes = [opcode for opcode, _ in decode_packets(bytes(sock.received))]
        self.assertIn(OpCode.VOLUME_UPDATE, opcodes)

    def test_a_peer_that_takes_nothing_at_all_is_written_off(self):
        # The state a sender leaves behind when it changes network: the socket
        # is fine, and nothing will ever be acknowledged again.
        sock = ChokedSocket(chunk=0)
        session = FCastSession(sock, peer="192.168.1.9")
        session.send_playback_update(playback_update())
        self.assertTrue(session.is_connected)

        # Wind the clock rather than wait out the timeout: what is under test
        # is the decision, not Python's ability to measure half a minute.
        session.last_progress_at = time.time() - SEND_STALL_TIMEOUT - 1
        session.flush()

        self.assertFalse(session.is_connected)
        self.assertTrue(sock.closed)

    def test_a_peer_that_is_taking_bytes_however_slowly_is_kept(self):
        # The clock is about output that is not moving, not about output that
        # is taking a while. A sender on a weak link takes what it can and
        # leaves the rest waiting, and is entitled to stay for doing so.
        sock = ChokedSocket(chunk=7, budget=7)
        session = FCastSession(sock, peer="192.168.1.9")
        session.send_playback_update(playback_update())
        self.assertTrue(session.outbox, "the socket should still be backed up")
        session.last_progress_at = time.time() - SEND_STALL_TIMEOUT - 1

        # Seven more bytes go out, and nothing else will. That is progress,
        # and it buys the peer the whole grace period again.
        sock.budget = 7
        session.flush()

        self.assertTrue(session.outbox, "the socket should still be backed up")
        self.assertTrue(session.is_connected)
        self.assertFalse(sock.closed)

    def test_a_session_that_was_quiet_for_a_while_gets_its_full_grace(self):
        # The clock runs from when output started waiting, not from whenever
        # this session last had something to say. Otherwise a sender that had
        # been idle for a minute would be written off by its next packet.
        sock = ChokedSocket(chunk=0)
        session = FCastSession(sock, peer="192.168.1.9")
        session.flush()
        session.outbox = bytearray()
        session.last_progress_at = time.time() - 600

        session.send_playback_update(playback_update())

        self.assertTrue(session.is_connected)


class TestKeepalive(unittest.TestCase):
    """The connection is the only thing that can report a sender gone."""

    def test_keepalive_is_enabled_on_the_connection(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(sock.close)

        main.configure_keepalive(sock)

        self.assertTrue(sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE))

    def test_the_timings_are_set_where_the_platform_has_them(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(sock.close)

        main.configure_keepalive(sock)

        for name, expected in (('TCP_KEEPIDLE', main.KEEPALIVE_IDLE),
                               ('TCP_KEEPINTVL', main.KEEPALIVE_INTERVAL),
                               ('TCP_KEEPCNT', main.KEEPALIVE_COUNT),
                               ('TCP_USER_TIMEOUT', main.USER_TIMEOUT_MS)):
            option = getattr(socket, name, None)
            if option is None:
                continue
            with self.subTest(option=name):
                self.assertEqual(
                    sock.getsockopt(socket.IPPROTO_TCP, option), expected)

    def test_an_unsupported_option_does_not_take_the_connection_down(self):
        class Refusing:
            def setsockopt(self, *args):
                raise OSError("not supported here")

        # Kodi runs on platforms that have none of this. Accepting the
        # connection must still succeed there.
        main.configure_keepalive(Refusing())


class FakePlayer:
    start_time = 0.0

    def __init__(self, sessions, owns_playback=True):
        self.owns_playback = owns_playback
        # The real player is handed main's list, not a copy of it.
        self.sessions = sessions
        self.played = []

    def play(self, item=None, listitem=None):
        self.played.append(item)

    def addSession(self, session):
        self.sessions.append(session)

    def removeSession(self, session):
        if session in self.sessions:
            self.sessions.remove(session)

    def isPlaying(self):
        return self.owns_playback


class FakeSession:
    def __init__(self, peer, connected=True):
        self.peer = peer
        self.protocol_version = 3
        self.playback_updates = []
        self.play_updates = []
        self._connected = connected

    @property
    def is_connected(self):
        return self._connected

    def close(self):
        self._connected = False

    def send_playback_update(self, message):
        self.playback_updates.append(message)

    def send_play_update(self, play_data):
        self.play_updates.append(play_data)


def wait_for_builtin(command, timeout=2.0):
    """stop_playback hands the builtin to a thread, so give it a moment."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if command in xbmc.builtins_called:
            return True
        time.sleep(0.01)
    return False


class SenderLossTestCase(unittest.TestCase):

    def setUp(self):
        xbmc.builtins_called.clear()
        xbmcgui.notifications.clear()
        xbmcgui.current_window_id = 10000
        xbmcgui.current_dialog_id = 9999
        main.sessions.clear()
        self.player = FakePlayer(main.sessions)
        self.previous_player, main.player = main.player, self.player
        main.playlist = None
        main.stop_requested_at = 0.0
        main.current_play_message = None
        main.current_play_peer = None

    def tearDown(self):
        main.sessions.clear()
        main.player = self.previous_player
        main.playlist = None
        main.stop_requested_at = 0.0
        main.current_play_message = None
        main.current_play_peer = None

    def casting(self, url, peer="192.168.1.9"):
        main.current_play_message = PlayMessage(container="video/mp4", url=url)
        main.current_play_peer = peer


class TestWhoIsServingTheStream(SenderLossTestCase):
    """Only a sender serving the media itself takes the stream with it."""

    def test_a_url_on_the_senders_own_machine_is_served_by_it(self):
        self.casting("http://192.168.1.9:8080/local/film.mp4")
        self.assertTrue(main.media_served_by("192.168.1.9"))

    def test_a_url_elsewhere_on_the_network_is_not(self):
        self.casting("http://192.168.1.50:8080/film.mp4")
        self.assertFalse(main.media_served_by("192.168.1.9"))

    def test_a_public_url_is_not(self):
        self.casting("https://rr3---sn-example.googlevideo.com/videoplayback?id=1")
        self.assertFalse(main.media_served_by("192.168.1.9"))

    def test_a_url_naming_a_host_is_left_alone(self):
        # Answering would mean a name lookup, which is not something to do on
        # the way out of a connection.
        self.casting("http://sender.local:8080/film.mp4")
        self.assertFalse(main.media_served_by("192.168.1.9"))

    def test_nothing_playing_is_served_by_nobody(self):
        self.assertFalse(main.media_served_by("192.168.1.9"))
        self.casting(None)
        self.assertFalse(main.media_served_by("192.168.1.9"))
        self.casting("http://192.168.1.9:8080/film.mp4")
        self.assertFalse(main.media_served_by(None))


class TestAttributingWhatIsPlaying(SenderLossTestCase):
    """Which sender asked for what is on screen is the whole question later."""

    def test_the_sender_that_asked_is_remembered(self):
        session = FakeSession("192.168.1.9")
        main.sessions.append(session)
        self.player.owns_playback = False

        main.handle_play(session, PlayMessage(
            container="video/mp4", url="http://192.168.1.9:8080/film.mp4"))

        self.assertEqual(main.current_play_peer, "192.168.1.9")

    def test_a_queue_stays_with_the_sender_that_handed_it_over(self):
        # advance_playlist starts later items without a session of its own, so
        # attribution has to survive the handover or every item after the
        # first belongs to nobody.
        session = FakeSession("192.168.1.9")
        main.sessions.append(session)
        self.player.owns_playback = False
        items = [{"container": "video/mp4", "url": "http://192.168.1.9:8080/a.mp4"},
                 {"container": "video/mp4", "url": "http://192.168.1.9:8080/b.mp4"}]
        main.handle_play(session, PlayMessage(
            container="application/json",
            content=json.dumps({"contentType": 0, "items": items})))

        self.assertTrue(main.advance_playlist())

        self.assertEqual(main.current_play_peer, "192.168.1.9")


class TestLosingTheSender(SenderLossTestCase):

    def test_the_stream_is_stopped_when_its_source_has_gone(self):
        session = FakeSession("192.168.1.9")
        main.sessions.append(session)
        self.casting("http://192.168.1.9:8080/film.mp4")

        main.on_sender_lost(session, "192.168.1.9")

        self.assertTrue(wait_for_builtin('PlayerControl(Stop)'))
        self.assertNotIn(session, main.sessions)

    def test_a_lost_session_leaves_the_broadcast_list_with_no_player_up(self):
        # Every broadcast walks this list. A session left in it is written to
        # for the rest of the Kodi session, whether or not a player exists to
        # have taken it out.
        session = FakeSession("192.168.1.9")
        main.sessions.append(session)
        main.player = None

        main.on_sender_lost(session, "192.168.1.9")

        self.assertEqual(main.sessions, [])

    def test_playback_from_elsewhere_carries_on(self):
        # A phone that goes to sleep having cast a public URL is not a reason
        # to stop the film.
        session = FakeSession("192.168.1.9")
        main.sessions.append(session)
        self.casting("https://cdn.example/film.mp4")

        main.on_sender_lost(session, "192.168.1.9")

        self.assertFalse(wait_for_builtin('PlayerControl(Stop)', timeout=0.3))

    def test_a_sender_already_back_on_a_new_connection_keeps_its_stream(self):
        # Changing network means the old connection dies and a new one opens,
        # often in that order. Acting on the death would stop what the sender
        # has just asked to carry on with.
        gone = FakeSession("192.168.1.9")
        returned = FakeSession("192.168.1.9")
        main.sessions.extend([gone, returned])
        self.casting("http://192.168.1.9:8080/film.mp4")

        main.on_sender_lost(gone, "192.168.1.9")

        self.assertFalse(wait_for_builtin('PlayerControl(Stop)', timeout=0.3))
        self.assertIn(returned, main.sessions)

    def test_playback_that_is_not_ours_is_left_alone(self):
        session = FakeSession("192.168.1.9")
        main.sessions.append(session)
        self.player.owns_playback = False
        self.casting("http://192.168.1.9:8080/film.mp4")

        main.on_sender_lost(session, "192.168.1.9")

        self.assertFalse(wait_for_builtin('PlayerControl(Stop)', timeout=0.3))

    def test_the_queue_the_sender_handed_over_goes_with_it(self):
        session = FakeSession("192.168.1.9")
        main.sessions.append(session)
        self.casting("http://192.168.1.9:8080/film.mp4")
        main.playlist = object()

        main.on_sender_lost(session, "192.168.1.9")

        self.assertIsNone(main.playlist)


class VanishedPeer(ChokedSocket):
    """A connection to a sender that is no longer at the other end.

    Established, readable without ever having anything to read, and writable
    without anything being taken: the state a sender leaves behind when it
    changes network. recv() never raises, so nothing in the read loop learns
    anything from it.
    """

    def __init__(self):
        super().__init__(chunk=0)

    def setsockopt(self, *args):
        pass

    def recv(self, size):
        raise BlockingIOError(11, "Resource temporarily unavailable")


class LoopingMonitor:
    """A Monitor the read loop can actually go round, with a backstop.

    The backstop exists so that a handler which fails to let go shows up as a
    failed assertion instead of a test that never returns.
    """

    def __init__(self, limit=40, step=0.05):
        self.limit = limit
        self.step = step
        self.waits = 0

    def abortRequested(self):
        return self.waits >= self.limit

    def waitForAbort(self, timeout=0.0):
        self.waits += 1
        time.sleep(self.step)
        return self.waits >= self.limit


class TestTheHandlerLettingGo(SenderLossTestCase):
    """The thread reading a connection has to end when the sender does.

    Nothing arrives on a half open connection, so the read loop learns nothing
    from recv() and used to spin for the rest of the Kodi session -- one more
    leaked thread, and one more dead session being broadcast to, for every
    time a sender changed network.
    """

    def test_the_read_loop_ends_when_the_session_writes_the_peer_off(self):
        conn = VanishedPeer()
        monitor = LoopingMonitor()

        with mock.patch.object(xbmc, "Monitor", lambda: monitor), \
             mock.patch.object(session_module, "SEND_STALL_TIMEOUT", 0.2):
            main.connection_handler(conn, ("192.168.1.9", 51000))

        self.assertLess(monitor.waits, 20,
                        "the handler ran to the backstop instead of letting go")
        self.assertEqual(main.sessions, [])
        self.assertTrue(conn.closed)


class TestStayingOutOfATeardown(SenderLossTestCase):
    """A player asked to stop is not worth polling until it has."""

    def test_nothing_is_pending_before_a_stop_is_asked_for(self):
        self.assertFalse(main.stop_pending())

    def test_a_stop_just_asked_for_is_pending(self):
        main.stop_playback()
        self.assertTrue(main.stop_pending())

    def test_kodi_reporting_the_stop_ends_the_wait(self):
        main.stop_playback()
        # What onPlayBackStopped does, and what anything still reachable does
        # within milliseconds.
        self.player.owns_playback = False
        self.assertFalse(main.stop_pending())

    def test_a_stop_that_is_never_reported_does_not_wait_for_ever(self):
        main.stop_playback()
        main.stop_requested_at = time.time() - main.STOP_SETTLE - 1
        self.assertFalse(main.stop_pending())


if __name__ == '__main__':
    unittest.main()
