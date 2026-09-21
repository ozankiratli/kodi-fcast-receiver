# Development

Notes for working on this add-on, written for whoever opens the project next -- including the version of you that has been away from it for six months. The README is for people installing the add-on; this is for people changing it.

## The map

`resources/lib/startup.py` is what Kodi launches. It exists only to import `main` and call it inside a `try`, because an exception escaping that file leaves the service silently dead: Kodi reports it once as a `PythonToCppException` and nothing else.

Everything else is under `resources/lib/fcast_plugin/`:

- `main.py` -- the service. Owns the listening socket, one thread per connection, the 20Hz player poll, and every handler a sender's message lands in.
- `FCastSession.py` -- one sender's connection: framing, the opcode table, version negotiation, and the outbound buffer.
- `FCastPackets.py` -- the message types, as plain classes that serialize to the protocol's JSON.
- `player.py` -- the `xbmc.Player` subclass, which is how Kodi reports playback back to us.
- `image_viewer.py` -- cast photos, which never touch the player. See below.
- `image_cache.py` -- downloading a picture before showing it, and keeping the last dozen.
- `playlist.py` -- a queue a sender hands over for the receiver to walk.
- `mdns.py` -- discovery, across the different D-Bus bindings the platforms ship.
- `settings.py` -- reading what the user chose, without ever raising.
- `FCastHTTPServer.py` -- serves an inline DASH manifest when a sender sends one as content rather than a URL.
- `util.py` -- logging, notifications, debounce.

`dev/` is not deployed. Only `addon.xml`, `icon.png`, `LICENSE.txt` and `resources/` are, which is what the `PAYLOAD` variable in the Makefile pins down.

## What Kodi makes hard

These are the constraints that shaped the code. Each one has cost this project a bug already, so changing code near them deserves a second look.

**Kodi's player callbacks fire for everything Kodi plays, not just for what was cast.** `onPlayBackEnded` arrives for a track in the user's own album exactly as it does for our stream. The add-on once answered those with a stop, which killed the track Kodi had just started and cut an album to seconds per song. `FCastPlayer.owns_playback` is the guard, and every callback returns early unless it is set.

**Stopping the player is done with `xbmc.executebuiltin('PlayerControl(Stop)')` on a thread of its own**, never with `player.stop()`. The original code called `player.stop()` directly and it was implicated in mid-stream freezes; commits `4a5acab` and `496c578` are the history. Whatever the mechanism inside Kodi, the shape to keep is: the builtin, from a thread that has nothing else waiting on it.

**A player that has been asked to stop can take a long time about it.** Kodi tears it down on its own thread, and a stream whose source has gone off the network holds that teardown open for as long as the socket takes to give up. Calls into the player queue behind the same lock, so `stop_pending()` in `main.py` keeps the 20Hz poll out of that window.

**Pictures never reach the player.** Handing an image to the video player renders it for a few milliseconds and closes. They go to Kodi's picture viewer through `ShowPicture`, and because `CGUIWindowSlideShow` derives from `CGUIDialog` despite its name, it is `getCurrentWindowDialogId()` that reports `12007`, not `getCurrentWindowId()`. Asking only the window means the viewer always reads as absent.

**Settings are read through a fresh `xbmcaddon.Addon()` every time.** A long-lived instance has been known to keep serving the values it was created with, so a change made in the settings dialog would not take effect until Kodi restarted.

**Sockets are non-blocking, and `send()` is not all-or-nothing.** It writes what fits and reports how much, so the session keeps an outbox and flushes it. A truncated packet desynchronizes the sender exactly the way a truncated read desynchronized us before the reassembly fix.

**Logging below `LOGINFO` is invisible unless the user has turned debug logging on.** Everything the add-on logged was `LOGDEBUG` at one point, which meant a failed download, a viewer that never opened, and discovery that never registered all looked exactly like working. Connections, failures and the version banner log at info or above now; the rest stays at debug.

## Getting a change onto a box

`make deploy KODI_HOST=user@host` copies the payload over with rsync and `--delete`. The delete matters: copying individual files by hand is how a new module ends up importing a symbol from a stale one, which Kodi reports only as a bare `ImportError`.

`KODI_ADDON_DIR` defaults to `~/.kodi/addons/service.fcast.receiver`, which is right for OSMC and for most builds. LibreELEC and CoreELEC ship neither rsync nor git nor make, so use `make deploy-ssh KODI_HOST=root@host` there: it needs only ssh and tar on the far end, stages beside the target and swaps, and leaves the previous install at `.bak`.

With `KODI_HOST` unset it deploys into `KODI_ADDON_DIR` on the local machine, for the case where the repository is cloned on the Kodi box itself. It refuses if that path is the checkout you are standing in.

Either way it deploys the working tree, not a commit and not the built zip. Then restart the service -- disable and enable it in Settings > Add-ons -- or restart Kodi. If anything under `resources/language` changed, Kodi itself has to restart: add-on strings are loaded only at start-up, so settings labels stay blank until it does.

## Reading the log

The add-on prefixes every line with its name, so `grep 'FCast Receiver' kodi.log` is the whole trick. On OSMC the file is `/home/osmc/.kodi/temp/kodi.log`; elsewhere it is under whatever `special://logpath/` maps to, which Kodi prints near the top of the log.

Lines at `LOGDEBUG` only appear if debug logging is on, in Settings > System > Logging. The lines that appear regardless are the version banner at start-up, connections opening and closing, and anything that went wrong.

## Branches and remotes

`main` is the working branch. `v1.0.0` is the release branch and is the one that exists on the forgejo mirror; it trails `main` and is fast-forwarded to it.

There are four remotes: `origin` (GitHub, ozankiratli), `forgejo` (the self-hosted mirror), `upstream` (c4valli, the project this is forked from) and `wolf3592`. Note that `branch.v1.0.0.remote` is `forgejo` while `branch.v1.0.0.pushremote` is `origin`, so a bare `git push` from that branch goes to GitHub. Name the remote when you push it.

## Generated, never committed

`dist/` holds the built zip, `repo/` the static Kodi repository and landing page that CI publishes, `.release/` a worktree that `pages.yml` makes when run locally, and `.tmp/` whatever logs you have pulled off a device. All four are in `.gitignore`. `make clean` removes the first two.
