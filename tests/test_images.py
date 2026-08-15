"""Still pictures: classification, pre-fetching, and the viewer's lifecycle."""

import http.server
import os
import shutil
import tempfile
import threading
import unittest

from context import fcast_plugin  # noqa: F401  (sets up sys.path)
from fcast_plugin import main
from fcast_plugin.image_viewer import ImageViewer, WINDOW_SLIDESHOW
from fcast_plugin.FCastPackets import PlayBackState, PlayMessage

import xbmc
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

    def isPlaying(self):
        return False

    def play(self, *args, **kwargs):
        pass


class FakeSession:
    def __init__(self):
        self.playback_updates = []
        self.play_updates = []

    def send_playback_update(self, message):
        self.playback_updates.append(message)

    def send_play_update(self, play_data):
        self.play_updates.append(play_data)


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
        ImageServer.received_headers = {}
        self.closed = []
        self.cache = tempfile.mkdtemp()
        self.viewer = ImageViewer(on_closed=lambda: self.closed.append(True))

    def tearDown(self):
        shutil.rmtree(self.cache, ignore_errors=True)

    def show_now(self, url, headers=None):
        self.viewer.show(url)

    def shown(self):
        return [c for c in xbmc.builtins_called if c.startswith("ShowPicture(")]

    def cached_files(self):
        return os.listdir(self.cache)

    def on_screen(self):
        xbmcgui.current_window_id = WINDOW_SLIDESHOW

    def off_screen(self):
        xbmcgui.current_window_id = 10000


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

    def test_poll_does_nothing_when_no_image_was_shown(self):
        self.viewer.poll()
        self.assertEqual(self.closed, [])


class TestPlaybackReporting(unittest.TestCase):
    def setUp(self):
        xbmc.builtins_called.clear()
        xbmcgui.current_window_id = 10000
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
        xbmcgui.current_window_id = WINDOW_SLIDESHOW
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
        xbmcgui.current_window_id = WINDOW_SLIDESHOW
        xbmc.builtins_called.clear()

        main.handle_play(None, PlayMessage(container="video/mp4", url="https://e/v.mp4"))

        self.assertIn("Action(Stop)", xbmc.builtins_called)
        self.assertFalse(main.image_viewer.is_showing)


if __name__ == "__main__":
    unittest.main()
