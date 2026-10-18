"""Startpunkt für start.bat: hält die Pakete aktuell und startet StickBeat.

Nutzt bis zur Installation nur die Standardbibliothek. yt-dlp wird alle zwei Tage
aktualisiert, weil YouTube häufig etwas ändert und alte Versionen dann scheitern.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements.txt"
STAMP = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "StickBeat" / "installiert.json"
UPDATE_EVERY = 2 * 24 * 3600


def pip(*args: str) -> int:
    return subprocess.call([sys.executable, "-m", "pip", "--disable-pip-version-check", *args])


def ensure_packages() -> None:
    wanted = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    try:
        stamp = json.loads(STAMP.read_text("utf-8"))
    except (OSError, ValueError):
        stamp = {}
    if stamp.get("requirements") != wanted:
        print("\n  Installiere benötigte Pakete (einmalig, braucht Internet) ...\n", flush=True)
        if pip("install", "-r", str(REQUIREMENTS)) != 0:
            print("\n  [Fehler] Pakete konnten nicht installiert werden. Internetverbindung prüfen.")
            sys.exit(1)
        stamp = {"requirements": wanted, "updated": time.time()}
    elif time.time() - stamp.get("updated", 0) > UPDATE_EVERY:
        print("  Suche Updates für den YouTube-Download (yt-dlp) ...", flush=True)
        if pip("install", "-q", "-U", "yt-dlp[default]", "ytmusicapi") == 0:
            stamp["updated"] = time.time()
        else:
            print("  Update ging nicht (offline?) – starte mit vorhandener Version.")
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.write_text(json.dumps(stamp), "utf-8")


if __name__ == "__main__":
    ensure_packages()
    sys.path.insert(0, str(HERE))
    from stickbeat.__main__ import main

    main()
