"""Ordnerstruktur der Bibliothek und Import gekaufter Musikdateien."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3

from . import enrich
from .util import safe_name

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".aif", ".aiff", ".wma", ".alac"}
COVER_FILES = ("cover.jpg", "folder.jpg", "front.jpg", "cover.png", "folder.png", "front.png", "albumart.jpg")
MAX_PATH = 240


# --- Zielpfade ------------------------------------------------------------------------------

def track_dest(root: str | Path, track: dict, collection: dict, settings: dict, total: int = 0) -> Path:
    """Interpret/Album/01 - Titel.mp3 – oder bei Playlists Playlists/Name/001 - Interpret - Titel.mp3."""
    ascii_only = bool(settings.get("ascii_names"))

    def name(text: Any, max_len: int) -> str:
        return safe_name(text, max_len, ascii_only)

    root = Path(root)
    title = track.get("title") or "Unbekannter Titel"
    artist = (track.get("artists") or ["Unbekannt"])[0]
    index = track.get("playlist_index") or 0

    if collection.get("kind") in ("playlist", "liked") and settings.get("playlist_layout") == "playlist" and index:
        folder = root / "Playlists" / name(collection.get("name") or "Playlist", 60)
        width = max(2, len(str(total or index)))
        stem = f"{index:0{width}d} - {name(artist, 40)} - {name(title, 70)}"
    else:
        folder = root / name(track.get("album_artist") or artist, 60) / name(track.get("album") or "Unbekanntes Album", 70)
        number = track.get("track_no") or 0
        if number:
            prefix = f"{track.get('disc_no') or 1}-{number:02d}" if (track.get("disc_total") or 1) > 1 else f"{number:02d}"
            stem = f"{prefix} - {name(title, 90)}"
        else:
            stem = name(title, 90)

    path = folder / f"{stem}.mp3"
    if len(str(path)) > MAX_PATH:  # Windows-Pfadlänge
        cut = max(20, len(stem) - (len(str(path)) - MAX_PATH))
        path = folder / f"{stem[:cut].rstrip('. ')}.mp3"
    return path


# --- Bibliothek für den USB-Tab -----------------------------------------------------------------

def library_folders(root: str | Path) -> list[dict]:
    root = Path(root)
    if not root.is_dir():
        return []
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        mp3s = [f for f in filenames if f.lower().endswith(".mp3")]
        if not mp3s:
            continue
        rel = Path(dirpath).relative_to(root)
        size = sum((Path(dirpath) / f).stat().st_size for f in mp3s)
        result.append({
            "rel": rel.as_posix(),
            "label": " · ".join(rel.parts) if rel.parts else "(Hauptordner)",
            "kind": "playlist" if rel.parts and rel.parts[0] == "Playlists" else "album",
            "files": len(mp3s),
            "size": size,
        })
    return sorted(result, key=lambda f: f["rel"].casefold())


# --- Import gekaufter Dateien ---------------------------------------------------------------------

def find_audio(folder: str | Path) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    files = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXT]
    return sorted(files, key=lambda p: str(p).casefold())


_FILENAME = re.compile(r"^(?:(?P<no>\d{1,3})[\s._-]+)?(?:(?P<artist>.+?)\s+-\s+)?(?P<title>.+)$")


def read_file(path: Path, import_root: Path | None = None) -> dict[str, Any]:
    """Liest vorhandene Tags; fehlende Felder kommen aus Dateiname und Ordnername."""
    try:
        audio = mutagen.File(path, easy=True)
    except mutagen.MutagenError:
        audio = None
    tags = (audio.tags if audio is not None else None) or {}

    def tag(key: str) -> str:
        try:
            values = tags.get(key) or [""]
        except (KeyError, ValueError):
            values = [""]
        return str(values[0]).strip()

    def number(text: str) -> tuple[int, int]:
        parts = (text or "").split("/")
        try:
            first = int(parts[0]) if parts[0].strip() else 0
        except ValueError:
            first = 0
        try:
            second = int(parts[1]) if len(parts) > 1 and parts[1].strip() else 0
        except ValueError:
            second = 0
        return first, second

    track_no, track_total = number(tag("tracknumber"))
    disc_no, disc_total = number(tag("discnumber"))
    parsed = _FILENAME.match(path.stem)
    title = tag("title") or (parsed.group("title") if parsed else path.stem)
    artist = tag("artist") or ((parsed.group("artist") or "") if parsed else "")
    if not track_no and parsed and parsed.group("no"):
        track_no = int(parsed.group("no"))
    album = tag("album")
    if not album and import_root is not None and path.parent != import_root:
        album = path.parent.name  # gekaufte Alben liegen meist in einem eigenen Ordner
    return {
        "spotify_id": None,
        "title": title.strip(),
        "artists": [a.strip() for a in re.split(r";|/|, ", artist) if a.strip()] or ["Unbekannt"],
        "album": album,
        "album_artist": tag("albumartist") or (artist.split(",")[0].strip() if artist else ""),
        "track_no": track_no, "track_total": track_total, "disc_no": disc_no or 1, "disc_total": disc_total or 1,
        "date": tag("date") or tag("originaldate"), "genre": tag("genre"), "isrc": tag("isrc") or None,
        "duration": float(getattr(getattr(audio, "info", None), "length", 0) or 0),
        "cover_url": None, "source_file": str(path),
    }


def embedded_cover(path: Path) -> bytes | None:
    """Vorhandenes Cover: in der Datei eingebettet oder als cover.jpg/folder.jpg daneben."""
    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            pics = ID3(path).getall("APIC")
            front = [p for p in pics if p.type == 3] or pics
            if front:
                return front[0].data
        elif suffix == ".flac":
            pics = FLAC(path).pictures
            front = [p for p in pics if p.type == 3] or pics
            if front:
                return front[0].data
        else:
            audio = mutagen.File(path)
            covr = (audio.tags or {}).get("covr") if audio is not None else None
            if covr:
                return bytes(covr[0])
    except (mutagen.MutagenError, OSError, KeyError, ValueError):
        pass
    for name in COVER_FILES:
        candidate = path.parent / name
        if candidate.exists():
            return candidate.read_bytes()
    return None


def prepare_import(track: dict, cover_size: int) -> bytes | None:
    """Ergänzt Lücken über Deezer und liefert das Cover (eigenes zuerst)."""
    if not track.get("album") or not track.get("date") or track["artists"] == ["Unbekannt"]:
        enrich.fill_missing(track)
    if not track.get("album_artist"):
        track["album_artist"] = track["artists"][0]
    raw = embedded_cover(Path(track["source_file"]))
    if raw:
        try:
            return enrich.prepare_cover(raw, cover_size)
        except OSError:
            pass
    return enrich.fetch_cover(track, cover_size)
