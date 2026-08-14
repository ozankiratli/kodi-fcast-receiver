class Addon:
    _info = {
        "name": "FCast Receiver",
        "version": "0.0.0-test",
        "id": "service.fcast.receiver",
    }
    def getAddonInfo(self, key):
        return self._info[key]
