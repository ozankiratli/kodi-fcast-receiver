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
        pass
