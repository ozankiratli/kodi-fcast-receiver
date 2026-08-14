import xbmc

from .FCastSession import FCastSession, PlayBackUpdateMessage, PlayBackState, PlayMessage, PlaybackErrorMessage, OpCode
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
            self.seekTime(self.start_time)
            self.start_time = 0.0
        self.onPlayBackTimeChanged()

    # These three callbacks used to force PlayerControl(Stop) and send an
    # OpCode.STOP back to the sender. That was a workaround for the player
    # hanging and taking Kodi's UI with it, and it did make the player quit
    # cleanly - but it also cancels the remaining items of a queued playlist,
    # which is why playlists cannot work while it is in place.
    #
    # The old lines are left commented at each site deliberately. Several
    # likely causes of those hangs have since been fixed (packet reassembly
    # corrupting the stream, and leaked connection threads that kept dead
    # sessions in self.sessions while this ran at 20Hz), but that has not been
    # confirmed on a device. If freezes reappear, restore these first.

    def onPlayBackStopped(self) -> None:
        # Playback was ended deliberately, by a sender or from the Kodi UI.
        # Nothing further should start, so a queued playlist is abandoned here
        # rather than advanced.
        # xbmc.executebuiltin('PlayerControl(Stop)')
        self.report_idle()

    def onPlayBackPaused(self) -> None:
        self.is_paused = True
        self.onPlayBackTimeChanged()

    def onPlayBackResumed(self) -> None:
        self.is_paused = False

    def onPlayBackEnded(self) -> None:
        # The item played to its end on its own. This is where a playlist
        # advances to the next item.
        # xbmc.executebuiltin('PlayerControl(Stop)')
        self.report_idle()

    def onPlayBackError(self) -> None:
        # xbmc.executebuiltin('PlayerControl(Stop)')
        for session in list(self.sessions):
            session.send_playback_error(PlaybackErrorMessage("Playback failed"))
        self.report_idle()

    def report_idle(self) -> None:
        """Tell every sender that playback is over.

        Idle is the only state available for this: FCast defines just Idle,
        Playing and Paused, with no separate stopped state. The previous
        `session.sendOpCode(OpCode.STOP)` did the job by accident - Stop is
        defined as sender-to-receiver only, so what a sender made of receiving
        one was undefined.

        Built from the last known position, because getTime() and
        getTotalTime() raise once the player has torn down.
        """
        self.is_paused = False
        message = PlayBackUpdateMessage(
            max(self.prev_time, 0),
            PlayBackState.IDLE,
            speed=self.playback_speed,
        )
        for session in list(self.sessions):
            session.send_playback_update(message)
        self.prev_time = -1

    def onPlayBackSpeedChanged(self, speed: float) -> None:
        self.playback_speed = speed

    def onPlayBackTimeChanged(self) -> None:
        self.prev_time = int(self.getTime())
        duration=int(self.getTotalTime())
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