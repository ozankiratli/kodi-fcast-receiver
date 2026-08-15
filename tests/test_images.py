"""Still pictures: classification, the viewer's lifecycle, and how long one stays up."""

import http.server
import os
import shutil
import tempfile
import threading
import time
import unittest

from context import fcast_plugin  # noqa: F401  (sets up sys.path)
from fcast_plugin import main, settings
from fcast_plugin.image_viewer import ImageViewer, WINDOW_SLIDESHOW
from fcast_plugin.FCastPackets import EventType, PlayBackState, PlayMessage
from test_playlist import playlist_message, video

import xbmc
import xbmcaddon
import xbmcgui

PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
       b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
       b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')


class ImageServer(http.server.BaseHTTPRequestHandler):
    """Serves one picture and records the headers it was asked with."""

    received_headers = {}

    def do_GET(self):
        type(self).received_headers = dict(self.headers)
        if self.path.startswith("/missing"):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(PNG)))
        self.end_headers()
        self.wfile.write(PNG)

    def log_message(self, *args):
        pass


class FakePlayer:
    """Enough of FCastPlayer for handle_play to reach its video branch."""

    start_time = 0.0
    owns_playback = False

    def __init__(self):
        self.played = []
        self.paused = []

    def isPlaying(self):
        return False

    def play(self, item=None, listitem=None):
        self.played.append(item)

    def doPause(self):
        self.paused.append(True)

    def doResume(self):
        self.paused.append(False)


