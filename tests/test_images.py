"""Still pictures: classification, and the viewer's lifecycle."""

import unittest

from context import fcast_plugin  # noqa: F401  (sets up sys.path)
from fcast_plugin import main
from fcast_plugin.image_viewer import ImageViewer, WINDOW_SLIDESHOW
from fcast_plugin.FCastPackets import PlayBackState, PlayMessage

import xbmc
import xbmcgui


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
    def setUp(self):
        xbmc.builtins_called.clear()
        xbmcgui.current_window_id = 10000
        self.closed = []
        self.viewer = ImageViewer(on_closed=lambda: self.closed.append(True))

    def on_screen(self):
        xbmcgui.current_window_id = WINDOW_SLIDESHOW

    def off_screen(self):
        xbmcgui.current_window_id = 10000


class TestViewerLifecycle(ViewerTestCase):

    def test_show_opens_kodi_picture_viewer(self):
        self.viewer.show("https://e/p.jpg")

        self.assertIn("ShowPicture(https://e/p.jpg)", xbmc.builtins_called)
        self.assertTrue(self.viewer.is_showing)

    def test_dismissal_from_the_ui_is_noticed(self):
        self.viewer.show("https://e/p.jpg")
        self.on_screen()
        self.viewer.poll()
        self.assertTrue(self.viewer.is_showing)

        self.off_screen()
        self.viewer.poll()

        self.assertFalse(self.viewer.is_showing)
        self.assertEqual(self.closed, [True])

    def test_slow_opening_is_not_mistaken_for_dismissal(self):
        # ShowPicture is asynchronous, so the window is not up immediately.
        self.viewer.show("https://e/p.jpg")

        for _ in range(5):
            self.viewer.poll()

        self.assertTrue(self.viewer.is_showing)
        self.assertEqual(self.closed, [])

    def test_a_viewer_that_never_opens_gives_up(self):
        self.viewer.show("https://e/p.jpg")
        self.viewer._opened_at -= 99  # past the open timeout

        self.viewer.poll()

        self.assertFalse(self.viewer.is_showing)
        self.assertEqual(self.closed, [True])

    def test_close_only_acts_while_the_viewer_is_on_screen(self):
        # Back is a global input action; firing it blind would hit whatever
        # else happens to be focused.
        self.viewer.show("https://e/p.jpg")
        self.off_screen()
        xbmc.builtins_called.clear()

        self.viewer.close()

        self.assertEqual(xbmc.builtins_called, [])
        self.assertFalse(self.viewer.is_showing)

    def test_close_dismisses_a_visible_viewer(self):
        self.viewer.show("https://e/p.jpg")
        self.on_screen()
        xbmc.builtins_called.clear()

        self.viewer.close()

        self.assertIn("Action(Back)", xbmc.builtins_called)
        self.assertFalse(self.viewer.is_showing)

    def test_showing_a_second_image_replaces_the_first(self):
        self.viewer.show("https://e/one.jpg")
        self.on_screen()
        self.viewer.show("https://e/two.jpg")

        self.assertIn("ShowPicture(https://e/two.jpg)", xbmc.builtins_called)
        self.assertTrue(self.viewer.is_showing)

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

    def tearDown(self):
        main.sessions.clear()
        main.image_viewer.close()

    def test_showing_an_image_reports_playing(self):
        main.handle_image(PlayMessage(container="image/jpeg", url="https://e/p.jpg"), "")

        self.assertEqual(self.session.playback_updates[0].state, PlayBackState.PLAYING)

    def test_closing_reports_idle(self):
        main.on_image_closed()

        self.assertEqual(self.session.playback_updates[-1].state, PlayBackState.IDLE)

    def test_headers_are_appended_to_the_url(self):
        main.handle_image(
            PlayMessage(container="image/jpeg", url="https://e/p.jpg"),
            "Referer=https%3A%2F%2Fe%2F")

        self.assertIn("ShowPicture(https://e/p.jpg|Referer=https%3A%2F%2Fe%2F)",
                      xbmc.builtins_called)

    def test_an_image_play_never_reaches_the_video_player(self):
        # This is the whole point: the video player renders a picture for a
        # few milliseconds and then closes.
        main.handle_play(None, PlayMessage(container="image/jpeg", url="https://e/p.jpg"))

        self.assertTrue(any(c.startswith("ShowPicture(") for c in xbmc.builtins_called))
        self.assertTrue(main.image_viewer.is_showing)


if __name__ == "__main__":
    unittest.main()
