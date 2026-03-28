# FCast receiver add-on for Kodi

## FCast

FCast is an open source protocol that enables wireless streaming of audio and video content between devices, supporting various stream types such as DASH, HLS, and mp4.

Unlike proprietary protocols like Chromecast and AirPlay, FCast offers an open approach, empowering third-party developers to create their own receiver devices or integrate the FCast protocol into their own apps.

Official web site: [fcast.org](https://fcast.org)

## Receiver

This add-on is an unofficial FCast receiver for Kodi. It allows you to stream content from any FCast client to Kodi media center.

## Status

The original addon by c4valli has not been updated for over 9 months and is currently non-functional. This fork aims to:

* Restore functionality by fixing broken features.
* Ensure compatibility with the latest Kodi versions (e.g., Kodi 21 "Omega").
* Provide ongoing maintenance and updates.

## Changes in This Fork

### Fixed

* **Speed control clamping** — Playback speed is now clamped to the range supported by Kodi (0.8x – 1.5x). Requests below 0.8x are rounded up and requests above 1.5x are rounded down, preventing out-of-range errors.
* **mDNS / device discovery** — The receiver now broadcasts via mDNS so it is discoverable by sender devices on the local network.
* **CastLab compatibility** — Resolved a compatibility issue with the Android CastLab app. CastLab support is currently in **beta**.

### Known Issues

* **Stream cancellation freeze** — Cancelling a stream occasionally causes Kodi to freeze. Clearing the queue or cache restores a stable connection, but the root cause has not yet been investigated.
* **Audio/video sync drift** — After a long pause or during extended playback (roughly 40+ minutes), the audio stream can begin skipping seconds intermittently, breaking A/V sync.
* **No playback position sync** — Streams always start from the beginning; the current playback position of the sending device is not synced to Kodi.

## Configuration

Enable the following Kodi setting for best results:

* **Settings → Player → Videos → Sync playback to display** — set to **On**

## Pre-Release Available

A testing version of the addon is available: [v0.0.1-alpha](https://github.com/wolf3592/kodi-fcast-receiver/releases/tag/v0.0.1-alpha).

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
