"""Audio holen (yt-dlp), in MP3 umwandeln (FFmpeg) und taggen (mutagen, ID3v2.3 + ID3v1)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import yt_dlp
from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TSRC, TXXX

ProgressFn = Callable[[float], None]
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
ATTEMPTS = 3
_TRANSIENT = re.compile(r"HTTP Error (403|429|5\d\d)|timed out|Connection|reset by peer|IncompleteRead|fragment", re.I)


class Cancelled(Exception):
    pass


class AudioError(Exception):
    pass


class LoginRequired(AudioError):
    """YouTube liefert nur mit Anmeldung (Bot-Sperre der Internet-Adresse oder Altersgrenze)."""

    def __init__(self, message: str, bot: bool):
        super().__init__(message)
        self.bot = bot


class AuthError(AudioError):
    """Die hinterlegte YouTube-Anmeldung (Cookies) ist nicht nutzbar."""


# Browser, aus denen yt-dlp die YouTube-Anmeldung (Cookies) lesen kann: id -> (Anzeige, yt-dlp-Name, Profilordner)
BROWSERS = {
    "firefox": ("Firefox", "firefox", None),
    "brave": ("Brave", "brave", None),
    "edge": ("Microsoft Edge", "edge", None),
    "chrome": ("Google Chrome", "chrome", None),
    "operagx": ("Opera GX", "opera", r"%APPDATA%\Opera Software\Opera GX Stable"),
    "opera": ("Opera", "opera", None),
    "vivaldi": ("Vivaldi", "vivaldi", None),
}
_BROWSER_DIRS = {
    "firefox": r"%APPDATA%\Mozilla\Firefox\Profiles",
    "brave": r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data",
    "edge": r"%LOCALAPPDATA%\Microsoft\Edge\User Data",
    "chrome": r"%LOCALAPPDATA%\Google\Chrome\User Data",
    "operagx": r"%APPDATA%\Opera Software\Opera GX Stable",
    "opera": r"%APPDATA%\Opera Software\Opera Stable",
    "vivaldi": r"%LOCALAPPDATA%\Vivaldi\User Data",
}
_TEST_VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # „Me at the zoo“, 19 Sekunden

# Nach einer Sperre lädt StickBeat eine Weile gleich mit Anmeldung, statt jedes Mal erst ohne
_auth_until = 0.0
AUTH_STICKY = 30 * 60

_SESSION_COOKIES = {"SAPISID", "__Secure-3PAPISID", "LOGIN_INFO"}  # nur vorhanden, wenn wirklich angemeldet
_LOGIN = re.compile(r"sign in to confirm|not a bot|confirm your age|age.restricted|inappropriate for some users", re.I)
_COOKIE_FAIL = re.compile(r"cookie|decrypt|dpapi|keyring|could not copy|could not find .* (profile|browser)", re.I)


def installed_browsers() -> list[dict]:
    return [{"id": key, "label": label, "installed": os.path.isdir(os.path.expandvars(_BROWSER_DIRS[key]))}
            for key, (label, _, _) in BROWSERS.items()]


def auth_options(settings: dict) -> dict:
    """yt-dlp-Optionen für die YouTube-Anmeldung laut Einstellungen (leer = keine)."""
    mode = settings.get("youtube_auth")
    if mode == "file":
        path = settings.get("cookies_file") or ""
        return {"cookiefile": path} if os.path.isfile(path) else {}
    if mode == "browser" and settings.get("browser") in BROWSERS:
        _, name, profile = BROWSERS[settings["browser"]]
        return {"cookiesfrombrowser": (name, os.path.expandvars(profile) if profile else None, None, None)}
    return {}


def js_runtime() -> dict | None:
    """yt-dlp braucht für YouTube eine JavaScript-Laufzeit (Deno oder Node.js)."""
    for name in ("deno", "node", "bun"):
        path = shutil.which(name)
        if path:
            return {name: {"path": path}}
    return None


def ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise AudioError("FFmpeg fehlt. Installieren mit:  winget install Gyan.FFmpeg  – danach StickBeat neu starten.")
    return path


# --- Download ---------------------------------------------------------------------------

def download(url: str, workdir: Path, on_progress: ProgressFn, auth: dict | None = None,
             is_cancelled: Callable[[], bool] = lambda: False) -> tuple[Path, dict]:
    """Lädt die Audiospur. Die Anmeldung wird nur benutzt, wenn YouTube ohne sie sperrt."""
    global _auth_until
    with_auth = bool(auth) and time.time() < _auth_until
    try:
        return _download(url, workdir, on_progress, auth if with_auth else None, is_cancelled)
    except LoginRequired:
        if not auth or with_auth:
            raise
    _auth_until = time.time() + AUTH_STICKY
    return _download(url, workdir, on_progress, auth, is_cancelled)


def _base_options(workdir: Path | None = None) -> dict:
    opts: dict = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "overwrites": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
    }
    if workdir is not None:
        opts["outtmpl"] = str(workdir / "quelle.%(ext)s")
    runtime = js_runtime()
    if runtime:
        opts["js_runtimes"] = runtime
    return opts


def _download(url: str, workdir: Path, on_progress: ProgressFn, auth: dict | None,
              is_cancelled: Callable[[], bool]) -> tuple[Path, dict]:
    def hook(status: dict) -> None:
        if is_cancelled():
            raise Cancelled()
        if status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            if total:
                on_progress(min(1.0, (status.get("downloaded_bytes") or 0) / total))

    opts = _base_options(workdir)
    opts["progress_hooks"] = [hook]
    opts.update(auth or {})

    info: dict = {}
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 – yt-dlp wirft je nach Fehler verschiedene Typen
            inner = (getattr(exc, "exc_info", None) or (None, None))[1]
            if isinstance(inner, Cancelled):
                raise Cancelled() from exc
            # YouTube antwortet ab und zu mit 403 oder bricht ab – ein neuer Anlauf klappt dann meist
            if attempt < ATTEMPTS and _TRANSIENT.search(str(exc)) and not _LOGIN.search(str(exc)):
                for leftover in workdir.glob("quelle.*"):
                    leftover.unlink(missing_ok=True)
                time.sleep(2 * attempt)
                continue
            raise _classify(exc, bool(auth)) from exc

    files = [Path(d["filepath"]) for d in info.get("requested_downloads") or [] if d.get("filepath")]
    files = [f for f in files if f.exists()] or sorted(workdir.glob("quelle.*"))
    if not files:
        raise AudioError("Download lieferte keine Datei.")
    source = {
        "codec": info.get("acodec") or info.get("ext") or "?",
        "abr": info.get("abr") or info.get("tbr"),
        "title": info.get("title"),
        "duration": info.get("duration"),
        "with_auth": bool(auth),
    }
    return files[0], source


def test_auth(settings: dict) -> tuple[bool, str]:
    """Prüft die hinterlegte Anmeldung an einem kurzen Video (lädt nichts herunter)."""
    auth = auth_options(settings)
    if not auth:
        if settings.get("youtube_auth") == "file":
            return False, "cookies.txt nicht gefunden – Datei wählen."
        return False, "Keine Anmeldung eingestellt."
    opts = _base_options()
    opts.update(auth, skip_download=True)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            logged_in = any(c.name in _SESSION_COOKIES and c.domain.endswith("youtube.com") for c in ydl.cookiejar)
            if not logged_in:
                return False, ("Keine YouTube-Anmeldung gefunden. Im Browser bei youtube.com anmelden"
                               + (" und die cookies.txt neu exportieren." if "cookiefile" in auth else ", Browser schließen, nochmal testen."))
            ydl.extract_info(_TEST_VIDEO, download=False)
    except Exception as exc:  # noqa: BLE001
        return False, str(_classify(exc, True))
    return True, "Klappt – angemeldet, YouTube liefert."


def _classify(exc: Exception, with_auth: bool) -> AudioError:
    text = str(exc).replace("ERROR: ", "").strip()
    low = text.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        if with_auth:
            return LoginRequired("YouTube sperrt trotz Anmeldung (Bot-Schutz). Cookies evtl. abgelaufen – neu "
                                 "exportieren, Router neu verbinden oder später nochmal.", bot=True)
        return LoginRequired("YouTube sperrt gerade deine Internet-Adresse (Bot-Schutz) – siehe Hinweis oben.", bot=True)
    if "could not find" in low and ("cookies database" in low or "browser" in low or "profile" in low):
        return AuthError("Browser-Daten nicht gefunden – ist der Browser installiert und dort bei YouTube angemeldet?")
    if _COOKIE_FAIL.search(low):
        return AuthError("Browser-Anmeldung nicht lesbar. Browser komplett schließen (auch unten rechts im Tray) "
                         "und nochmal – oder Firefox bzw. eine cookies.txt nehmen.")
    if _LOGIN.search(low):
        return LoginRequired("Song ist bei YouTube altersbeschränkt – geht nur mit YouTube-Anmeldung (Einstellungen).",
                             bot=False)
    if "video unavailable" in low or "not available" in low:
        return AudioError("Dieses Video ist bei YouTube nicht (mehr) verfügbar. Tipp: anderen YouTube-Link angeben.")
    if "javascript" in low or "js runtime" in low or "n challenge" in low:
        return AudioError("yt-dlp braucht Node.js oder Deno:  winget install OpenJS.NodeJS.LTS  – danach neu starten.")
    if "http error 403" in low:
        return AudioError("YouTube blockt gerade (HTTP 403). StickBeat neu starten (holt yt-dlp-Updates) und nochmal versuchen.")
    return AudioError("Download fehlgeschlagen: " + (text.splitlines() or ["unbekannter Fehler"])[0][:220])


# --- Umwandeln ---------------------------------------------------------------------------

def to_mp3(src: Path, dst: Path, bitrate: int, duration: float | None, on_progress: ProgressFn,
           is_cancelled: Callable[[], bool] = lambda: False) -> None:
    """Echte CBR-MP3 (konstante Bitrate), 44,1 kHz Stereo – das spielt jeder Player."""
    cmd = [
        ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-i", str(src), "-map", "0:a:0", "-vn",
        "-c:a", "libmp3lame", "-b:a", f"{bitrate}k", "-ar", "44100", "-ac", "2",
        "-map_metadata", "-1", "-id3v2_version", "0", "-write_id3v1", "0",
        "-progress", "pipe:1", "-nostats", "-f", "mp3", str(dst),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    assert proc.stdout is not None and proc.stderr is not None
    for line in proc.stdout:
        if is_cancelled():
            proc.kill()
            proc.wait()
            raise Cancelled()
        if line.startswith("out_time_us=") and duration:
            try:
                on_progress(min(1.0, int(line.split("=", 1)[1]) / 1_000_000 / duration))
            except ValueError:
                pass
    errors = proc.stderr.read()
    if proc.wait() != 0 or not dst.exists() or dst.stat().st_size < 10_000:
        last = (errors.strip().splitlines() or ["unbekannter Fehler"])[-1]
        raise AudioError(f"Umwandlung in MP3 fehlgeschlagen: {last[:200]}")


# --- Taggen -------------------------------------------------------------------------------

def write_tags(path: Path, track: dict, cover: bytes | None) -> None:
    """ID3v2.3 (liest praktisch jedes Gerät) plus ID3v1 als Rückfallebene für sehr alte Player."""
    tags = ID3()
    tags.add(TIT2(encoding=3, text=track.get("title") or "Unbekannter Titel"))
    tags.add(TPE1(encoding=3, text=", ".join(track.get("artists") or ["Unbekannt"])))
    if track.get("album"):
        tags.add(TALB(encoding=3, text=track["album"]))
    if track.get("album_artist"):
        tags.add(TPE2(encoding=3, text=track["album_artist"]))
    if track.get("track_no"):
        total = track.get("track_total")
        tags.add(TRCK(encoding=3, text=f"{track['track_no']}/{total}" if total else str(track["track_no"])))
    disc_total = track.get("disc_total") or 1
    if disc_total > 1 or (track.get("disc_no") or 1) > 1:
        tags.add(TPOS(encoding=3, text=f"{track.get('disc_no') or 1}/{disc_total}"))
    if track.get("date"):
        tags.add(TDRC(encoding=3, text=str(track["date"])[:10]))
    if track.get("genre"):
        tags.add(TCON(encoding=3, text=track["genre"]))
    if track.get("isrc"):
        tags.add(TSRC(encoding=3, text=track["isrc"]))
    if track.get("spotify_id"):
        tags.add(TXXX(encoding=3, desc="SPOTIFY_ID", text=track["spotify_id"]))
    if cover:
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
    tags.save(str(path), v2_version=3, v1=2)
