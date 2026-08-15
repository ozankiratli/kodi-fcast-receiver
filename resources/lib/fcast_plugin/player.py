import xbmc

from .FCastSession import FCastSession, PlayBackUpdateMessage, PlayBackState, PlayMessage,OpCode
from .util import log

from typing import List

class FCastPlayer(xbmc.Player):
    playback_speed: float = 1.0
    sessions: List[FCastSession]
    is_paused: bool = False
    # Used to perform time updates
    prev_time: int = -1
    # Fallback only. main assigns the sender's PlayMessage.time to the instance
    # before every cast, which shadows this. It matters because Kodi calls our
    # callbacks for anything it plays, including playback we did not start, and
    # onAVStarted would otherwise raise AttributeError.
    start_time: float = 0.0

    def __init__(self, sessions: List[FCastSession]):
        self.sessions = sessions
        super().__init__()

    def doPause(self) -> None:
        if not self.is_paused:
            self.is_paused = True
            self.pause()

    def doResume(self) -> None:
        if self.is_paused:
            self.is_paused = False
            self.pause()

    def onAVStarted(self) -> None:
        log("Playback started")
        self.is_paused = False
        if self.start_time > 0.0:
            log(f"Seeking to start time {self.start_time}")
            try:
                self.seekTime(self.start_time)
            except Exception as e:
                log(f"Could not seek to start time: {e}")
            self.start_time = 0.0
        self.onPlayBackTimeChanged()

    def onPlayBackStopped(self) -> None:
        xbmc.executebuiltin('PlayerControl(Stop)')
        self.onPlayBackEnded()

    def onPlayBackPaused(self) -> None:
        self.is_paused = True
        self.onPlayBackTimeChanged()

    def onPlayBackResumed(self) -> None:
        self.is_paused = False

    def onPlayBackEnded(self) -> None:
        xbmc.executebuiltin('PlayerControl(Stop)')
        for session in self.sessions:
            session.sendOpCode(opcode=OpCode.STOP)

    def onPlayBackError(self) -> None:
        xbmc.executebuiltin('PlayerControl(Stop)')
        self.onPlayBackEnded()

    def onPlayBackSpeedChanged(self, speed: float) -> None:
        self.playback_speed = speed

    def onPlayBackTimeChanged(self) -> None:
        # getTime() raises "Kodi is not playing any media file" rather than
        # returning anything when the player has moved on. Kodi calls these
        # callbacks for everything it plays, not only what we cast, and with
        # gapless audio onAVStarted can arrive after playback has already
        # advanced past the item it was announcing.
        try:
            self.prev_time = int(self.getTime())
            duration = int(self.getTotalTime())
        except Exception as e:
            log(f"Skipping playback update, player is not ready: {e}")
            return
        pb_message = PlayBackUpdateMessage(
            self.prev_time,
            PlayBackState.PAUSED if self.is_paused else PlayBackState.PLAYING,
            speed=self.playback_speed,
            duration=duration
        )
        for session in self.sessions:
            session.send_playback_update(pb_message)

    def addSession(self, session: FCastSession):
        self.sessions.append(session)

    def removeSession(self, session: FCastSession):
        self.sessions.remove(session)