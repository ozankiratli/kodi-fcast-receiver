import xbmc
import xbmcgui
import xbmcaddon
from threading import Timer

# Retrieve Kodi addon information
addon       = xbmcaddon.Addon()
addonname   = addon.getAddonInfo('name')

def notify(msg, icon=xbmcgui.NOTIFICATION_INFO, timeout=3000, sound=False):
    xbmcgui.Dialog().notification(addonname, msg, icon, timeout, sound)

def log(msg, level=xbmc.LOGDEBUG):
    xbmc.log("%s: %s" % (addonname, msg), level=level)

# Trottle repeated attempts at a function call
def debounce(func, wait):
    timer = [None]
    def debounced(*args, **kwargs):
        if timer[0]:
            timer[0].cancel()
        timer[0] = Timer(wait, func, args=args, kwargs=kwargs)
        timer[0].start()
    return debounced