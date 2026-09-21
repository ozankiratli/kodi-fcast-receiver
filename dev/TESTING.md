# Testing

## Running the suite

    make test

It runs against stubbed Kodi modules, so it needs no device, no Kodi install and no network. It is fast enough to run on every change, and CI runs it before building anything in both release workflows.

Under the hood that is `python3 -m unittest discover -s dev/tests -t dev/tests -v`, so a single file or a single test can be run the usual way:

    python3 -m unittest discover -s dev/tests -t dev/tests -p test_session.py
    python3 -m unittest discover -s dev/tests -t dev/tests -k test_muted_reads_as_zero

## How it runs without Kodi

`dev/tests/context.py` puts `dev/tests/stubs` on `sys.path` ahead of `resources/lib`, so `import xbmc` inside the add-on finds the stub. Every test module imports it first. The stubs are deliberately thin: `xbmc` records log lines, builtins and JSON-RPC calls and lets tests script the replies; `xbmcgui` records notifications and reports whichever window id a test sets; `xbmcaddon` raises for a setting that is not there, which is what Kodi does for one `resources/settings.xml` does not declare; `xbmcvfs` maps `special://temp` somewhere disposable.

When a stub and the real Kodi disagree, the stub is wrong. Fixing it is part of the change.

## What is covered

- `test_session.py` -- the wire protocol: framing across arbitrary read boundaries, unknown opcodes and fields, version negotiation.
- `test_connection.py` -- a sender that stops being there: partial sends, the stall clock, keepalive, whose stream is whose, and the read loop letting go.
- `test_play.py` -- classifying a play request, and the HTTP headers that go with it.
- `test_player.py` -- start position, ownership of playback, and what senders are told when media ends.
- `test_playlist.py` -- queues a sender hands over, and walking them.
- `test_images.py` -- pictures end to end: classification, the viewer's lifecycle, caching, durations.
- `test_volume.py` -- reading volume from Kodi, setting it, and publishing changes.
- `test_settings.py` -- the settings file and the code that reads it, including that every declared id is read somewhere and every read id is declared.
- `test_mdns.py` -- the discovery backends, and that none of them may raise.

## The standard this project holds

**Name the suite that would fail if the change were wrong, and run that one.** If there is not one, that is the finding, and it is worth saying out loud rather than reporting the rest as green.

**A check that cannot fail is not a check.** The way to know is to break the code on purpose and watch the test go red. That is worth doing by hand for anything subtle -- change the condition, invert the comparison, delete the line -- and it has repeatedly found tests that were passing for the wrong reason. Watch for a green run that stays green: it usually means the test never reached the code at all.

**Check the baseline before trusting the result.** A mutation run against a suite that was already failing tells you nothing, and the failure it reports may have nothing to do with what you changed.

**Render it, don't read it.** Reading the code and reasoning about what it produces has produced confident wrong answers here more than once. Look at the bytes, the log, the screen.

## Exercising the real thing

Four tools under `dev/tools/`, all standalone, none of them needing Kodi except where they are pointed at it.

`fcast_send.py` is a sender with its own generated media, so there is nothing to download or host. It is the only thing that will exercise the receiver's playlist path: Grayjay keeps its queue on the sender and drives it with `MediaItemEnd` events, so no real app walks a v3 playlist for us.

    python3 dev/tools/fcast_send.py --receiver 192.168.1.42 --playlist 5
    python3 dev/tools/fcast_send.py --receiver 192.168.1.42 --playlist 5 --set-item 3
    python3 dev/tools/fcast_send.py --receiver 192.168.1.42 --image

`fcast_trace.py` is a receiver that logs everything and fakes a short playback, for the question "what does this sender actually react to?". It can emit each end-of-media signal in turn, which is how the `MediaItemEnd` behavior was settled.

    python3 dev/tools/fcast_trace.py --end-signal event --version 3

`fcast_proxy.py` sits between a sender and a receiver that already behaves correctly and prints the whole conversation while forwarding bytes untouched. The transcript is the authoritative answer to "what does the working receiver send that we do not?".

    python3 dev/tools/fcast_proxy.py --target 192.168.1.50 --full

`mdns_probe.py` reports which mDNS backend works on a given machine. Copy it to the device and run it with the same interpreter Kodi uses; it registers for real, holds it, and cleans up. Watch from another box with `avahi-browse -rt _fcast._tcp`.

## What no suite covers

The stubs stop at the edge of Kodi, so everything past that edge is checked by hand on a device:

Playback actually starting, and an adaptive stream actually playing -- the tests check what is handed to `inputstream.adaptive`, not that it works. The picture viewer on a real skin, since window and dialog ids are the part the stubs assert rather than reproduce. Discovery from a real sender app. Anything about how long Kodi takes to do something, including the player teardown behind `stop_pending()`.

When one of these is what a change turns on, say so in the report rather than letting `make test` passing stand in for it.
