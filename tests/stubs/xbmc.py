"""Minimal stand-ins for the Kodi modules, so the add-on can be tested off-device."""

LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR, LOGFATAL = 0, 1, 2, 3, 4

messages = []

def log(msg, level=LOGDEBUG):
    messages.append((level, msg))

def executebuiltin(command, wait=False):
    pass

def sleep(millis):
    pass

def executeJSONRPC(request):
    return '{"jsonrpc":"2.0","id":1,"result":[]}'

class Monitor:
    def abortRequested(self):
        return False
    def waitForAbort(self, timeout=0.0):
        return True

class Player:
    def __init__(self, *a, **k):
        self.seeked_to = []
        self.playing = False
        self.time = 0.0
        self.total_time = 0.0

    def isPlaying(self):
        return self.playing

    def getTime(self):
        return self.time

    def getTotalTime(self):
        return self.total_time

    def seekTime(self, seconds):
        self.seeked_to.append(seconds)
        self.time = seconds

    def pause(self):
        pass

    def play(self, *a, **k):
        self.playing = True