def wait_for(predicate, timeout=5.0):
    """Give a thread the play message started a moment to get there."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class FakeSession:
    def __init__(self):
        self.playback_updates = []
        self.play_updates = []
        self.media_events = []

    def send_playback_update(self, message):
        self.playback_updates.append(message)

    def send_play_update(self, play_data):
        self.play_updates.append(play_data)

    def send_media_event(self, event_type, item):
        self.media_events.append((event_type, item))
        return True

    def states(self):
        return [update.state for update in self.playback_updates]


def image(name, **extra):
    item = {"container": "image/jpeg", "url": "https://e/%s.jpg" % name}
    item.update(extra)
    return item


class TestClassification(unittest.TestCase):

    def test_declared_image_containers(self):
        for container in ("image/jpeg", "image/png", "image/webp", "image/avif",
                          "IMAGE/JPEG", "image/jpeg; charset=binary"):
            with self.subTest(container=container):
                self.assertTrue(main.is_image(container, "https://e/p"))

    def test_extension_is_used_when_no_container_is_declared(self):
        self.assertTrue(main.is_image(None, "https://e/holiday.JPG"))
        self.assertTrue(main.is_image("", "https://e/p.png?size=large"))

    def test_streams_and_video_are_not_images(self):
        for container, url in (
            ("video/mp4", "https://e/v.mp4"),
            ("application/dash+xml", "https://e/m.mpd"),
            ("application/vnd.apple.mpegurl", "https://e/m.m3u8"),
            (None, "https://e/v.mp4"),
        ):
            with self.subTest(container=container):
                self.assertFalse(main.is_image(container, url))

    def test_a_declared_container_beats_a_misleading_extension(self):
        # A video whose URL happens to end .png is still a video.
        self.assertFalse(main.is_image("video/mp4", "https://e/thumb.png"))


class ViewerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.HTTPServer(("127.0.0.1", 0), ImageServer)
        cls.origin = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        xbmc.builtins_called.clear()
        xbmcgui.current_window_id = 10000
        xbmcgui.current_dialog_id = 9999
        ImageServer.received_headers = {}
        self.closed = []
        self.expired = []
        self.cache = tempfile.mkdtemp()
        self.viewer = ImageViewer(on_closed=lambda: self.closed.append(True),
                                  on_expired=lambda: self.expired.append(True))

    def tearDown(self):
        shutil.rmtree(self.cache, ignore_errors=True)

    def show_now(self, url, headers=None, duration=0.0):
        self.viewer.show(url, duration=duration)

    def shown(self):
        return [c for c in xbmc.builtins_called if c.startswith("ShowPicture(")]

    def cached_files(self):
        return os.listdir(self.cache)

    def on_screen(self):
        # Kodi reports the picture viewer as the current dialog, not window.
        xbmcgui.current_dialog_id = WINDOW_SLIDESHOW

    def off_screen(self):
        xbmcgui.current_dialog_id = 9999


class TestViewerLifecycle(ViewerTestCase):

    def test_dismissal_from_the_ui_is_noticed(self):
        self.show_now(self.origin + "/p.png")
        self.on_screen()
        self.viewer.poll()
        self.assertTrue(self.viewer.is_showing)

        self.off_screen()
        self.viewer.poll()

        self.assertFalse(self.viewer.is_showing)
        self.assertEqual(self.closed, [True])

    def test_slow_opening_is_not_mistaken_for_dismissal(self):
        # ShowPicture is asynchronous, so the window is not up immediately.
        self.show_now(self.origin + "/p.png")

        for _ in range(5):
            self.viewer.poll()

        self.assertTrue(self.viewer.is_showing)
        self.assertEqual(self.closed, [])

    def test_a_viewer_that_never_opens_gives_up(self):
        self.show_now(self.origin + "/p.png")
        self.viewer._opened_at -= 99  # past the open timeout

        self.viewer.poll()

        self.assertFalse(self.viewer.is_showing)
        self.assertEqual(self.closed, [True])

    def test_close_dismisses_a_visible_viewer(self):
        # ACTION_STOP, which is what Kodi binds to X - the key that actually
        # exits the picture viewer. Back navigates instead.
        self.show_now(self.origin + "/p.png")
        self.on_screen()
        xbmc.builtins_called.clear()

        self.assertTrue(self.viewer.close())

        self.assertIn("Action(Stop)", xbmc.builtins_called)
        self.assertFalse(self.viewer.is_showing)

    def test_close_still_works_when_tracking_has_gone_stale(self):
        # A sender's Stop must dismiss a picture that is genuinely on screen,
        # even if we stopped believing one was.
        self.on_screen()
        self.viewer._showing = False

        self.assertTrue(self.viewer.close())

        self.assertIn("Action(Stop)", xbmc.builtins_called)

    def test_close_is_a_no_op_with_nothing_showing(self):
        self.off_screen()

        self.assertFalse(self.viewer.close())

        self.assertEqual(xbmc.builtins_called, [])

    def test_showing_a_second_image_swaps_in_place(self):
        # Closing and reopening drops to the UI behind for a frame, which
        # reads as a dark flash between images.
        self.show_now(self.origin + "/one.png")
        self.on_screen()
        self.viewer.poll()
        xbmc.builtins_called.clear()

        self.show_now(self.origin + "/two.png")

        self.assertNotIn("Action(Stop)", xbmc.builtins_called)
        self.assertEqual(len(self.shown()), 1)
        self.assertTrue(self.viewer.is_showing)

    def test_swapping_in_place_keeps_the_viewer_confirmed_on_screen(self):
        # Resetting the open-timeout state on a swap would make the next poll
        # think the viewer was still opening.
        self.show_now(self.origin + "/one.png")
        self.on_screen()
        self.viewer.poll()

        self.show_now(self.origin + "/two.png")
        self.off_screen()
        self.viewer.poll()

        self.assertFalse(self.viewer.is_showing)
        self.assertEqual(self.closed, [True])

    def test_detection_accepts_either_the_window_or_the_dialog_id(self):
        """The viewer is a CGUIDialog, so it reports through the dialog id.

        Checking only getCurrentWindowId() meant this was always False, which
        made the poll declare the viewer dead five seconds in and left a
        sender's Stop with nothing to act on.
        """
        self.off_screen()
        self.assertFalse(self.viewer.is_on_screen)

        xbmcgui.current_dialog_id = WINDOW_SLIDESHOW
        self.assertTrue(self.viewer.is_on_screen)

        xbmcgui.current_dialog_id = 9999
        xbmcgui.current_window_id = WINDOW_SLIDESHOW
        self.assertTrue(self.viewer.is_on_screen)
        xbmcgui.current_window_id = 10000

    def test_a_visible_viewer_is_not_declared_dead_by_the_poll(self):
        self.show_now("https://e/p.jpg")
        self.on_screen()
        self.viewer._opened_at -= 99  # well past the open timeout

        for _ in range(5):
            self.viewer.poll()

        self.assertTrue(self.viewer.is_showing)
        self.assertEqual(self.closed, [])

    def test_poll_does_nothing_when_no_image_was_shown(self):
        self.viewer.poll()
        self.assertEqual(self.closed, [])


class TestCountdown(ViewerTestCase):
    """How long a picture stays up, at the viewer's own level."""

    def test_a_picture_with_no_duration_never_expires(self):
        self.show_now(self.origin + "/p.png")
        self.on_screen()

        for _ in range(5):
            self.viewer.poll()

        self.assertEqual(self.viewer.seconds_left, 0.0)
        self.assertEqual(self.expired, [])
        self.assertTrue(self.viewer.is_showing)

    def test_the_countdown_starts_when_the_picture_is_up_not_when_asked_for(self):
        # ShowPicture returns long before Kodi has fetched anything, so
        # counting from there would cut a slow picture short.
        self.show_now(self.origin + "/p.png", duration=30)
        self.assertEqual(self.viewer.seconds_left, 0.0)

        self.on_screen()
        self.viewer.poll()

        self.assertAlmostEqual(self.viewer.seconds_left, 30, delta=0.5)

    def test_time_running_out_hands_over_once(self):
        self.show_now(self.origin + "/p.png", duration=0.05)
        self.on_screen()
        self.viewer.poll()

        time.sleep(0.06)
        self.viewer.poll()
        self.viewer.poll()

        self.assertEqual(self.expired, [True])
        # Still showing: it is the callback's business what comes next.
        self.assertTrue(self.viewer.is_showing)

    def test_a_picture_swapped_in_place_gets_its_own_time(self):
        # The window is already up, so there is no opening to wait for.
        self.show_now(self.origin + "/one.png", duration=10)
        self.on_screen()
        self.viewer.poll()

        self.show_now(self.origin + "/two.png", duration=30)

        self.assertAlmostEqual(self.viewer.seconds_left, 30, delta=0.5)

    def test_closing_forgets_the_countdown(self):
        self.show_now(self.origin + "/p.png", duration=30)
        self.on_screen()
        self.viewer.poll()

        self.viewer.close()

        self.assertEqual(self.viewer.seconds_left, 0.0)

    def test_pause_holds_the_countdown_where_it_is(self):
        self.show_now(self.origin + "/p.png", duration=0.05)
        self.on_screen()
        self.viewer.poll()

        self.assertTrue(self.viewer.pause())
        time.sleep(0.06)
        self.viewer.poll()

        self.assertEqual(self.expired, [])
        self.assertGreater(self.viewer.seconds_left, 0.0)

    def test_resume_carries_on_from_where_it_paused(self):
        self.show_now(self.origin + "/p.png", duration=30)
        self.on_screen()
        self.viewer.poll()
        self.viewer.pause()
        left = self.viewer.seconds_left

        self.viewer.resume()

        self.assertAlmostEqual(self.viewer.seconds_left, left, delta=0.5)

    def test_resuming_a_picture_with_no_duration_does_not_start_a_clock(self):
        self.show_now(self.origin + "/p.png")
        self.on_screen()
        self.viewer.poll()
        self.viewer.pause()

        self.viewer.resume()

        self.assertEqual(self.viewer.seconds_left, 0.0)

    def test_pausing_before_the_picture_appears_holds_the_whole_duration(self):
        # A sender can pause inside the moment between ShowPicture and Kodi
        # having the picture up, before the countdown has begun at all.
        self.show_now(self.origin + "/p.png", duration=30)
        self.viewer.pause()

        self.on_screen()
        self.viewer.poll()
        self.assertEqual(self.viewer.seconds_left, 30)

        self.viewer.resume()

        self.assertAlmostEqual(self.viewer.seconds_left, 30, delta=0.5)
        self.assertEqual(self.expired, [])

    def test_pause_and_resume_say_whether_there_was_a_picture(self):
        # False is what tells main to give the request to the player instead.
        self.assertFalse(self.viewer.pause())
        self.assertFalse(self.viewer.resume())

        self.show_now(self.origin + "/p.png")

        self.assertTrue(self.viewer.pause())
        self.assertTrue(self.viewer.resume())


