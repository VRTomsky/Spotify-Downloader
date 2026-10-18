"""Einstellungen als JSON-Datei im Datenordner."""

from __future__ import annotations

import json
import threading
from typing import Any

from .util import data_dir, default_music_dir, log

DEFAULTS: dict[str, Any] = {
    "output_dir": str(default_music_dir()),
    "bitrate": 320,                # kbps der MP3s
    "cover_size": 1000,            # Kantenlänge des eingebetteten Covers in Pixel
    "playlist_layout": "playlist",  # "playlist" = eigener Ordner in Playlist-Reihenfolge, "artist" = Interpret/Album
    "ascii_names": False,          # Umlaute in Dateinamen ersetzen (für sehr alte Geräte)
    "spotify_client_id": "",
    "cookies_file": "",            # optional: cookies.txt für YouTube Music Premium (bis 256 kbps)
    "usb_layout": "flat",          # "flat" = ein Ordner pro Album auf dem Stick, "tree" = wie Bibliothek
}

ALLOWED_BITRATES = (128, 192, 256, 320)
ALLOWED_COVER_SIZES = (500, 800, 1000)

_lock = threading.Lock()
_path = data_dir() / "settings.json"


def load() -> dict[str, Any]:
    with _lock:
        values = dict(DEFAULTS)
        if _path.exists():
            try:
                values.update(json.loads(_path.read_text("utf-8")))
            except (OSError, ValueError) as exc:
                log.warning("Einstellungen nicht lesbar, nehme Standardwerte: %s", exc)
        return values


def save(changes: dict[str, Any]) -> dict[str, Any]:
    values = load()
    for key, value in changes.items():
        if key not in DEFAULTS:
            continue
        values[key] = _clean(key, value, values[key])
    with _lock:
        _path.write_text(json.dumps(values, indent=2, ensure_ascii=False), "utf-8")
    return values


def _clean(key: str, value: Any, old: Any) -> Any:
    if key == "bitrate":
        value = int(value)
        return value if value in ALLOWED_BITRATES else old
    if key == "cover_size":
        value = int(value)
        return value if value in ALLOWED_COVER_SIZES else old
    if key == "playlist_layout":
        return value if value in ("playlist", "artist") else old
    if key == "usb_layout":
        return value if value in ("flat", "tree") else old
    if key == "ascii_names":
        return bool(value)
    return str(value or "").strip()
