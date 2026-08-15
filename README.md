# FCast receiver add-on for Kodi

## FCast

FCast is an open source protocol that enables wireless streaming of audio and video content between devices, supporting various stream types such as DASH, HLS, and mp4.

Unlike proprietary protocols like Chromecast and AirPlay, FCast offers an open approach, empowering third-party developers to create their own receiver devices or integrate the FCast protocol into their own apps.

Official web site: [fcast.org](https://fcast.org)

## Receiver

This add-on is an unofficial FCast receiver for Kodi. It allows you to stream content from any FCast client to Kodi media center.

The receiver listens on TCP port `46899` and advertises itself over mDNS as `_fcast._tcp`, so senders find it automatically on the local network. Senders can also connect directly by IP if discovery is unavailable.

**Requirements**

* Kodi 21 (Omega) or newer
* `inputstream.adaptive` 21.5.9 or newer, for HLS and DASH streams

**Tested senders**

* [Grayjay](https://grayjay.app)
* Official FCast senders
* CastLab for Android — **beta**, see Known Issues

## Installation

### From the add-on repository (recommended)

Installing the repository once lets Kodi update the receiver on its own, on every device.

1. In Kodi, allow add-ons from unknown sources: **Settings → System → Add-ons → Unknown sources**
2. Download `repository.fcast.ozankiratli-1.0.0.zip` from
   <https://ozankiratli.github.io/kodi-fcast-receiver/repository.fcast.ozankiratli/repository.fcast.ozankiratli-1.0.0.zip>
3. **Settings → Add-ons → Install from zip file**, and pick that zip
4. **Settings → Add-ons → Install from repository → FCast Receiver Repository → Services → FCast Receiver**

Kodi checks for updates on its own schedule as long as **Settings → System → Add-ons → Updates** is set to *Install updates automatically*.

### From a zip

Grab the zip from the [releases page](https://github.com/ozankiratli/kodi-fcast-receiver/releases) and use **Settings → Add-ons → Install from zip file**. Updates are then manual.

**Already running a zip install?** A zip install has no repository behind it, so Kodi has nothing to check for new versions. Install the repository as described above and Kodi will pick up newer releases for the add-on you already have — there is no need to uninstall it first.

## Configuration

### Kodi

Enable the following Kodi setting for speed control:

* **Settings → Player → Videos → Sync playback to display** — set to **On**

### Add-on settings

**Settings → Add-ons → My add-ons → Services → FCast Receiver → Configure**

* **General → Show on-screen notifications** — the messages shown when a sender connects, disconnects or starts something playing. Turn them off to keep the screen clear; errors are always shown regardless.
* **Pictures → Download pictures before showing them** — Kodi's picture viewer clears what is on screen the moment it is told to show something else, so casting a second photo left the screen black for the length of the download. With this on, the picture already up stays there while the next one is fetched, and they swap when it is ready. Turn it off to hand the URL straight to Kodi as before.
* **Pictures → Picture duration** — how long a picture in a cast playlist stays on screen before the queue moves on. A sender that asks for a duration of its own gets it, unless **Ignore the duration the sender asks for** is set. **Off** leaves each picture up until the sender moves on.

  A picture cast on its own is never closed automatically, whatever this is set to. It stays up until a sender stops it or you close it from Kodi.

## Changes in This Fork

### Fixed

* **Packet reassembly** — FCast messages split across TCP reads were corrupted, desynchronising the stream and dropping the connection. This only worked reliably when a message happened to arrive in a single read, so larger `Play` messages — Grayjay attaches title and thumbnail metadata — failed almost every time.
* **Protocol tolerance** — Unknown opcodes, opcodes for features this receiver does not implement, and unrecognised message fields are now ignored instead of closing the connection. Senders speaking a newer protocol version are no longer disconnected mid-handshake.
* **Stream request headers** — HTTP headers attached to a `Play` request are now applied, as `inputstream.adaptive` manifest and stream headers or appended to the URL for direct playback. CDNs that check `Referer` or `User-Agent` rejected these streams before.
* **HLS and DASH detection** — Both are now recognised from the container the sender declares as well as from the URL extension.
* **mDNS on LibreELEC and CoreELEC** — Discovery works against whichever D-Bus binding the platform ships: `python-dbus` on Debian and Raspbian, DBussy on LibreELEC and CoreELEC, with an `avahi-publish` fallback. A missing binding no longer stops the add-on from starting. The service now also advertises TXT records, which some senders require.
* **Leaked connection threads** — A disconnected client left a thread spinning for the lifetime of the Kodi session.
* **Silent start-up failures** — Errors during start-up are reported on screen and logged with a full traceback, instead of leaving the service quietly dead.
* **Speed control clamping** — Playback speed is now clamped to the range supported by Kodi (0.8x – 1.5x). Requests below 0.8x are rounded up and requests above 1.5x are rounded down, preventing out-of-range errors.
* **mDNS / device discovery** — The receiver broadcasts via mDNS so it is discoverable by sender devices on the local network.
* **CastLab compatibility** — Resolved a compatibility issue with the Android CastLab app.
* **Playback position sync** — Streams now start at the current playback position of the sending device.
* **Stream cancellation freeze** — Cancelling a stream occasionally caused Kodi to freeze. This is now resolved.

### Protocol support

The receiver implements FCast protocol **v2** and announces itself as such. Senders on v3 or later negotiate down, as the specification requires, and their v3-only messages (`Initial`, playlists, event subscription) are accepted and ignored rather than treated as errors. Playlists and event subscription are not implemented.

### Known Issues

* **Audio/video sync drift** — After a long pause or during extended playback (roughly 40+ minutes), the audio stream can begin skipping seconds intermittently while the video speeds up to catch up, breaking A/V sync. This is an `inputstream.adaptive` problem, not a receiver one: it reproduces with any add-on playing adaptive streams, whether or not FCast is involved (see xbmc/xbmc#22625). There is no workaround available from this add-on.
* **Images are not displayed properly** — CastLab sends photos with an image MIME type, which Kodi treats as video. The image appears for a few milliseconds before the player closes. A dedicated image path is needed.

## Development

### Environment

```
python -m venv venv
source ./venv/bin/activate
pip install -U mpv kodistubs
```

### Tests

```
make test
```

The suite runs against stubbed Kodi modules in `tests/stubs`, so it needs neither Kodi nor a device. It covers wire framing across every chunk boundary, opcode and unknown-field tolerance, version negotiation, and stream classification.

### Deploying to a device

Clone the repository on the Kodi machine and deploy in place:

```
make deploy
```

Or push to another machine over ssh:

```
make deploy KODI_HOST=pi@raspberrypi
```

Both copy the whole add-on with `rsync --delete`, so a renamed or removed module cannot leave a stale copy behind. LibreELEC and CoreELEC ship no `rsync`, `git` or `make` in the base image — deploy to those from a development machine with:

```
make deploy-ssh KODI_HOST=root@libreelec \
                KODI_ADDON_DIR=/storage/.kodi/addons/service.fcast.receiver
```

That needs only `ssh` and `tar` on the device. It stages the transfer and swaps on success, keeping the previous install alongside as `.bak`.

After any deploy, restart the service by disabling and re-enabling the add-on in **Settings → Add-ons**.

To see the add-on's own log lines, turn on **Settings → System → Logging → Enable debug logging**, then:

```
grep "FCast Receiver:" ~/.kodi/temp/kodi.log
```

### Diagnosing discovery problems

`tools/mdns_probe.py` runs standalone on a device, without Kodi. It reports which D-Bus bindings and Avahi tools are present, then registers the service with each backend in turn so you can confirm it with `avahi-browse -rt _fcast._tcp` from another machine.

## Acknowledgments

* Original creator: [c4valli](https://github.com/c4valli/kodi-fcast-receiver)
* Upstream fork: [wolf3592](https://github.com/wolf3592/kodi-fcast-receiver)
* LibreELEC D-Bus incompatibility reported by [Svensk2137](https://github.com/Svensk2137)