class TestPlaybackReporting(unittest.TestCase):
    def setUp(self):
        xbmc.builtins_called.clear()
        xbmcgui.current_window_id = 10000
        xbmcgui.current_dialog_id = 9999
        main.sessions.clear()
        self.session = FakeSession()
        main.sessions.append(self.session)
        self.previous_player, main.player = main.player, FakePlayer()

    def tearDown(self):
        main.sessions.clear()
        main.image_viewer.close()
        main.player = self.previous_player

    def show(self, message):
        main.handle_play(None, message)

    def test_showing_an_image_reports_playing(self):
        main.handle_image(PlayMessage(container="image/jpeg", url="https://e/p.jpg"))

        self.assertEqual(self.session.playback_updates[0].state, PlayBackState.PLAYING)

    def test_closing_reports_idle(self):
        main.on_image_closed()

        self.assertEqual(self.session.playback_updates[-1].state, PlayBackState.IDLE)

    def test_sender_stop_dismisses_the_picture_and_reports_idle(self):
        self.show(PlayMessage(container="image/jpeg", url="https://e/p.jpg"))
        xbmcgui.current_dialog_id = WINDOW_SLIDESHOW
        xbmc.builtins_called.clear()
        self.session.playback_updates.clear()

        main.handle_stop(None)

        self.assertIn("Action(Stop)", xbmc.builtins_called)
        self.assertEqual(self.session.playback_updates[-1].state, PlayBackState.IDLE)
        self.assertFalse(main.image_viewer.is_showing)

    def test_an_image_play_never_reaches_the_video_player(self):
        # This is the whole point: the video player renders a picture for a
        # few milliseconds and then closes.
        self.show(PlayMessage(container="image/jpeg", url="https://e/p.jpg"))

        self.assertTrue(any(c.startswith("ShowPicture(") for c in xbmc.builtins_called))
        self.assertTrue(main.image_viewer.is_showing)

    def test_starting_a_video_dismisses_a_picture(self):
        # The picture viewer sits above the video window, so a picture left up
        # hides the video completely.
        self.show(PlayMessage(container="image/jpeg", url="https://e/p.jpg"))
        xbmcgui.current_dialog_id = WINDOW_SLIDESHOW
        xbmc.builtins_called.clear()

        main.handle_play(None, PlayMessage(container="video/mp4", url="https://e/v.mp4"))

        self.assertIn("Action(Stop)", xbmc.builtins_called)
        self.assertFalse(main.image_viewer.is_showing)


