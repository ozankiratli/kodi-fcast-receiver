# FCast receiver add-on for Kodi

## FCast

FCast is an open source protocol that enables wireless streaming of audio and video content between devices, supporting various stream types such as DASH, HLS, and mp4.

Unlike proprietary protocols like Chromecast and AirPlay, FCast offers an open approach, empowering third-party developers to create their own receiver devices or integrate the FCast protocol into their own apps.

Official web site: [fcast.org](https://fcast.org)

## Receiver

This add-on is an unofficial FCast receiver for Kodi. It allows you to stream content from any FCast client to Kodi media center.

## Changes in This Fork

### Fixed

* **Speed control clamping** — Playback speed is now clamped to the range supported by Kodi (0.8x – 1.5x). Requests below 0.8x are rounded up and requests above 1.5x are rounded down, preventing out-of-range errors.
* **mDNS / device discovery** — The receiver now broadcasts via mDNS so it is discoverable by sender devices on the local network.
* **CastLab compatibility** — Resolved a compatibility issue with the Android CastLab app. CastLab support is currently in **beta**.
* **Playback position sync** — Streams now start at the current playback position of the sending device.
* **Stream cancellation freeze** — Cancelling a stream occasionally caused Kodi to freeze. This is now resolved. 

### Known Issues

* **Audio/video sync drift** — After a long pause or during extended playback (roughly 40+ minutes), the audio stream can begin skipping seconds intermittently, breaking A/V sync.

## Configuration

Enable the following Kodi setting for functionality:

* **Settings → Player → Videos → Sync playback to display** — set to **On**

## Acknowledgments

* Original creator: [c4valli](https://github.com/c4valli/kodi-fcast-receiver)
* Upstream fork: [wolf3592](https://github.com/wolf3592/kodi-fcast-receiver)

## Development Environment

1. Create virtual environment

```
python -m venv venv
```

2. Activate virtual environment

```
source ./venv/bin/activate
```

3. Install required modules

```
pip install -U mpv kodistubs
```
