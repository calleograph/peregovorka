#!/usr/bin/env python3
"""Минимальный «хозяин» значков в трее для испытаний (org.kde.StatusNotifierWatcher, как в KDE и в GNOME с AppIndicator):
принимает регистрацию значков StatusNotifierItem и пишет их в файл (по строке на значок). Сам значки не рисует."""
import sys
import dbus, dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

LOG = sys.argv[1]
IFACE = "org.kde.StatusNotifierWatcher"


class Watcher(dbus.service.Object):
    def __init__(self, bus):
        self.items = []
        dbus.service.Object.__init__(self, dbus.service.BusName(IFACE, bus), "/StatusNotifierWatcher")

    @dbus.service.method(IFACE, in_signature="s", sender_keyword="sender")
    def RegisterStatusNotifierItem(self, service, sender=None):
        item = service if service.startswith(":") or "." in service else sender
        path = service if service.startswith("/") else "/StatusNotifierItem"
        self.items.append(f"{sender} {path}")
        open(LOG, "a").write(f"{sender} {path}\n")
        self.StatusNotifierItemRegistered(f"{sender}{path}")

    @dbus.service.method(IFACE, in_signature="s")
    def RegisterStatusNotifierHost(self, service):
        pass

    @dbus.service.signal(IFACE, signature="s")
    def StatusNotifierItemRegistered(self, item):
        pass

    @dbus.service.method(dbus.PROPERTIES_IFACE, in_signature="ss", out_signature="v")
    def Get(self, iface, prop):
        return {"IsStatusNotifierHostRegistered": True, "ProtocolVersion": 0, "RegisteredStatusNotifierItems": dbus.Array(self.items, signature="s")}[prop]

    @dbus.service.method(dbus.PROPERTIES_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, iface):
        return {"IsStatusNotifierHostRegistered": True, "ProtocolVersion": 0, "RegisteredStatusNotifierItems": dbus.Array(self.items, signature="s")}


DBusGMainLoop(set_as_default=True)
Watcher(dbus.SessionBus())
GLib.MainLoop().run()
