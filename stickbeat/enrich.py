"""Zusatz-Infos und Cover von Deezer (kostenlos, ohne Konto) und Cover-Aufbereitung.

Deezer liefert Cover in 1000×1000 Pixeln, Spotify nur 640×640. Außerdem füllt
Deezer Lücken (Album, Jahr, Tracknummer, Genre), wenn Spotify sie nicht liefert.
"""

from __future__ import annotations

import io
import threading
import time
from collections import OrderedDict
from difflib import SequenceMatcher
from typing import Any

import requests
from PIL import Image

from .util import log

DEEZER = "https://api.deezer.com"
_http = requests.Session()
_http.headers.update({"User-Agent": "StickBeat/1.0"})
_lock = threading.Lock()
_album_cache: dict[int, dict | None] = {}
_isrc_cache: dict[str, dict | None] = {}
_cover_cache: OrderedDict[tuple[str, int], bytes] = OrderedDict()


def _get(path: str, params: dict | None = None) -> Any:
    for _ in range(2):
        try:
            data = _http.get(DEEZER + path, params=params, timeout=15).json()
        except (requests.RequestException, ValueError):
            return None
        if isinstance(data, dict) and data.get("error"):
            if (data["error"] or {}).get("code") == 4:  # Abfragelimit: kurz warten
                time.sleep(1.5)
                continue
            return None
        return data
    return None


def _album(album_id: int | None) -> dict | None:
    if not album_id:
        return None
    with _lock:
        if album_id in _album_cache:
            return _album_cache[album_id]
    data = _get(f"/album/{album_id}")
    with _lock:
        _album_cache[album_id] = data
    return data


def lookup_isrc(isrc: str | None) -> dict | None:
    if not isrc:
        return None
    with _lock:
        if isrc in _isrc_cache:
            return _isrc_cache[isrc]
    data = _get(f"/track/isrc:{isrc}")
    with _lock:
        _isrc_cache[isrc] = data
    return data


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.casefold(), b.casefold()).ratio()


def search_track(artists: list[str], title: str, duration: float) -> dict | None:
    artist = artists[0] if artists else ""
    data = _get("/search", {"q": f"{artist} {title}"})
    best, best_score = None, 0.0
    for hit in (data or {}).get("data", [])[:10]:
        score = 0.5 * _similar(hit.get("title", ""), title) + 0.3 * _similar(hit.get("artist", {}).get("name", ""), artist)
        if duration and hit.get("duration"):
            diff = abs(hit["duration"] - duration)
            score += 0.2 if diff <= 3 else 0.1 if diff <= 10 else -0.3
        if score > best_score:
            best, best_score = hit, score
    if not best or best_score < 0.6:
        return None
    return _get(f"/track/{best['id']}") or best


def fill_missing(track: dict) -> bool:
    """Ergänzt fehlende Felder (Album, Jahr, Nummer, ISRC, Genre, Cover) aus Deezer."""
    hit = lookup_isrc(track.get("isrc")) or search_track(track.get("artists") or [], track.get("title") or "",
                                                         track.get("duration") or 0)
    if not hit:
        if not track.get("album"):
            track["album"] = track.get("title") or "Unbekanntes Album"  # wie eine Single behandeln
        if not track.get("album_artist"):
            track["album_artist"] = (track.get("artists") or ["Unbekannt"])[0]
        return False
    album = _album((hit.get("album") or {}).get("id")) or {}

    def put(key: str, value: Any) -> None:
        if value and not track.get(key):
            track[key] = value

    put("album", (hit.get("album") or {}).get("title"))
    put("album_artist", (album.get("artist") or {}).get("name") or (hit.get("artist") or {}).get("name"))
    put("track_no", hit.get("track_position"))
    put("disc_no", hit.get("disk_number"))
    put("track_total", album.get("nb_tracks"))
    put("date", album.get("release_date") or hit.get("release_date"))
    put("isrc", hit.get("isrc"))
    genres = (album.get("genres") or {}).get("data") or []
    put("genre", genres[0].get("name") if genres else "")
    put("deezer_cover", album.get("cover_xl") or (hit.get("album") or {}).get("cover_xl"))
    put("cover_url", track.get("deezer_cover"))
    return True


def cover_candidates(track: dict) -> list[str]:
    """Cover-URLs, beste zuerst: Deezer 1000 px, dann Spotify 640 px."""
    urls = []
    if track.get("deezer_cover"):
        urls.append(track["deezer_cover"])
    else:
        hit = lookup_isrc(track.get("isrc"))
        if hit:
            album = _album((hit.get("album") or {}).get("id")) or {}
            url = album.get("cover_xl") or (hit.get("album") or {}).get("cover_xl")
            if url:
                urls.append(url)
            genres = (album.get("genres") or {}).get("data") or []
            if genres and not track.get("genre"):
                track["genre"] = genres[0].get("name", "")
    if track.get("cover_url") and track["cover_url"] not in urls:
        urls.append(track["cover_url"])
    return urls


def fetch_cover(track: dict, size: int) -> bytes | None:
    for url in cover_candidates(track):
        key = (url, size)
        with _lock:
            if key in _cover_cache:
                _cover_cache.move_to_end(key)
                return _cover_cache[key]
        try:
            raw = _http.get(url, timeout=20).content
            data = prepare_cover(raw, size)
        except (requests.RequestException, OSError) as exc:
            log.info("Cover %s nicht ladbar: %s", url, exc)
            continue
        with _lock:
            _cover_cache[key] = data
            while len(_cover_cache) > 40:
                _cover_cache.popitem(last=False)
        return data
    return None


def prepare_cover(raw: bytes, size: int) -> bytes:
    """Quadratisches Baseline-JPEG (kein Progressive), das auch alte Player anzeigen."""
    img = Image.open(io.BytesIO(raw))
    img = img.convert("RGB")
    if img.width != img.height:
        side = min(img.width, img.height)
        left, top = (img.width - side) // 2, (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side))
    if img.width > size:
        img = img.resize((size, size), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90, optimize=True, progressive=False)
    return out.getvalue()
