"""Startet den lokalen Server und öffnet den Browser."""

from __future__ import annotations

import socket
import sys
import threading
import webbrowser

from . import PORT, __version__
from .util import log, setup_logging

URL = f"http://127.0.0.1:{PORT}/"


def _port_free() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", PORT))
            return True
        except OSError:
            return False


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # Umlaute im Konsolenfenster
    setup_logging()
    open_browser = "--no-browser" not in sys.argv
    if not _port_free():
        print(f"\n  Port {PORT} ist belegt – läuft StickBeat schon in einem anderen Fenster?")
        print(f"  Öffne {URL} im Browser.\n")
        if open_browser:
            webbrowser.open(URL)
        return

    from flask import cli

    from .server import create_app

    cli.show_server_banner = lambda *args, **kwargs: None  # Flask-Entwicklerhinweis ausblenden
    app = create_app()
    print("\n  ========================================================")
    print(f"   StickBeat {__version__} läuft:  {URL}")
    print("   Dieses Fenster offen lassen. Schließen = StickBeat beenden.")
    print("  ========================================================\n")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, [URL]).start()
    try:
        app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False, use_reloader=False)
    finally:
        log.info("StickBeat beendet.")


if __name__ == "__main__":
    main()
