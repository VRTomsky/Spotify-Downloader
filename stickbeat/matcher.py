"""Findet zu Spotify-Infos die passende Audiospur auf YouTube Music.

Reihenfolge: ISRC-Suche (eindeutige Aufnahme-Nummer) → Suche nach „Interpret Titel“
unter Songs (offizielle Audiospuren) → notfalls unter Videos. Jeder Treffer bekommt
Punkte für Titel, Interpret und Länge; Remix/Live/Slowed-Versionen werden abgewertet,
wenn das Original gesucht ist.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from ytmusicapi import YTMusic

from .util import log

THRESHOLD = 0.62
_VERSION_WORDS = ("remix", "live", "acoustic", "akustik", "instrumental", "karaoke", "slowed", "sped up",
                  "nightcore", "reverb", "extended", "cover", "8d", "a cappella", "acapella", "unplugged")
_BRACKETS = re.compile(r"[\(\[\{].*?[\)\]\}]")
_FEAT = re.compile(r"\b(feat\.?|ft\.?|featuring|with)\b.*$")


class NoMatch(Exception):
    pass


@dataclass
class Match:
    video_id: str
    title: str
    artist: str
    duration: int | None
    kind: str
    score: float

    @property
    def url(self) -> str:
        return f"https://music.youtube.com/watch?v={self.video_id}"


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = text.replace("&", " and ").replace("’", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _base_title(text: str) -> str:
    text = _BRACKETS.sub(" ", text or "")
    text = text.split(" - ")[0]
    return _norm(_FEAT.sub("", text.lower()))


def _versions(text: str) -> set[str]:
    low = (text or "").lower()
    return {w for w in _VERSION_WORDS if re.search(rf"\b{re.escape(w)}\b", low)}


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    score = SequenceMatcher(None, a, b).ratio()
    if len(a) > 3 and len(b) > 3 and (a in b or b in a):
        score = max(score, 0.85)
    return score


class Matcher:
    def __init__(self) -> None:
        self._yt: YTMusic | None = None
        self._lock = threading.Lock()

    def _client(self) -> YTMusic:
        if self._yt is None:
            # Bewusst ohne language/location: mit "de" liefert der Song-Filter leere Listen
            self._yt = YTMusic()
        return self._yt

    def _search(self, query: str, filter_: str) -> list[dict]:
        for attempt in range(2):
            try:
                with self._lock:
                    return self._client().search(query, filter=filter_, limit=10) or []
            except Exception as exc:  # ytmusicapi wirft je nach Fehler unterschiedliche Typen
                log.info("YouTube-Music-Suche '%s' fehlgeschlagen (%s)", query, exc)
                self._yt = None
        return []

    def score(self, track: dict, hit: dict) -> float:
        want_title = track.get("title") or ""
        want_artists = [_norm(a) for a in (track.get("artists") or [])[:3] if a]
        got_title = hit.get("title") or ""
        got_artists = " ".join(_norm(a.get("name", "")) for a in hit.get("artists") or [])
        haystack = f"{got_artists} {_norm(got_title)}"

        title_score = max(_ratio(_norm(want_title), _norm(got_title)),
                          _ratio(_base_title(want_title), _base_title(got_title)))
        if want_artists:
            hits = sum(1 for a in want_artists if a and (a in haystack or _ratio(a, got_artists) > 0.8))
            artist_score = hits / len(want_artists)
            if hits and artist_score < 1:
                artist_score = max(artist_score, 0.7)  # Hauptinterpret gefunden reicht meist
        else:
            artist_score = 0.5

        want_len, got_len = track.get("duration") or 0, hit.get("duration_seconds") or 0
        if want_len and got_len:
            diff = abs(want_len - got_len)
            if diff > 30:
                return 0.0
            duration_score = 1.0 if diff <= 3 else 0.8 if diff <= 7 else 0.4 if diff <= 15 else 0.1
        else:
            duration_score = 0.5

        score = 0.45 * title_score + 0.30 * artist_score + 0.25 * duration_score
        want_v, got_v = _versions(want_title), _versions(got_title)
        score -= 0.30 * len(got_v - want_v) + 0.15 * len(want_v - got_v)
        if hit.get("resultType") == "song":
            score += 0.05
        return round(score, 3)

    def find(self, track: dict) -> Match:
        candidates: list[tuple[float, dict]] = []

        def consider(results: list[dict]) -> None:
            for hit in results:
                if hit.get("videoId"):
                    candidates.append((self.score(track, hit), hit))

        if track.get("isrc"):
            consider(self._search(track["isrc"], "songs"))
        artist = (track.get("artists") or [""])[0]
        query = f"{artist} {track.get('title', '')}".strip()
        if not candidates or max(c[0] for c in candidates) < 0.85:
            consider(self._search(query, "songs"))
        if not candidates or max(c[0] for c in candidates) < THRESHOLD:
            consider(self._search(query, "videos"))

        if not candidates:
            raise NoMatch("YouTube Music hat nichts gefunden.")
        best_score, best = max(candidates, key=lambda c: c[0])
        found = f"„{best.get('title')}“ von {', '.join(a.get('name', '') for a in best.get('artists') or [])}"
        if best_score < THRESHOLD:
            raise NoMatch(f"Kein sicherer Treffer (bester: {found}, {best_score:.0%}).")
        return Match(
            video_id=best["videoId"], title=best.get("title") or "",
            artist=", ".join(a.get("name", "") for a in best.get("artists") or []),
            duration=best.get("duration_seconds"), kind=best.get("resultType") or "", score=best_score,
        )
