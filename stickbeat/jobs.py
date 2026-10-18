"""Warteschlange: ein Arbeits-Thread erledigt alle Aufgaben nacheinander.

Ein fehlgeschlagener Song stoppt nie die ganze Playlist – er wird markiert und kann
später einzeln (auch mit eigenem YouTube-Link) erneut versucht werden.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable

from . import audio, enrich, library, settings, usb
from .matcher import Matcher, NoMatch
from .spotify import KIND_LABELS, SpotifyClient, SpotifyError, parse_link
from .util import data_dir, human_size, log, temp_dir

FINAL = {"fertig", "vorhanden", "fehler", "abgebrochen"}
_YOUTUBE = re.compile(r"^https?://(www\.|m\.|music\.)?(youtube\.com/(watch\?|shorts/)|youtu\.be/)", re.I)
MAX_JOBS = 30


@dataclass
class TrackState:
    index: int
    title: str
    artist: str = ""
    data: dict = field(default_factory=dict)
    status: str = "wartet"
    progress: float = 0.0
    message: str = ""
    path: str = ""
    source: str = ""
    manual_url: str = ""
    seconds: float = 0.0

    def public(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "data"}


@dataclass
class Job:
    id: str
    kind: str                  # "spotify" | "import" | "usb"
    title: str
    subtitle: str = ""
    label: str = ""            # Song / Album / Playlist / Import / USB-Stick
    link: str = ""
    cover_url: str | None = None
    status: str = "wartet"     # liest | wartet | läuft | fertig | fertig mit Fehlern | abgebrochen | fehler
    note: str = ""
    error: str = ""
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    target: str = ""           # Zielordner bzw. Laufwerk
    settings: dict = field(default_factory=dict)
    collection: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    tracks: list[TrackState] = field(default_factory=list)
    current: int | None = None
    cancelled: bool = False

    def counts(self) -> dict[str, int]:
        result = {"gesamt": len(self.tracks), "fertig": 0, "vorhanden": 0, "fehler": 0, "abgebrochen": 0, "offen": 0}
        for t in self.tracks:
            if t.status in result:
                result[t.status] += 1
            else:
                result["offen"] += 1
        return result

    def public(self, with_tracks: bool = True) -> dict:
        counts = self.counts()
        total = len(self.tracks) or 1
        finished = sum(1 for t in self.tracks if t.status in FINAL)
        running = next((t for t in self.tracks if t.status not in FINAL and t.status != "wartet"), None)
        progress = (finished + (running.progress if running else 0)) / total if self.tracks else 0.0
        timed = [t.seconds for t in self.tracks if t.status == "fertig" and t.seconds > 0]
        eta = None
        if timed and self.status == "läuft":
            remaining = sum(1 for t in self.tracks if t.status not in FINAL) - (running.progress if running else 0)
            eta = round(sum(timed) / len(timed) * max(0.0, remaining))
        data = {f.name: getattr(self, f.name) for f in fields(self)
                if f.name not in ("tracks", "settings", "collection", "extra")}
        data["extra"] = {k: v for k, v in self.extra.items() if k in ("index", "total", "layout")}
        data.update(counts=counts, progress=round(progress, 4), eta=eta,
                    current_title=f"{running.title} – {running.artist}".strip(" –") if running else "",
                    current_status=running.status if running else "")
        if with_tracks:
            data["tracks"] = [t.public() for t in self.tracks]
        return data


class JobManager:
    def __init__(self, spotify: SpotifyClient):
        self.spotify = spotify
        self.matcher = Matcher()
        self.jobs: list[Job] = []
        self._units: deque[tuple[str, int | None]] = deque()
        self._cv = threading.Condition()
        self._lock = threading.RLock()
        self.version = 0
        self._state_file = data_dir() / "jobs.json"
        self._load()
        threading.Thread(target=self._worker, name="stickbeat-worker", daemon=True).start()

    # --- Zustand nach außen --------------------------------------------------------------------
    def bump(self) -> None:
        with self._cv:
            self.version += 1
            self._cv.notify_all()

    def wait_for_change(self, last: int, timeout: float = 15.0) -> int:
        with self._cv:
            self._cv.wait_for(lambda: self.version != last, timeout=timeout)
            return self.version

    def snapshot(self) -> dict:
        with self._lock:
            return {"version": self.version, "jobs": [j.public() for j in self.jobs],
                    "busy": any(j.status in ("läuft", "liest", "wartet") for j in self.jobs)}

    def _job(self, job_id: str) -> Job | None:
        with self._lock:
            return next((j for j in self.jobs if j.id == job_id), None)

    def _add(self, job: Job) -> None:
        with self._lock:
            self.jobs.insert(0, job)
            # alte, abgeschlossene Aufträge aus der Liste nehmen
            while len(self.jobs) > MAX_JOBS:
                old = next((j for j in reversed(self.jobs) if j.status not in ("läuft", "liest", "wartet")), None)
                if not old:
                    break
                self.jobs.remove(old)
        self.bump()

    def _enqueue(self, job: Job, indexes: list[int | None]) -> None:
        with self._cv:
            for index in indexes:
                self._units.append((job.id, index))
            self._cv.notify_all()
        self._refresh(job)

    # --- Neue Aufträge ------------------------------------------------------------------------------
    def add_spotify(self, link: str, overrides: dict) -> Job:
        parsed = parse_link(link)
        if not parsed:
            raise ValueError("Das ist kein Spotify-Link für einen Song, ein Album oder eine Playlist.")
        kind, id_ = parsed
        opts = {**settings.load(), **{k: v for k, v in overrides.items() if v not in (None, "")}}
        job = Job(id=uuid.uuid4().hex[:10], kind="spotify", title="Lese Spotify …", label=KIND_LABELS[kind],
                  link=link.strip(), status="liest", target=opts["output_dir"], settings=opts)
        self._add(job)
        threading.Thread(target=self._resolve, args=(job, kind, id_), daemon=True).start()
        return job

    def _resolve(self, job: Job, kind: str, id_: str) -> None:
        try:
            coll = self.spotify.resolve(kind, id_)
        except Exception as exc:  # noqa: BLE001 – Fehler landet sichtbar im Auftrag
            log.warning("Link %s nicht lesbar: %s", job.link, exc)
            job.status, job.error, job.title = "fehler", str(exc), "Link nicht lesbar"
            self._refresh(job)
            return
        tracks = coll.pop("tracks")
        if not tracks:
            job.status, job.error, job.title = "fehler", "Keine Songs gefunden.", coll.get("name") or job.title
            self._refresh(job)
            return
        total = len(tracks)
        for number, track in enumerate(tracks, start=1):
            if kind in ("playlist", "liked"):
                track["playlist_index"] = number
        job.collection = coll
        job.title = coll.get("name") or job.title
        job.subtitle = coll.get("subtitle") or ""
        job.cover_url = coll.get("cover_url") or tracks[0].get("cover_url")
        job.note = coll.get("note") or ""
        job.extra["total"] = total
        job.tracks = [TrackState(index=i, title=t["title"], artist=", ".join(t["artists"]), data=t)
                      for i, t in enumerate(tracks)]
        job.status = "wartet"
        log.info("Auftrag %s: %s „%s“ mit %d Songs (Quelle: %s)", job.id, job.label, job.title, total, coll.get("source"))
        self._enqueue(job, list(range(total)))

    def add_import(self, source_dir: str) -> Job:
        files = library.find_audio(source_dir)
        if not files:
            raise ValueError("In diesem Ordner liegen keine Musikdateien (MP3, FLAC, M4A, WAV …).")
        opts = settings.load()
        job = Job(id=uuid.uuid4().hex[:10], kind="import", title=Path(source_dir).name or source_dir,
                  subtitle=f"{len(files)} Dateien aus {source_dir}", label="Import", link=source_dir,
                  target=opts["output_dir"], settings=opts,
                  collection={"kind": "album", "name": Path(source_dir).name})
        job.tracks = [TrackState(index=i, title=f.name, artist="", data={"source_file": str(f)}) for i, f in enumerate(files)]
        self._add(job)
        self._enqueue(job, list(range(len(files))))
        return job

    def add_usb(self, drive_root: str, folders: list[str], layout: str) -> Job:
        opts = settings.load()
        plan = usb.plan_copy(opts["output_dir"], folders, layout, bool(opts.get("ascii_names")))
        if not plan:
            raise ValueError("Keine Ordner mit MP3s ausgewählt.")
        drive = Path(drive_root)
        if not drive.exists():
            raise ValueError(f"Laufwerk {drive_root} nicht gefunden – Stick eingesteckt?")
        needed = sum(p["size"] for p in plan)
        free = shutil.disk_usage(drive).free
        if needed > free:
            raise ValueError(f"Nicht genug Platz: {human_size(needed)} nötig, nur {human_size(free)} frei.")
        job = Job(id=uuid.uuid4().hex[:10], kind="usb", title=f"Auf {drive_root} kopieren",
                  subtitle=f"{len(plan)} Ordner · {sum(len(p['files']) for p in plan)} Songs · {human_size(needed)}",
                  label="USB-Stick", target=str(drive_root), settings=opts, extra={"plan": plan, "layout": layout})
        job.tracks = [TrackState(index=i, title=p["target"], artist=f"{len(p['files'])} Songs") for i, p in enumerate(plan)]
        self._add(job)
        self._enqueue(job, [None])
        return job

    # --- Steuerung -------------------------------------------------------------------------------------
    def cancel(self, job_id: str) -> None:
        job = self._job(job_id)
        if not job:
            raise ValueError("Auftrag nicht gefunden.")
        job.cancelled = True
        for t in job.tracks:
            if t.status == "wartet":
                t.status, t.message = "abgebrochen", "Abgebrochen"
        self._refresh(job)

    def retry(self, job_id: str, index: int | None = None, url: str = "") -> int:
        job = self._job(job_id)
        if not job:
            raise ValueError("Auftrag nicht gefunden.")
        if job.status in ("liest",):
            raise ValueError("Der Auftrag wird gerade noch gelesen.")
        url = (url or "").strip()
        if url and not _YOUTUBE.match(url):
            raise ValueError("Bitte einen YouTube-Link angeben (youtube.com/watch?v=… oder youtu.be/…).")
        if job.kind == "usb":
            for t in job.tracks:
                t.status, t.progress, t.message = "wartet", 0.0, ""
            job.cancelled, job.finished = False, None
            self._enqueue(job, [None])
            return len(job.tracks)
        if index is not None:
            if not 0 <= index < len(job.tracks):
                raise ValueError("Song nicht gefunden.")
            targets = [job.tracks[index]]
        else:
            targets = [t for t in job.tracks if t.status in ("fehler", "abgebrochen")]
        busy = [t for t in targets if t.status not in FINAL]
        if busy:
            raise ValueError("Dieser Song wird gerade bearbeitet.")
        for t in targets:
            t.status, t.progress, t.message, t.seconds = "wartet", 0.0, "", 0.0
            if url:
                t.manual_url = url
        job.cancelled, job.finished, job.error = False, None, ""
        self._enqueue(job, [t.index for t in targets])
        return len(targets)

    def remove(self, job_id: str) -> None:
        job = self._job(job_id)
        if job and job.status in ("läuft", "wartet", "liest"):
            raise ValueError("Laufende Aufträge erst abbrechen.")
        with self._lock:
            self.jobs = [j for j in self.jobs if j.id != job_id]
        self._save()
        self.bump()

    def clear_finished(self) -> None:
        with self._lock:
            self.jobs = [j for j in self.jobs if j.status in ("läuft", "wartet", "liest")]
        self._save()
        self.bump()

    # --- Arbeits-Thread ------------------------------------------------------------------------------
    def _worker(self) -> None:
        while True:
            with self._cv:
                self._cv.wait_for(lambda: bool(self._units))
                job_id, index = self._units.popleft()
            job = self._job(job_id)
            if not job:
                continue
            if job.started is None:
                job.started = time.time()
            try:
                if job.kind == "usb":
                    if not job.cancelled:
                        job.status = "läuft"
                        self.bump()
                        self._run_usb(job)
                elif index is not None and job.tracks[index].status == "wartet":
                    track = job.tracks[index]
                    if job.cancelled:
                        track.status, track.message = "abgebrochen", "Abgebrochen"
                    else:
                        job.status, job.current = "läuft", index
                        self._run_track(job, track)
            except Exception:  # noqa: BLE001 – der Worker darf nie sterben
                log.error("Unerwarteter Fehler im Worker:\n%s", traceback.format_exc())
            finally:
                job.current = None
                self._refresh(job)
                self._save()

    def _refresh(self, job: Job) -> None:
        """Gesamtstatus eines Auftrags aus den Einzelstatus ableiten."""
        if job.status == "liest" or (job.status == "fehler" and not job.tracks):
            self.bump()
            return
        open_tracks = [t for t in job.tracks if t.status not in FINAL]
        if open_tracks:
            active = job.current is not None or any(t.status != "wartet" for t in open_tracks)
            job.status = "läuft" if active or job.status == "läuft" else "wartet"
            job.finished = None
        else:
            job.status = _final_status(job)
            job.finished = job.finished or time.time()
        self.bump()

    def _progress(self, track: TrackState, start: float, span: float) -> Callable[[float], None]:
        last = [0.0]

        def update(fraction: float) -> None:
            track.progress = round(start + span * fraction, 4)
            now = time.time()
            if now - last[0] > 0.25:
                last[0] = now
                self.bump()
        return update

    def _run_track(self, job: Job, track: TrackState) -> None:
        started = time.time()
        work = temp_dir() / f"{job.id}-{track.index}"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            if job.kind == "import":
                self._import_track(job, track, work)
            else:
                self._spotify_track(job, track, work)
        except audio.Cancelled:
            track.status, track.message = "abgebrochen", "Abgebrochen"
        except Exception as exc:  # noqa: BLE001
            track.status, track.message = "fehler", _friendly(exc)
            log.warning("Fehler bei „%s“ (%s): %s", track.title, job.title, track.message)
            if not isinstance(exc, (NoMatch, audio.AudioError, SpotifyError, PermissionError)):
                log.info(traceback.format_exc())
        finally:
            track.seconds = round(time.time() - started, 1)
            shutil.rmtree(work, ignore_errors=True)

    def _spotify_track(self, job: Job, track: TrackState, work: Path) -> None:
        opts = job.settings
        data = track.data
        is_cancelled = lambda: job.cancelled  # noqa: E731

        track.status, track.progress, track.message = "sucht", 0.02, ""
        self.bump()
        try:
            self.spotify.enrich(data)
        except Exception as exc:  # noqa: BLE001 – ohne Zusatzinfos geht es trotzdem weiter
            log.info("Zusatzinfos für %s fehlen: %s", data.get("title"), exc)
        if not data.get("album") or not data.get("album_artist"):
            enrich.fill_missing(data)
        track.title, track.artist = data["title"], ", ".join(data["artists"])
        if not job.cover_url:
            job.cover_url = data.get("cover_url") or data.get("deezer_cover")

        dest = library.track_dest(opts["output_dir"], data, job.collection, opts, job.extra.get("total", 0))
        if dest.exists() and not track.manual_url:
            track.status, track.progress, track.path = "vorhanden", 1.0, str(dest)
            track.message = "Schon vorhanden – übersprungen"
            return

        if track.manual_url:
            url, found = track.manual_url, "Eigener YouTube-Link"
        else:
            match = self.matcher.find(data)
            url = match.url
            found = f"„{match.title}“ ({match.artist}, Treffer {min(match.score, 1.0):.0%})"
        track.progress = 0.1
        if job.cancelled:
            raise audio.Cancelled()

        track.status = "lädt"
        self.bump()
        source_file, source = audio.download(url, work, self._progress(track, 0.1, 0.6),
                                             opts.get("cookies_file", ""), is_cancelled)
        abr = f"{source['abr']:.0f} kbps" if source.get("abr") else "? kbps"
        track.source = f"{found} · Quelle {source['codec']} {abr}"

        track.status = "wandelt"
        self.bump()
        out = work / "ziel.mp3"
        audio.to_mp3(source_file, out, int(opts["bitrate"]), source.get("duration") or data.get("duration"),
                     self._progress(track, 0.7, 0.22), is_cancelled)

        track.status, track.progress = "taggt", 0.93
        self.bump()
        audio.write_tags(out, data, enrich.fetch_cover(data, int(opts["cover_size"])))
        self._place(out, dest)
        track.status, track.progress, track.path = "fertig", 1.0, str(dest)

    def _import_track(self, job: Job, track: TrackState, work: Path) -> None:
        opts = job.settings
        src = Path(track.data["source_file"])
        track.status, track.progress = "sucht", 0.05
        self.bump()
        data = library.read_file(src, Path(job.link))
        cover = library.prepare_import(data, int(opts["cover_size"]))
        track.data = data
        track.title, track.artist = data["title"], ", ".join(data["artists"])
        dest = library.track_dest(opts["output_dir"], data, {"kind": "album"}, opts)
        if dest.exists():
            track.status, track.progress, track.path = "vorhanden", 1.0, str(dest)
            track.message = "Schon in der Bibliothek – übersprungen"
            return
        out = work / "ziel.mp3"
        if src.suffix.lower() == ".mp3":
            shutil.copyfile(src, out)  # MP3 bleibt unverändert – kein Qualitätsverlust
            track.source = "MP3 übernommen (nicht neu umgewandelt)"
        else:
            track.status = "wandelt"
            self.bump()
            audio.to_mp3(src, out, int(opts["bitrate"]), data.get("duration"), self._progress(track, 0.1, 0.8),
                         lambda: job.cancelled)
            track.source = f"{src.suffix[1:].upper()} → MP3 {opts['bitrate']} kbps"
        track.status, track.progress = "taggt", 0.93
        self.bump()
        audio.write_tags(out, data, cover)
        self._place(out, dest)
        track.status, track.progress, track.path = "fertig", 1.0, str(dest)

    @staticmethod
    def _place(tmp: Path, dest: Path) -> None:
        """Fertige Datei in einem Rutsch ans Ziel – so bleibt die Reihenfolge auf FAT32-Sticks korrekt."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp, dest)

    def _run_usb(self, job: Job) -> None:
        plan = job.extra.get("plan") or []

        def on_folder(index: int, status: str, fraction: float) -> None:
            track = job.tracks[index]
            track.status = status
            track.progress = round(fraction, 4)
            self.bump()

        try:
            result = usb.run_copy(plan, job.target, on_folder, lambda: job.cancelled)
        except OSError as exc:
            for t in job.tracks:
                if t.status not in FINAL:
                    t.status, t.message = "fehler", _friendly(exc)
            return
        for t in job.tracks:
            if t.status not in FINAL:
                t.status = "abgebrochen" if result.get("cancelled") else "fertig"
        try:
            folders = usb.device_index(job.target)
            usb.write_index(job.target, folders)
            job.extra["index"] = folders
        except OSError as exc:
            log.warning("Inhaltsliste nicht schreibbar: %s", exc)
        job.note = (f"{result['copied']} Songs kopiert, {result['skipped']} schon vorhanden. "
                    f"Inhaltsliste: {usb.INDEX_FILE} auf dem Stick. Stick vor dem Abziehen in Windows auswerfen!")

    # --- Speichern über Neustarts hinweg --------------------------------------------------------------------
    def _save(self) -> None:
        with self._lock:
            payload = []
            for job in self.jobs:
                if job.status == "liest":
                    continue
                data = asdict(job)
                data["extra"] = {k: v for k, v in job.extra.items() if k != "index"}
                payload.append(data)
        try:
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
            tmp.replace(self._state_file)
        except OSError as exc:
            log.warning("Auftragsliste nicht speicherbar: %s", exc)

    def _load(self) -> None:
        try:
            payload: list[dict[str, Any]] = json.loads(self._state_file.read_text("utf-8"))
        except (OSError, ValueError):
            return
        for data in payload:
            try:
                tracks = [TrackState(**t) for t in data.pop("tracks", [])]
                job = Job(**data)
                job.tracks = tracks
            except TypeError:
                continue
            for t in job.tracks:
                if t.status not in FINAL:
                    t.status, t.progress, t.message = "fehler", 0.0, "Unterbrochen – StickBeat wurde beendet."
            job.current = None
            if job.tracks:
                job.status = _final_status(job)
            self.jobs.append(job)


def _final_status(job: Job) -> str:
    counts = job.counts()
    if counts["fehler"]:
        return "fertig mit Fehlern"
    if counts["abgebrochen"]:
        return "abgebrochen"
    return "fertig"


def _friendly(exc: Exception) -> str:
    if isinstance(exc, NoMatch):
        return f"{exc} Tipp: „YouTube-Link“ klicken und passenden Link einfügen."
    if isinstance(exc, PermissionError):
        return "Kein Schreibzugriff aufs Ziel – Stick schreibgeschützt oder Datei geöffnet?"
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return "Ziel ist voll – kein Platz mehr."
    if isinstance(exc, FileNotFoundError):
        return f"Datei/Ordner nicht gefunden: {exc.filename}"
    return str(exc) or exc.__class__.__name__
