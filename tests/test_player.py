"""Player callbacks must survive playback the add-on did not start.

FCastPlayer is a global xbmc.Player subclass, so Kodi calls these for
everything it plays. Both cases here were reported from the field on
0.2.0-beta by a user playing local FLAC music while the add-on was connected.
"""

import unittest

from context import fcast_plugin  # noqa: F401  (sets up sys.path)
from fcast_plugin.player import FCastPlayer


class FakeSession:
    def __init__(self):
        self.playback_updates = []

    def send_playback_update(self, message):
        self.playback_updates.append(message)

    def sendOpCode(self, opcode):
        pass


def unavailable(*args):
    raise RuntimeError("Kodi is not playing any media file")


class TestStartTime(unittest.TestCase):
    """`start_time` carries the sender's requested position into playback.

    main sets it from PlayMessage.time immediately before calling play(), and
    onAVStarted consumes it once. The class-level default must not interfere:
    an instance attribute set by main shadows it.
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
        # Nothing has set start_time when Kodi plays something we did not cast.
        player = FCastPlayer([])

        player.onAVStarted()

        self.assertEqual(player.seeked_to, [])

    def test_zero_start_time_does_not_seek(self):
        player = FCastPlayer([])
        player.start_time = 0.0

        player.onAVStarted()

        self.assertEqual(player.seeked_to, [])


class TestPlayerNotReady(unittest.TestCase):
    """getTime() raises once the player has moved on.

    Gapless audio makes onAVStarted arrive after playback has already advanced
    past the item it was announcing.
    """

    def test_time_change_survives_a_player_that_has_moved_on(self):
        session = FakeSession()
        player = FCastPlayer([session])
        player.getTime = unavailable

        player.onPlayBackTimeChanged()  # must not raise

        self.assertEqual(session.playback_updates, [])

    def test_duration_failing_is_also_survivable(self):
        session = FakeSession()
        player = FCastPlayer([session])
        player.getTotalTime = unavailable

        player.onPlayBackTimeChanged()

        self.assertEqual(session.playback_updates, [])

    def test_av_started_survives_a_player_that_has_moved_on(self):
        session = FakeSession()
        player = FCastPlayer([session])
        player.start_time = 30.0
        player.seekTime = unavailable
        player.getTime = unavailable

        player.onAVStarted()  # must not raise

        self.assertEqual(session.playback_updates, [])

    def test_a_ready_player_still_reports_normally(self):
        session = FakeSession()
        player = FCastPlayer([session])
        player.time = 42.0
        player.total_time = 300.0

        player.onPlayBackTimeChanged()

        self.assertEqual(len(session.playback_updates), 1)
        self.assertEqual(session.playback_updates[0].time, 42)
        self.assertEqual(session.playback_updates[0].duration, 300)


if __name__ == "__main__":
    unittest.main()
