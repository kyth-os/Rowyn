"""Capture the desktop through xdg-desktop-portal.

KWin's own ScreenShot2 API answers NoAuthorized for an unprivileged process, so
the capture goes through org.freedesktop.portal.Screenshot: the portal asks the
compositor on the session's behalf and reports a file URI. The Request.Response
signal can arrive after the call returns, so this waits for it instead of
assuming the file exists.

Usage: portal-shot.py OUT.png
"""
import os
import shutil
import sys

from gi.repository import Gio, GLib


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: portal-shot.py OUT.png", file=sys.stderr)
        return 2
    out = sys.argv[1]
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    token = f"rowyn_shot_{os.getpid()}"
    options = {
        "handle_token": GLib.Variant("s", token),
        "interactive": GLib.Variant("b", False),
        "modal": GLib.Variant("b", False),
    }
    try:
        reply = bus.call_sync(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Screenshot",
            "Screenshot",
            GLib.Variant("(sa{sv})", ("", options)),
            GLib.VariantType("(o)"),
            Gio.DBusCallFlags.NONE,
            10000,
            None,
        )
    except GLib.Error as error:
        print(f"screenshot request failed: {error.message}", file=sys.stderr)
        return 1
    handle = reply.unpack()[0]
    loop = GLib.MainLoop()
    status = {"code": 1}

    def on_response(_conn, _sender, _path, _iface, _signal, params):
        code, results = params.unpack()
        if code == 0 and "uri" in results:
            source = GLib.filename_from_uri(results["uri"])[0]
            shutil.copyfile(source, out)
            print(f"saved: {out}")
            status["code"] = 0
        else:
            print(f"screenshot response: {code} {results}", file=sys.stderr)
        loop.quit()

    bus.signal_subscribe(
        "org.freedesktop.portal.Desktop",
        "org.freedesktop.portal.Request",
        "Response",
        handle,
        None,
        Gio.DBusSignalFlags.NONE,
        on_response,
    )

    def on_timeout():
        print("timed out waiting for the portal screenshot", file=sys.stderr)
        loop.quit()
        return False

    GLib.timeout_add_seconds(25, on_timeout)
    loop.run()
    return status["code"]


if __name__ == "__main__":
    raise SystemExit(main())
