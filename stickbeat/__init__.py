"""StickBeat – Spotify-Links lokal als getaggte MP3s speichern und auf den USB-Stick bringen."""

import os

__version__ = "1.1.0"
APP_NAME = "StickBeat"
# 5000 passt zur Redirect-URI in der Spotify-App; anderer Port nur zum Testen (start.bat --port 5010)
PORT = int(os.environ.get("STICKBEAT_PORT") or 5000)
