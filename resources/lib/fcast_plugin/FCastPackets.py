from datetime import datetime, timezone
from enum import Enum
from typing import Optional

class PlayBackState(int, Enum):
    IDLE = 0
    PLAYING = 1
    PAUSED = 2

class PlayMessage:
    def __init__(self,
        container: str,
        url: Optional[str] = None,
        time: Optional[float] = None,
        content: Optional[str] = None,
        speed: float = 1.0,
        volume: Optional[float] = None,
        headers = None,
        metadata = None
    ) -> None:
        self.container = container
        self.url = url
        self.content = content
        self.time = time
        self.speed = speed
        self.volume = volume
        self.headers = headers
        self.metadata = metadata

class SeekMessage:
    def __init__(self, time: int) -> None:
        self.time = time

class PlayBackUpdateMessage:
    def __init__(self,
        time: int,
        state: PlayBackState,
        speed: float = 1.0,
        duration: Optional[float] = None,
        generationTime: Optional[int] = None
    ) -> None:
        self.time = time
        self.duration = duration
        self.speed = speed
        self.state = state
        self.generationTime = generationTime if generationTime else int(datetime.now(timezone.utc).timestamp() * 1000)

class VolumeUpdateMessage:
    def __init__(self,
        volume: float,
        generationTime: Optional[int] = None
    ) -> None:
        self.volume = volume
        self.generationTime = generationTime if generationTime else int(datetime.now(timezone.utc).timestamp() * 1000)

class SetVolumeMessage:
    def __init__(self, volume: float) -> None:
        self.volume = volume

class SetSpeedMessage:
    def __init__(self, speed: float = 1.0) -> None:
        self.speed = speed

class PlaybackErrorMessage:
    def __init__(self, message: str) -> None:
        self.message = message

class VersionMessage:
    def __init__(self, version: int) -> None:
        self.version = version

# --- Protocol v3 ------------------------------------------------------------

class InitialSenderMessage:
    def __init__(self,
        displayName: Optional[str] = None,
        appName: Optional[str] = None,
        appVersion: Optional[str] = None
    ) -> None:
        self.displayName = displayName
        self.appName = appName
        self.appVersion = appVersion

class InitialReceiverMessage:
    def __init__(self,
        displayName: Optional[str] = None,
        appName: Optional[str] = None,
        appVersion: Optional[str] = None,
        playData: Optional[PlayMessage] = None
    ) -> None:
        self.displayName = displayName
        self.appName = appName
        self.appVersion = appVersion
        self.playData = playData

class PlayUpdateMessage:
    def __init__(self,
        playData: Optional[PlayMessage] = None,
        generationTime: Optional[int] = None
    ) -> None:
        self.playData = playData
        self.generationTime = generationTime if generationTime else int(datetime.now(timezone.utc).timestamp() * 1000)

class SetPlaylistItemMessage:
    def __init__(self, itemIndex: int) -> None:
        self.itemIndex = itemIndex

def summarize_play_message(message: Optional[PlayMessage]) -> Optional[PlayMessage]:
    """Copy a PlayMessage for echoing back to senders, without its content.

    `content` carries an entire inline manifest. Echoing it in Initial or
    PlayUpdate can push the packet past the protocol's 32KB ceiling, and the
    sender already knows what it sent us. The URL and container are what the
    other senders actually need to display what is playing.
    """
    if message is None:
        return None

    return PlayMessage(
        container=message.container,
        url=message.url,
        time=message.time,
        speed=message.speed,
        volume=message.volume,
        headers=message.headers,
        metadata=message.metadata,
    )
