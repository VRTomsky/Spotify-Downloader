"""Kleine Helfer: Datenordner, Logging, FAT32-sichere Dateinamen."""

from __future__ import annotations

import logging
import os
import re
import shutil
import unicodedata
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import APP_NAME

log = logging.getLogger("stickbeat")


def data_dir() -> Path:
    """Ordner für Einstellungen, Logs, Spotify-Login und Zwischendateien."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def temp_dir() -> Path:
    path = data_dir() / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_file() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path / "stickbeat.log"


def default_music_dir() -> Path:
    music = Path.home() / "Music"
    return (music if music.is_dir() else Path.home()) / APP_NAME


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    file_handler = RotatingFileHandler(log_file(), maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
    log.setLevel(logging.INFO)
    log.addHandler(file_handler)
    log.addHandler(console)
    # Werkzeug-Zugriffslog (jede Anfrage) würde das Konsolenfenster fluten
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def find_tool(name: str) -> str | None:
    return shutil.which(name)


# --- Dateinamen ----------------------------------------------------------------

# Zeichen, die FAT32/Windows in Datei- und Ordnernamen nicht erlauben
_REPLACE = {":": " -", "/": "-", "\\": "-", "|": "-", "?": "", "*": "", "<": "(", ">": ")", '"': "'"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_GERMAN = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def to_ascii(text: str) -> str:
    text = text.translate(_GERMAN)
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii")


def safe_name(text: str | None, max_len: int = 80, ascii_only: bool = False) -> str:
    """Macht aus beliebigem Text einen Datei-/Ordnernamen, den FAT32 und Windows akzeptieren."""
    text = unicodedata.normalize("NFC", str(text or "")).strip()
    if ascii_only:
        text = to_ascii(text)
    for bad, good in _REPLACE.items():
        text = text.replace(bad, good)
    text = _CONTROL.sub("", text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(". ")
    if not text:
        text = "Unbekannt"
    if text.split(".")[0].upper() in _RESERVED:
        text = "_" + text
    if len(text) > max_len:
        text = text[:max_len].rstrip(". ")
    return text


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit in ("B", "KB") else f"{num:.1f} {unit}".replace(".", ",")
        num /= 1024
    return f"{num:.1f} TB"
