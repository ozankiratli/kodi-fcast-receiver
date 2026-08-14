"""Player behaviour, in particular the start position sent by the caster."""

import unittest

from context import fcast_plugin  # noqa: F401  (sets up sys.path)
from fcast_plugin.player import FCastPlayer
from fcast_plugin.FCastPackets import PlayBackState


class FakeSession:
    protocol_version = 3

    def __init__(self):
        self.playback_updates = []
        self.play_updates = []
        self.opcodes = []
        self.errors = []

    def send_playback_update(self, message):
        self.playback_updates.append(message)

    def send_play_update(self, play_data):
        self.play_updates.append(play_data)

    def sendOpCode(self, opcode):
        self.opcodes.append(opcode)

    def send_playback_error(self, message):
        self.errors.append(message)


class TestStartTime(unittest.TestCase):
    """`start_time` carries the sender's requested position into playback.

    main sets it from PlayMessage.time immediately before calling play(), and
    onAVStarted consumes it once. A class-level default must not interfere
    with that: an instance attribute set by main shadows it.
    """

    def test_seeks_to_the_position_the_sender_asked_for(self):
        player = FCastPlayer([])
        player.start_time = 12.5  # what main assigns from PlayMessage.time

        player.onAVStarted()

        self.assertEqual(player.seeked_to, [12.5])

    def test_position_is_consumed_once_not_reapplied(self):
        player = FCastPlayer([])
        player.start_time = 30.0

        player.onAVStarted()
        player.onAVStarted()

        self.assertEqual(player.seeked_to, [30.0])

    def test_playback_started_outside_fcast_does_not_raise(self):
        # The player receives callbacks for everything Kodi plays, not just
        # what we cast. Nothing has set start_time in that case.
        player = FCastPlayer([])

        player.onAVStarted()

        self.assertEqual(player.seeked_to, [])

    def test_zero_start_time_does_not_seek(self):
        player = FCastPlayer([])
        player.start_time = 0.0

        player.onAVStarted()

        self.assertEqual(player.seeked_to, [])


class TestEndOfPlayback(unittest.TestCase):
    """Playback ending must report Idle, and must not force a stop.

    FCast has no stopped state, so Idle is the only thing these can report.
    Forcing PlayerControl(Stop) here is what prevents a queued playlist from
    advancing, so these assert the callbacks stay side-effect free.
    """

    def test_ended_reports_idle_to_every_sender(self):
        sessions = [FakeSession(), FakeSession()]
        player = FCastPlayer(sessions)
        player.prev_time = 95

        player.onPlayBackEnded()

        for session in sessions:
            self.assertEqual(len(session.playback_updates), 1)
            self.assertEqual(session.playback_updates[0].state, PlayBackState.IDLE)
            self.assertEqual(session.playback_updates[0].time, 95)

    def test_stopped_reports_idle_too(self):
        session = FakeSession()
        player = FCastPlayer([session])

        player.onPlayBackStopped()

        self.assertEqual(session.playback_updates[0].state, PlayBackState.IDLE)

    def test_no_stop_opcode_is_sent_back_to_the_sender(self):
        # Stop is defined sender-to-receiver only.
        session = FakeSession()
        player = FCastPlayer([session])

        player.onPlayBackEnded()

        self.assertEqual(session.opcodes, [])

    def test_error_reports_a_playback_error_then_idle(self):
        session = FakeSession()
        player = FCastPlayer([session])

        player.onPlayBackError()

        self.assertEqual(len(session.errors), 1)
        self.assertEqual(session.playback_updates[0].state, PlayBackState.IDLE)

    def test_idle_after_teardown_does_not_query_the_player(self):
        # getTime()/getTotalTime() raise once the player has gone away.
        session = FakeSession()
        player = FCastPlayer([session])

        def boom():
            raise RuntimeError("player is gone")

        player.getTime = boom
        player.getTotalTime = boom

        player.onPlayBackEnded()

        self.assertEqual(session.playback_updates[0].state, PlayBackState.IDLE)


class TestPlaybackUpdates(unittest.TestCase):

    def test_start_reports_position_to_every_session(self):
        sessions = [FakeSession(), FakeSession()]
        player = FCastPlayer(sessions)
        player.start_time = 8.0
        player.total_time = 120.0

        player.onAVStarted()

        for session in sessions:
            self.assertEqual(len(session.playback_updates), 1)
            self.assertEqual(session.playback_updates[0].time, 8)
            self.assertEqual(session.playback_updates[0].duration, 120)


if __name__ == "__main__":
    unittest.main()
