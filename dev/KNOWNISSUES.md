# Known issues

Things that are wrong and not yet fixed, with what is actually established about each one separated from what is guessed. An entry leaves here when it is fixed, or when it is understood well enough to be closed as not ours.

Last reviewed 2026-09-20, against 0.9.9~pre.

---

## Kodi's whole interface freezes when a cast stream stalls

**What happens.** A sender changes network mid-stream -- Wi-Fi to cellular, or between access points. The stream hangs, which is expected. Kodi's entire interface then becomes unresponsive for an undetermined period, from a few seconds to a few minutes, and Stop does not stop the stream during it. It comes back on its own.

**Hard to reproduce on purpose.** Observed on OSMC on a Vero V, Kodi 21.3.

**What has been done.** Work on 2026-09-20 (commit `9bf5623`) found and fixed several real defects in this exact path: a sender that changed network was never detected at all, so its connection, thread and place in the broadcast list survived for the rest of the Kodi session; partial socket writes were being dropped, truncating packets, which only happens when a peer stops reading; any send error killed the session without closing the socket or ending the thread; and `accept()` could block the listen loop for a minute when a pending connection was reset between `select()` and `accept()`. The add-on now also stops playback itself when the sender that was serving the stream disappears, so the box recovers without waiting for someone to press Stop.

**It did not fix the freeze.** That is the honest state of it.

**What is not established.** No mechanism has been found by which this add-on can freeze Kodi's whole interface. A Python service add-on's threads are decoupled from Kodi's application thread, and the thing known to block that thread for seconds to minutes is Kodi's own player teardown waiting on a network read that will not return. That points away from us, but it has not been demonstrated either way.

There is also an unresolved contradiction in the report: if the media is a remote CDN URL, the sender changing network should not stall Kodi's read at all. Either the URL is not what it appears to be -- some senders proxy the stream through the sending device, which would make the source the sender's own address -- or the stall has a different cause entirely.

**What to capture next time it happens.** The add-on now says what it noticed and when, so the first question is whether it noticed anything at all. Look for these in `kodi.log`:

    Session with <ip> ended: ...
    Connection from <ip> lost: ...
    Stopping playback: <ip> was serving it and is gone

If none of them appear anywhere near the freeze, the add-on's connection handling was not involved and the cause is on Kodi's side of the line. If they do appear, note how long after the network change, and whether the freeze started before or after them.

Also worth having: the URL Kodi actually opened, which Kodi logs itself when it opens a stream. That settles the contradiction above.

---

## One sender disconnecting stops another sender's stream

**What happens.** Sender A starts a stream. While it is playing, sender B starts another; A's stream is terminated on the receiver and B's starts, which is correct. A's connection is still open. When A then closes its connection, B's stream stops too.

**What is established.** The receiver has no notion of which sender owns what is playing, for the purpose of control messages. Every handler in `main.py` -- `handle_stop`, `handle_pause`, `handle_resume`, `handle_seek`, `handle_speed`, `handle_set_playlist_item` -- takes the session the message arrived on and then ignores it, acting on whatever is playing. So a `Stop` from A stops B's stream, and the same is true of pause, resume and seek.

**The likely mechanism**, not yet confirmed on a device: senders send a `Stop` as they tear their session down, and the receiver acts on it. That would explain the symptom exactly, and it means "A kills its connection" is really "A sends Stop, then closes".

**What has been ruled out.** The connection-loss handling added in `9bf5623` is not the cause. `on_sender_lost` stops playback only when the departing sender was also serving the media, which it decides by comparing the address the connection came from against the host in the URL now playing. With B's stream on screen, a disconnect from A does not match and nothing is stopped. `test_one_sender_leaving_does_not_stop_another_senders_stream` in `dev/tests/test_connection.py` pins that down.

**Why it is not a one-line fix.** Protocol v3 deliberately supports several senders at once: `PlayUpdate` exists so every connected remote shows the same thing, which implies any of them may control it. So "only the owner may send Stop" is not obviously right, and a rule that is too strict breaks the case where someone picks up a second phone to pause the film.

A shape worth considering: track the session that started what is playing, honor explicit control messages from any sender as now, but ignore a `Stop` that arrives from a session which is closing and does not own the current playback. That distinguishes a deliberate stop from a teardown artifact. It needs the disconnect and the `Stop` to be correlated in time, which means the session has to know it is on its way out.

**What to capture.** Run `dev/tools/fcast_proxy.py` between sender A and the receiver and watch what A sends as it disconnects. If a `Stop` goes out, the mechanism above is confirmed and the fix follows from it.

---

## Sync playback to display: speed control and sync drift are the same switch

**What happens.** With Settings > Player > Videos > Sync playback to display turned on, audio and video drift apart over a long playback, often far enough to notice somewhere after the forty minute mark. With it off there is no drift, and a sender's speed requests do nothing at all. There is no position of that switch that gives both.

**What is established.** The drift belongs to Kodi's handling of that setting, not to this add-on. It shows up with any add-on playing adaptive streams, FCast or not -- xbmc/xbmc#22625 is the upstream issue the README has cited since before the setting was identified as the lever. Nothing in the receiver needs the setting except `handle_speed`, which applies a sender's request with the JSON-RPC `Player.SetTempo` clamped to 0.8x to 1.5x, and that call does nothing unless the display clock is in use.

Kodi ships the setting off: a guisettings dump taken from the Vero V has `videoplayer.usedisplayasclock` set to `false` and marked as sitting at its default. So nobody meets the drift by accident -- they meet it after being told to turn the setting on, which is what the README and the website told them to do until this was rewritten on 2026-09-20.

**The position taken.** Present it as the user's choice rather than as a setting to enable, and say plainly what each side of it costs. Off is the better default, because playback that stays in sync matters to everyone and speed control matters to the few people who use it.

**What is not established.** Whether Kodi offers any route to fine-grained playback speed that does not go through the display clock. Kodi's other speed control, `Player.SetSpeed`, is understood to be trick-play -- discrete steps for fast forward and rewind, without normal audio -- and so not a substitute for 0.8x to 1.5x, but that has not been checked against Kodi's API documentation or on a device. If there is such a route, this entry closes and the trade-off disappears.

**What is wrong regardless of the answer.** When the setting is off, a speed request silently does nothing. `handle_speed` logs the JSON-RPC response and never looks at it, so neither the user nor the sender is told that the speed they asked for is not happening. Checking that response, and saying so, is worth doing whichever way the question above falls. The setting's own state is readable over JSON-RPC as `videoplayer.usedisplayasclock`, so the message can name exactly what to turn on.
