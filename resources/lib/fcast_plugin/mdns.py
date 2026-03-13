import dbus
import socket
from .util import log

AVAHI_SERVICE_TYPE = "_fcast._tcp"
AVAHI_PORT = 46899

_group = None

def register():
    global _group
    try:
        service_name = f"Kodi - {socket.gethostname()}"
        bus = dbus.SystemBus()
        server = dbus.Interface(
            bus.get_object("org.freedesktop.Avahi", "/"),
            "org.freedesktop.Avahi.Server"
        )
        _group = dbus.Interface(
            bus.get_object("org.freedesktop.Avahi", server.EntryGroupNew()),
            "org.freedesktop.Avahi.EntryGroup"
        )
        _group.AddService(
            -1,
            -1,
            dbus.UInt32(0),
            service_name,
            AVAHI_SERVICE_TYPE,
            "",
            "",
            dbus.UInt16(AVAHI_PORT),
            [],
        )
        _group.Commit()
        log(f"mDNS: Registered '{service_name}' as {AVAHI_SERVICE_TYPE}:{AVAHI_PORT}")
    except Exception as e:
        log(f"mDNS: Failed to register: {e}")

def unregister():
    global _group
    try:
        if _group:
            _group.Reset()
            _group = None
            log("mDNS: Unregistered service")
    except Exception as e:
        log(f"mDNS: Failed to unregister: {e}")