class TestPictureDuration(unittest.TestCase):
    """showDuration: only playlists, and only as long as the user allows."""

    def setUp(self):
        xbmc.builtins_called.clear()
        xbmcaddon.reset_settings()
        xbmcgui.current_window_id = 10000
        xbmcgui.current_dialog_id = 9999
        main.sessions.clear()
        self.session = FakeSession()
        main.sessions.append(self.session)
        self.previous_player, main.player = main.player, FakePlayer()
        main.playlist = None

    def tearDown(self):
        main.sessions.clear()
        main.image_viewer.close()
        main.player = self.previous_player
        main.playlist = None
        xbmcaddon.reset_settings()

    def on_screen(self):
        xbmcgui.current_dialog_id = WINDOW_SLIDESHOW

    def play(self, message):
        main.handle_play(None, message)

    def start(self, *items):
        """Cast a playlist and let the first picture reach the screen."""
        self.play(playlist_message(list(items)))
        self.on_screen()
        main.image_viewer.poll()

    def test_a_picture_cast_on_its_own_is_never_timed_out(self):
        # Ozan's constraint, and what FCast's own receiver does: it starts the
        # showDuration timer only for items that came from a playlist.
        xbmcaddon.settings[settings.PLAYLIST_IMAGE_DURATION] = 5

        self.play(PlayMessage(container="image/jpeg", url="https://e/p.jpg"))
        self.on_screen()
        main.image_viewer.poll()

        self.assertEqual(main.image_viewer.seconds_left, 0.0)

    def test_a_playlist_picture_gets_the_duration_the_sender_asked_for(self):
        self.start(image("a", showDuration=45), image("b"))

        self.assertAlmostEqual(main.image_viewer.seconds_left, 45, delta=0.5)

    def test_a_playlist_picture_with_no_duration_gets_the_users(self):
        # Otherwise it holds up the rest of the queue for good: nothing ever
        # reports that a picture finished.
        xbmcaddon.settings[settings.PLAYLIST_IMAGE_DURATION] = 20

        self.start(image("a"), image("b"))

        self.assertAlmostEqual(main.image_viewer.seconds_left, 20, delta=0.5)

    def test_the_user_can_override_the_senders_duration(self):
        xbmcaddon.settings.update({
            settings.PLAYLIST_IMAGE_DURATION: 20,
            settings.PLAYLIST_IMAGE_DURATION_OVERRIDE: True,
        })

        self.start(image("a", showDuration=45), image("b"))

        self.assertAlmostEqual(main.image_viewer.seconds_left, 20, delta=0.5)

    def test_a_duration_of_zero_leaves_the_picture_up(self):
        # The way out for anyone who wants to move through a slideshow by hand.
        xbmcaddon.settings[settings.PLAYLIST_IMAGE_DURATION] = 0

        self.start(image("a"), image("b"))

        self.assertEqual(main.image_viewer.seconds_left, 0.0)

    def test_the_senders_duration_still_applies_when_the_users_is_zero(self):
        xbmcaddon.settings[settings.PLAYLIST_IMAGE_DURATION] = 0

        self.start(image("a", showDuration=45), image("b"))

        self.assertAlmostEqual(main.image_viewer.seconds_left, 45, delta=0.5)

    def test_time_running_out_moves_the_queue_on(self):
        self.start(image("a", showDuration=0.05), image("b", showDuration=60))
        xbmc.builtins_called.clear()

        time.sleep(0.06)
        main.image_viewer.poll()

        self.assertEqual(main.playlist.index, 1)
        self.assertIn("ShowPicture(https://e/b.jpg)", xbmc.builtins_called)
        self.assertTrue(main.image_viewer.is_showing)

    def test_moving_on_reports_the_picture_that_finished(self):
        # A MediaItemEnd with a null item tells a sender nothing: it matches
        # the item against its own queue entry to know what ended.
        self.start(image("a", showDuration=0.05), image("b", showDuration=60))

        time.sleep(0.06)
        main.image_viewer.poll()

        self.assertEqual(len(self.session.media_events), 1)
        event_type, item = self.session.media_events[0]
        self.assertEqual(event_type, EventType.MEDIA_ITEM_END)
        self.assertIsNotNone(item)
        self.assertEqual(item.url, "https://e/a.jpg")

    def test_the_last_picture_closes_the_viewer_and_reports_idle(self):
        self.start(image("only", showDuration=0.05))
        self.session.playback_updates.clear()
        xbmc.builtins_called.clear()

        time.sleep(0.06)
        main.image_viewer.poll()

        self.assertIn("Action(Stop)", xbmc.builtins_called)
        self.assertFalse(main.image_viewer.is_showing)
        self.assertEqual(self.session.states()[-1], PlayBackState.IDLE)
        self.assertIsNone(main.playlist)

    def test_a_video_after_a_picture_is_played_not_shown(self):
        self.start(image("a", showDuration=0.05), video("b"))
        xbmc.builtins_called.clear()

        time.sleep(0.06)
        main.image_viewer.poll()

        self.assertEqual(main.playlist.index, 1)
        # The picture viewer sits above the video window, so it has to go.
        self.assertIn("Action(Stop)", xbmc.builtins_called)
        self.assertTrue(wait_for(lambda: main.player.played == ["https://e/b.mp4"]))

    def test_pausing_a_picture_holds_it_and_says_so(self):
        self.start(image("a", showDuration=60), image("b"))
        self.session.playback_updates.clear()

        main.handle_pause(None)

        self.assertEqual(self.session.states(), [PlayBackState.PAUSED])
        self.assertGreater(main.image_viewer.seconds_left, 0.0)

        main.handle_resume(None)

        self.assertEqual(self.session.states()[-1], PlayBackState.PLAYING)

    def test_pausing_a_picture_leaves_the_player_alone(self):
        # While a picture is up, whatever Kodi is playing is not ours.
        self.start(image("a", showDuration=60))
        main.player.paused = []

        main.handle_pause(None)

        self.assertEqual(main.player.paused, [])


if __name__ == "__main__":
    unittest.main()
