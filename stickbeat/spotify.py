"""Spotify: Links erkennen, Login (PKCE) und Metadaten lesen.

Von Spotify kommen nur Infos (Titel, Interpret, Album, Cover) – niemals Audio.
Der Spotify-Stream ist per DRM verschlüsselt und wird nicht angefasst.

Seit Februar 2026 gilt für eigene Entwickler-Apps:
- der App-Besitzer braucht Spotify Premium,
- Playlist-Inhalte gibt es per API nur für eigene oder gemeinsame Playlists.
Für fremde Playlists liest StickBeat deshalb die öffentliche Einbettungsseite
(open.spotify.com/embed/...). Die liefert höchstens 100 Songs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from typing import Any, Callable
from urllib.parse import urlencode, urlparse

import requests

from . import PORT, enrich
from .util import data_dir, log

API = "https://api.spotify.com/v1"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
REDIRECT_URI = f"http://127.0.0.1:{PORT}/callback"
SCOPES = "playlist-read-private playlist-read-collaborative user-library-read"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36"}
EMBED_LIMIT = 100

KIND_LABELS = {"track": "Song", "album": "Album", "playlist": "Playlist", "liked": "Lieblingssongs"}

_LINK = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]{2}(?:-[a-z]{2})?/)?(?:embed/)?|spotify:)"
    r"(track|album|playlist)[/:]([A-Za-z0-9]{22})"
)
_LIKED = re.compile(r"open\.spotify\.com/collection/tracks|spotify:(?:user:[^:]+:)?collection(?::tracks)?$")
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class SpotifyError(Exception):
    pass


# --- Link-Erkennung --------------------------------------------------------------

def parse_link(text: str) -> tuple[str, str] | None:
    """Gibt (art, id) zurück, z. B. ("playlist", "37i9dQZF1DXcBWIGoYBM5M"), sonst None."""
    text = (text or "").strip()
    if _LIKED.search(text):
        return "liked", "me"
    match = _LINK.search(text)
    if match:
        return match.group(1), match.group(2)
    host = urlparse(text if "://" in text else "https://" + text).netloc.lower()
    if host in ("spotify.link", "spoti.fi", "spotify.app.link"):
        resolved = _resolve_short_link(text if "://" in text else "https://" + text)
        match = _LINK.search(resolved)
        if match:
            return match.group(1), match.group(2)
    return None


def _resolve_short_link(url: str) -> str:
    try:
        resp = requests.get(url, headers=UA, timeout=15, allow_redirects=True)
    except requests.RequestException:
        return ""
    found = _LINK.search(resp.url) or _LINK.search(resp.text)
    return found.group(0) if found else ""


# --- Normalisierte Song-Daten ---------------------------------------------------------

def _largest_image(images: list[dict] | None) -> str | None:
    if not images:
        return None
    best = max(images, key=lambda img: (img.get("width") or 0))
    return best.get("url")


def _track_from_api(track: dict, album: dict | None = None) -> dict[str, Any]:
    album = album or track.get("album") or {}
    artists = [a.get("name", "") for a in track.get("artists") or [] if a.get("name")]
    album_artists = [a.get("name", "") for a in album.get("artists") or [] if a.get("name")]
    return {
        "spotify_id": track.get("id"),
        "title": track.get("name") or "Unbekannter Titel",
        "artists": artists or ["Unbekannt"],
        "album": album.get("name") or "",
        "album_artist": (album_artists or artists or ["Unbekannt"])[0],
        "album_id": album.get("id"),
        "track_no": track.get("track_number") or 0,
        "track_total": album.get("total_tracks") or 0,
        "disc_no": track.get("disc_number") or 1,
        "disc_total": 1,
        "date": album.get("release_date") or "",
        "duration": (track.get("duration_ms") or 0) / 1000,
        "isrc": (track.get("external_ids") or {}).get("isrc"),
        "upc": (album.get("external_ids") or {}).get("upc"),
        "cover_url": _largest_image(album.get("images")),
        "genre": "",
        "needs_enrich": False,
    }


def _collection(kind: str, id_: str, name: str, subtitle: str, cover: str | None,
                tracks: list[dict], source: str, note: str | None = None) -> dict[str, Any]:
    return {"kind": kind, "id": id_, "name": name or KIND_LABELS.get(kind, ""), "subtitle": subtitle,
            "cover_url": cover, "tracks": tracks, "source": source, "note": note}


# --- Client ------------------------------------------------------------------------

class SpotifyClient:
    def __init__(self, get_settings: Callable[[], dict]):
        self._get_settings = get_settings
        self._token_path = data_dir() / "spotify_token.json"
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[str, str]] = {}
        self._http = requests.Session()
        self._http.headers.update(UA)
        self._profile: dict | None = None

    # Login ---------------------------------------------------------------------
    @property
    def client_id(self) -> str:
        return (self._get_settings().get("spotify_client_id") or "").strip()

    def _load_token(self) -> dict | None:
        try:
            return json.loads(self._token_path.read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def connected(self) -> bool:
        token = self._load_token()
        return bool(token and token.get("refresh_token") and token.get("client_id") == self.client_id)

    def login_url(self) -> str:
        client_id = self.client_id
        if not client_id:
            raise SpotifyError("Trag zuerst deine Spotify Client-ID in den Einstellungen ein.")
        verifier = secrets.token_urlsafe(72)[:96]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        self._pending[state] = (verifier, client_id)
        return AUTH_URL + "?" + urlencode({
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": state,
            "scope": SCOPES,
        })

    def finish_login(self, code: str, state: str) -> None:
        verifier, client_id = self._pending.pop(state, (None, None))
        if not verifier:
            raise SpotifyError("Login-Sitzung abgelaufen. Bitte nochmal auf „Mit Spotify verbinden“ klicken.")
        resp = requests.post(TOKEN_URL, timeout=20, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        })
        if resp.status_code != 200:
            raise SpotifyError(f"Spotify-Login fehlgeschlagen: {_error_text(resp)}")
        self._store_token(resp.json(), client_id)
        self._profile = None

    def logout(self) -> None:
        self._token_path.unlink(missing_ok=True)
        self._profile = None

    def _store_token(self, data: dict, client_id: str, old: dict | None = None) -> dict:
        token = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token") or (old or {}).get("refresh_token"),
            "expires_at": time.time() + int(data.get("expires_in", 3600)) - 60,
            "client_id": client_id,
        }
        self._token_path.write_text(json.dumps(token), "utf-8")
        return token

    def _access_token(self) -> str:
        with self._lock:
            token = self._load_token()
            if not token or token.get("client_id") != self.client_id:
                raise SpotifyError("Nicht mit Spotify verbunden.")
            if time.time() < token.get("expires_at", 0):
                return token["access_token"]
            resp = requests.post(TOKEN_URL, timeout=20, data={
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
                "client_id": token["client_id"],
            })
            if resp.status_code != 200:
                if resp.status_code in (400, 401):
                    self.logout()
                raise SpotifyError("Spotify-Anmeldung abgelaufen – bitte in den Einstellungen neu verbinden.")
            return self._store_token(resp.json(), token["client_id"], token)["access_token"]

    def _expire_token(self) -> None:
        token = self._load_token()
        if token:
            token["expires_at"] = 0
            self._token_path.write_text(json.dumps(token), "utf-8")

    # API -------------------------------------------------------------------------
    def get(self, path_or_url: str, params: dict | None = None) -> dict:
        url = path_or_url if path_or_url.startswith("http") else API + path_or_url
        for attempt in range(5):
            try:
                resp = self._http.get(url, params=params, timeout=20,
                                      headers={"Authorization": f"Bearer {self._access_token()}"})
            except requests.RequestException as exc:
                if attempt >= 2:
                    raise SpotifyError(f"Keine Verbindung zu Spotify: {exc}") from exc
                time.sleep(2)
                continue
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After") or 5)
                if wait > 60:
                    raise SpotifyError(f"Spotify-Abfragelimit erreicht. Wieder möglich in ca. {max(1, wait // 60)} Min.")
                time.sleep(wait + 1)
                continue
            if resp.status_code == 401 and attempt == 0:
                self._expire_token()
                continue
            if resp.status_code == 403:
                raise SpotifyError("Spotify verweigert den Zugriff (403): " + _error_text(resp)
                                   + " – Ist Premium aktiv und dein Konto in der Developer-App unter „User Management“ eingetragen?")
            if resp.status_code == 404:
                raise SpotifyError("Nicht gefunden (404) – ist der Link richtig und öffentlich?")
            if resp.status_code >= 500:
                time.sleep(2)
                continue
            raise SpotifyError(f"Spotify-Fehler {resp.status_code}: {_error_text(resp)}")
        raise SpotifyError("Spotify antwortet gerade nicht – später nochmal versuchen.")

    def profile(self) -> dict | None:
        if not self.connected():
            return None
        if self._profile is None:
            try:
                me = self.get("/me")
                self._profile = {"name": me.get("display_name") or me.get("id") or "Spotify-Konto"}
            except SpotifyError as exc:
                return {"name": None, "error": str(exc)}
        return self._profile

    def my_playlists(self) -> list[dict]:
        result: list[dict] = []
        url: str | None = "/me/playlists"
        params: dict | None = {"limit": 50}
        while url and len(result) < 500:
            page = self.get(url, params)
            for pl in page.get("items") or []:
                if not pl:
                    continue
                total = (pl.get("items") or pl.get("tracks") or {}).get("total")
                result.append({
                    "id": pl.get("id"), "name": pl.get("name"), "total": total,
                    "owner": (pl.get("owner") or {}).get("display_name"),
                    "cover_url": _largest_image(pl.get("images")),
                    "url": f"https://open.spotify.com/playlist/{pl.get('id')}",
                })
            url, params = page.get("next"), None
        return result

    # Auflösen eines Links in eine Songliste ------------------------------------------
    def resolve(self, kind: str, id_: str) -> dict[str, Any]:
        if kind == "liked":
            if not self.connected():
                raise SpotifyError("Für deine Lieblingssongs musst du mit Spotify verbunden sein (Einstellungen).")
            return self._api_liked()
        if self.connected():
            try:
                return getattr(self, f"_api_{kind}")(id_)
            except SpotifyError as exc:
                log.warning("Spotify-API für %s/%s: %s – nutze öffentliche Einbettung", kind, id_, exc)
                coll = self._embed(kind, id_)
                coll["note"] = " ".join(filter(None, [f"Spotify-API: {exc}", coll.get("note")]))
                return coll
        return self._embed(kind, id_)

    def _api_track(self, id_: str) -> dict:
        data = _track_from_api(self.get(f"/tracks/{id_}"))
        return _collection("track", id_, data["title"], ", ".join(data["artists"]), data["cover_url"], [data], "api")

    def _api_album(self, id_: str) -> dict:
        album = self.get(f"/albums/{id_}")
        items: list[dict] = []
        page: dict | None = album.get("tracks") or {}
        while page:
            items.extend(page.get("items") or [])
            page = self.get(page["next"]) if page.get("next") else None
        tracks = [_track_from_api(t, album) for t in items if t and t.get("id")]
        # Tracknummer-Gesamtzahl je CD, CD-Anzahl fürs Tagging
        discs: dict[int, int] = {}
        for t in tracks:
            discs[t["disc_no"]] = discs.get(t["disc_no"], 0) + 1
        for t in tracks:
            t["track_total"] = discs[t["disc_no"]]
            t["disc_total"] = max(discs) if discs else 1
            t["needs_enrich"] = True  # ISRC fehlt in Album-Songlisten, wird pro Song nachgeladen
        artists = ", ".join(a.get("name", "") for a in album.get("artists") or [])
        return _collection("album", id_, album.get("name"), artists, _largest_image(album.get("images")), tracks, "api")

    def _api_playlist(self, id_: str) -> dict:
        pl = self.get(f"/playlists/{id_}")
        owner = (pl.get("owner") or {}).get("display_name") or ""
        paging = pl.get("items") if isinstance(pl.get("items"), dict) else pl.get("tracks")
        if not isinstance(paging, dict) or not isinstance(paging.get("items"), list):
            # Fremde Playlist: API liefert seit 2026 nur Name/Cover, keine Songs
            coll = self._embed("playlist", id_)
            coll["name"] = pl.get("name") or coll["name"]
            coll["subtitle"] = owner or coll["subtitle"]
            coll["note"] = ("Fremde Playlist: Spotify gibt die Songliste nur über die öffentliche Einbettung heraus"
                            + (f" (max. {EMBED_LIMIT} Songs)." if len(coll["tracks"]) >= EMBED_LIMIT else ".")
                            + " Tipp: Songs in eine eigene Playlist kopieren, dann klappt alles.")
            return coll
        tracks: list[dict] = []
        page: dict | None = paging
        while page:
            for entry in page.get("items") or []:
                track = (entry or {}).get("item") or (entry or {}).get("track")
                if not track or track.get("type", "track") != "track" or track.get("is_local") or not track.get("id"):
                    continue
                tracks.append(_track_from_api(track))
            page = self.get(page["next"]) if page.get("next") else None
        return _collection("playlist", id_, pl.get("name"), owner, _largest_image(pl.get("images")), tracks, "api")

    def _api_liked(self) -> dict:
        tracks: list[dict] = []
        url: str | None = "/me/tracks"
        params: dict | None = {"limit": 50}
        while url:
            page = self.get(url, params)
            for entry in page.get("items") or []:
                track = (entry or {}).get("track") or (entry or {}).get("item")
                if track and track.get("id") and not track.get("is_local"):
                    tracks.append(_track_from_api(track))
            url, params = page.get("next"), None
        return _collection("liked", "me", "Lieblingssongs", "Spotify", None, tracks, "api")

    # Öffentliche Einbettungsseite (ohne Login, max. 100 Songs) -------------------------
    def _embed(self, kind: str, id_: str) -> dict:
        try:
            resp = requests.get(f"https://open.spotify.com/embed/{kind}/{id_}", headers=UA, timeout=20)
        except requests.RequestException as exc:
            raise SpotifyError(f"Keine Verbindung zu Spotify: {exc}") from exc
        if resp.status_code == 404:
            raise SpotifyError("Spotify kennt diesen Link nicht (404). Ist die Playlist privat?")
        match = _NEXT_DATA.search(resp.text)
        if resp.status_code != 200 or not match:
            raise SpotifyError("Spotify-Seite konnte nicht gelesen werden. Mit Spotify verbinden hilft (Einstellungen).")
        try:
            entity = json.loads(match.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
        except (KeyError, TypeError, ValueError) as exc:
            raise SpotifyError("Spotify hat das Seitenformat geändert – bitte mit Spotify verbinden.") from exc

        cover = None
        sources = (entity.get("coverArt") or {}).get("sources") or []
        if sources:
            cover = max(sources, key=lambda s: s.get("width") or 0).get("url")

        if kind == "track":
            artists = [a.get("name") for a in entity.get("artists") or [] if a.get("name")]
            track = _blank_track(id_, entity.get("title") or entity.get("name"), artists, entity.get("duration"))
            track["date"] = ((entity.get("releaseDate") or {}).get("isoString") or "")[:10]
            return _collection("track", id_, track["title"], ", ".join(track["artists"]), None, [track], "embed")

        tracks = []
        for item in entity.get("trackList") or []:
            uri = item.get("uri") or ""
            if not uri.startswith("spotify:track:"):
                continue
            artists = [a.strip() for a in (item.get("subtitle") or "").split(",") if a.strip()]
            tracks.append(_blank_track(uri.rsplit(":", 1)[-1], item.get("title"), artists, item.get("duration")))

        if kind == "album":
            for no, track in enumerate(tracks, start=1):
                track.update(album=entity.get("name") or "", album_artist=entity.get("subtitle") or track["artists"][0],
                             track_no=no, track_total=len(tracks), cover_url=cover)

        note = None
        if kind == "playlist" and len(tracks) >= EMBED_LIMIT:
            note = (f"Ohne Spotify-Login sind höchstens {EMBED_LIMIT} Songs pro Playlist sichtbar. "
                    "Mit Spotify verbinden (Einstellungen) – bei eigenen Playlists kommen dann alle.")
        return _collection(kind, id_, entity.get("name") or entity.get("title"), entity.get("subtitle") or "",
                           cover, tracks, "embed", note)

    # Fehlende Infos pro Song nachladen (läuft erst kurz vor dem Download) ----------------
    def enrich(self, track: dict) -> None:
        if not track.get("needs_enrich"):
            return
        if self.connected() and track.get("spotify_id"):
            try:
                full = _track_from_api(self.get(f"/tracks/{track['spotify_id']}"))
                keep = {k: track[k] for k in ("album", "album_artist", "track_total", "disc_total", "cover_url")
                        if track.get(k)}
                track.update(full)
                track.update(keep)  # Album-Infos aus der Albumliste bleiben maßgeblich (Ordnername!)
                track["needs_enrich"] = False
                return
            except SpotifyError as exc:
                log.info("Spotify-Details für %s nicht ladbar (%s) – frage Deezer", track.get("title"), exc)
        enrich.fill_missing(track)
        track["needs_enrich"] = False


def _blank_track(spotify_id: str, title: str | None, artists: list[str], duration_ms: Any) -> dict[str, Any]:
    return {
        "spotify_id": spotify_id, "title": title or "Unbekannter Titel", "artists": artists or ["Unbekannt"],
        "album": "", "album_artist": "", "album_id": None, "track_no": 0, "track_total": 0,
        "disc_no": 1, "disc_total": 1, "date": "", "duration": (duration_ms or 0) / 1000,
        "isrc": None, "upc": None, "cover_url": None, "genre": "", "needs_enrich": True,
    }


def _error_text(resp: requests.Response) -> str:
    try:
        data = resp.json()
        err = data.get("error")
        if isinstance(err, dict):
            return err.get("message") or str(err)
        return data.get("error_description") or str(err or data)
    except ValueError:
        return resp.text[:200] or f"HTTP {resp.status_code}"
