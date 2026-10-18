"""USB-Stick: Laufwerke finden, Ordner in fester Reihenfolge kopieren, Inhaltsliste schreiben.

Viele MP3-Player (auch der DJP-106SD) spielen Ordner und Titel in der Reihenfolge, in der
sie auf den Stick geschrieben wurden – nicht alphabetisch. Deshalb wird hier streng
nacheinander und sortiert kopiert, ohne Umbenennen hinterher.
"""

from __future__ import annotations

import os
import shutil
import string
import sys
import time
from pathlib import Path
from typing import Callable

from .util import human_size, safe_name

SKIP_DIRS = {"SYSTEM VOLUME INFORMATION", "$RECYCLE.BIN", "RECYCLER"}
INDEX_FILE = "INHALT.txt"


def list_drives() -> list[dict]:
    """Wechseldatenträger (USB-Sticks, SD-Karten) mit Dateisystem und freiem Platz."""
    if sys.platform != "win32":
        return []
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.SetErrorMode(0x0001)  # keine „Kein Datenträger“-Dialoge bei leeren Kartenlesern
    mask = kernel32.GetLogicalDrives()
    drives = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        if kernel32.GetDriveTypeW(root) != 2:  # 2 = DRIVE_REMOVABLE
            continue
        label = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        if not kernel32.GetVolumeInformationW(root, label, 261, None, None, None, fs, 261):
            continue
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        drives.append({
            "root": root, "letter": letter, "label": label.value or "USB-Stick", "fs": fs.value,
            "total": usage.total, "free": usage.free,
            "free_text": human_size(usage.free), "total_text": human_size(usage.total),
            "fat32": fs.value.upper() == "FAT32",
        })
    return drives


def plan_copy(library_root: str | Path, folders: list[str], layout: str, ascii_only: bool = False) -> list[dict]:
    """Welche Bibliotheksordner wohin auf den Stick kommen (sortiert)."""
    root = Path(library_root).resolve()
    plan = []
    for rel in folders:
        src = (root / rel).resolve()
        if root not in src.parents and src != root:
            continue  # nur Ordner innerhalb der Bibliothek
        if not src.is_dir():
            continue
        files = sorted((f.name for f in src.iterdir() if f.is_file() and f.suffix.lower() == ".mp3"), key=str.casefold)
        if not files:
            continue
        parts = Path(rel).parts
        if layout == "flat":
            label = parts[-1] if parts and parts[0] == "Playlists" else " - ".join(parts)
            target = safe_name(label, 100, ascii_only)
        else:
            target = "/".join(safe_name(p, 80, ascii_only) for p in parts)
        size = sum((src / f).stat().st_size for f in files)
        plan.append({"src": str(src), "target": target, "files": files, "size": size})
    return sorted(plan, key=lambda p: p["target"].casefold())


def run_copy(plan: list[dict], drive_root: str, on_folder: Callable[[int, str, float], None],
             is_cancelled: Callable[[], bool]) -> dict:
    """Kopiert Ordner für Ordner, Datei für Datei. Gleich große vorhandene Dateien werden übersprungen."""
    total = sum(p["size"] for p in plan) or 1
    done = 0
    copied = skipped = 0
    for index, item in enumerate(plan):
        on_folder(index, "kopiert", 0.0)
        dest_dir = Path(drive_root) / Path(item["target"])
        dest_dir.mkdir(parents=True, exist_ok=True)
        folder_done = 0
        for name in item["files"]:
            if is_cancelled():
                on_folder(index, "abgebrochen", folder_done / (item["size"] or 1))
                return {"copied": copied, "skipped": skipped, "cancelled": True}
            src = Path(item["src"]) / name
            dst = dest_dir / name
            size = src.stat().st_size
            if dst.exists() and dst.stat().st_size == size:
                skipped += 1
            else:
                _copy_with_progress(src, dst)
                copied += 1
            folder_done += size
            done += size
            on_folder(index, "kopiert", folder_done / (item["size"] or 1))
        on_folder(index, "fertig", 1.0)
    return {"copied": copied, "skipped": skipped, "cancelled": False, "bytes": total}


def _copy_with_progress(src: Path, dst: Path) -> None:
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        shutil.copyfileobj(fin, fout, 1024 * 1024)
        fout.flush()
        os.fsync(fout.fileno())


def device_index(drive_root: str) -> list[dict]:
    """So nummeriert ein typischer MP3-Player den Stick: Ordner in Schreib-Reihenfolge, Titel darin.

    Auf FAT32 liefert Windows die Einträge in der Reihenfolge auf dem Datenträger (nicht sortiert)
    – genau die Reihenfolge, die einfache Player verwenden.
    """
    folders: list[dict] = []

    def walk(path: Path, rel: str) -> None:
        try:
            entries = list(os.scandir(path))
        except OSError:
            return
        mp3s = [e.name for e in entries if e.is_file() and e.name.lower().endswith(".mp3")]
        if mp3s:
            folders.append({"name": rel or "(Hauptordner)", "files": mp3s})
        for entry in entries:
            if entry.is_dir() and not entry.name.startswith(".") and entry.name.upper() not in SKIP_DIRS:
                walk(Path(entry.path), f"{rel}/{entry.name}" if rel else entry.name)

    walk(Path(drive_root), "")
    running = 0
    for number, folder in enumerate(folders, start=1):
        folder["number"] = number
        tracks = []
        for no, file in enumerate(folder["files"], start=1):
            running += 1
            tracks.append({"no": no, "global": running, "name": Path(file).stem})
        folder["tracks"] = tracks
        del folder["files"]
    return folders


def write_index(drive_root: str, folders: list[dict]) -> Path:
    lines = [
        f"StickBeat – Inhalt von {drive_root}  (Stand {time.strftime('%d.%m.%Y %H:%M')})",
        "Taste FOLDER = nächster Ordner, SKIP +/- = nächster/vorheriger Titel.",
        "Reihenfolge = Reihenfolge auf dem Stick (so zählt der Player).",
        "",
    ]
    for folder in folders:
        lines.append(f"Ordner {folder['number']:02d}  ·  {folder['name']}")
        for track in folder["tracks"]:
            lines.append(f"   {track['no']:03d}  (gesamt {track['global']:03d})  {track['name']}")
        lines.append("")
    path = Path(drive_root) / INDEX_FILE
    path.write_text("\n".join(lines), encoding="utf-8-sig", newline="\r\n")
    return path
