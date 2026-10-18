"""Lokaler Webserver (nur 127.0.0.1) mit JSON-API und Live-Updates per Server-Sent Events."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, abort, jsonify, redirect, request, send_from_directory

from . import __version__, audio, library, settings, usb
from .jobs import JobManager
from .spotify import KIND_LABELS, REDIRECT_URI, SpotifyClient, SpotifyError, parse_link
from .util import data_dir, log, log_file

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# Ordnerauswahl in eigenem Prozess: Tkinter braucht einen Hauptthread
_PICKER = r"""
import sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
mode, start, title = sys.argv[1], sys.argv[2] or None, sys.argv[3]
if mode == "file":
    path = filedialog.askopenfilename(initialdir=start, title=title, filetypes=[("cookies.txt", "*.txt"), ("Alle Dateien", "*.*")])
else:
    path = filedialog.askdirectory(initialdir=start, title=title, mustexist=False)
root.destroy()
sys.stdout.write(path or "")
"""


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    spotify = SpotifyClient(settings.load)
    manager = JobManager(spotify)

    def fail(message: str, status: int = 400):
        return jsonify({"ok": False, "error": message}), status

    @app.before_request
    def guard():
        # Schutz gegen fremde Webseiten, die heimlich Anfragen an 127.0.0.1 schicken (CSRF / DNS-Rebinding)
        if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
            abort(403)
        if request.method == "POST" and request.headers.get("X-StickBeat") != "1":
            abort(403)

    # --- Seiten -------------------------------------------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/static/<path:name>")
    def static_files(name: str):
        resp = send_from_directory(WEB_DIR, name)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    # --- Zustand ------------------------------------------------------------------------------------
    @app.get("/api/state")
    def state():
        return jsonify(manager.snapshot())

    @app.get("/api/events")
    def events():
        def stream():
            last = -1
            yield "retry: 2000\n\n"
            while True:
                version = manager.wait_for_change(last, timeout=15)
                if version == last:
                    yield ": ping\n\n"
                    continue
                last = version
                yield f"data: {json.dumps(manager.snapshot(), ensure_ascii=False)}\n\n"
                time.sleep(0.2)  # höchstens ~5 Updates pro Sekunde
        return Response(stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/info")
    def info():
        prof = spotify.profile()
        s = settings.load()
        return jsonify({
            "version": __version__,
            "settings": s,
            "redirect_uri": REDIRECT_URI,
            "spotify": {"client_id_set": bool(s.get("spotify_client_id")), "connected": spotify.connected(),
                        "profile": prof},
            "tools": {"ffmpeg": bool(shutil.which("ffmpeg")), "js_runtime": next(iter(audio.js_runtime() or {}), None)},
            "browsers": audio.installed_browsers(),
            "log_file": str(log_file()),
            "data_dir": str(data_dir()),
        })

    # --- Spotify ------------------------------------------------------------------------------------
    @app.post("/api/detect")
    def detect():
        url = (request.get_json(silent=True) or {}).get("url", "")
        parsed = parse_link(url)
        if not parsed:
            return jsonify({"valid": False})
        return jsonify({"valid": True, "kind": parsed[0], "id": parsed[1], "label": KIND_LABELS[parsed[0]]})

    @app.get("/spotify/login")
    def spotify_login():
        try:
            return redirect(spotify.login_url())
        except SpotifyError as exc:
            return redirect("/?spotify_error=" + quote(str(exc)))

    @app.get("/callback")
    def spotify_callback():
        if request.args.get("error"):
            return redirect("/?spotify_error=" + quote("Spotify-Login abgebrochen: " + request.args["error"]))
        try:
            spotify.finish_login(request.args.get("code", ""), request.args.get("state", ""))
        except SpotifyError as exc:
            return redirect("/?spotify_error=" + quote(str(exc)))
        return redirect("/?spotify=ok")

    @app.post("/api/spotify/logout")
    def spotify_logout():
        spotify.logout()
        return jsonify({"ok": True})

    @app.get("/api/spotify/playlists")
    def spotify_playlists():
        if not spotify.connected():
            return fail("Nicht mit Spotify verbunden.")
        try:
            return jsonify({"ok": True, "playlists": spotify.my_playlists()})
        except SpotifyError as exc:
            return fail(str(exc))

    # --- Aufträge -----------------------------------------------------------------------------------
    @app.post("/api/download")
    def download():
        body = request.get_json(silent=True) or {}
        overrides = {k: body.get(k) for k in ("output_dir", "bitrate", "playlist_layout")}
        if overrides.get("bitrate"):
            overrides["bitrate"] = int(overrides["bitrate"])
        if overrides.get("output_dir") and not _usable_dir(overrides["output_dir"]):
            return fail("Zielordner nicht beschreibbar oder nicht vorhanden.")
        try:
            job = manager.add_spotify(body.get("url", ""), overrides)
        except ValueError as exc:
            return fail(str(exc))
        return jsonify({"ok": True, "job": job.id})

    @app.post("/api/import")
    def import_files():
        folder = (request.get_json(silent=True) or {}).get("folder", "")
        try:
            job = manager.add_import(folder)
        except ValueError as exc:
            return fail(str(exc))
        return jsonify({"ok": True, "job": job.id})

    @app.post("/api/jobs/<job_id>/<action>")
    def job_action(job_id: str, action: str):
        body = request.get_json(silent=True) or {}
        try:
            if action == "cancel":
                manager.cancel(job_id)
            elif action == "retry":
                index = body.get("index")
                count = manager.retry(job_id, int(index) if index is not None else None, body.get("url", ""))
                return jsonify({"ok": True, "count": count})
            elif action == "remove":
                manager.remove(job_id)
            else:
                abort(404)
        except ValueError as exc:
            return fail(str(exc))
        return jsonify({"ok": True})

    @app.post("/api/jobs/clear")
    def clear_jobs():
        manager.clear_finished()
        return jsonify({"ok": True})

    # --- USB ----------------------------------------------------------------------------------------
    @app.get("/api/drives")
    def drives():
        return jsonify({"drives": usb.list_drives()})

    @app.get("/api/library")
    def library_list():
        root = settings.load()["output_dir"]
        return jsonify({"root": root, "folders": library.library_folders(root)})

    @app.post("/api/usb/copy")
    def usb_copy():
        body = request.get_json(silent=True) or {}
        drive = body.get("drive", "")
        if drive not in {d["root"] for d in usb.list_drives()} and not body.get("allow_any"):
            return fail("Bitte einen USB-Stick aus der Liste wählen.")
        layout = body.get("layout") if body.get("layout") in ("flat", "tree") else "flat"
        settings.save({"usb_layout": layout})
        try:
            job = manager.add_usb(drive, list(body.get("folders") or []), layout)
        except ValueError as exc:
            return fail(str(exc))
        return jsonify({"ok": True, "job": job.id})

    @app.get("/api/usb/index")
    def usb_index():
        drive = request.args.get("drive", "")
        if drive not in {d["root"] for d in usb.list_drives()}:
            return fail("Stick nicht gefunden.")
        return jsonify({"ok": True, "folders": usb.device_index(drive)})

    # --- Einstellungen & Helfer -------------------------------------------------------------------
    @app.post("/api/settings")
    def save_settings():
        body = request.get_json(silent=True) or {}
        if "output_dir" in body and body["output_dir"] and not _usable_dir(body["output_dir"]):
            return fail("Ordner nicht beschreibbar oder nicht vorhanden.")
        if body.get("cookies_file") and not os.path.isfile(body["cookies_file"]):
            return fail("cookies.txt nicht gefunden.")
        return jsonify({"ok": True, "settings": settings.save(body)})

    @app.post("/api/youtube/test")
    def youtube_test():
        works, message = audio.test_auth(settings.load())
        if works:
            manager.clear_block()
        return jsonify({"ok": True, "works": works, "message": message})

    @app.post("/api/pick")
    def pick():
        body = request.get_json(silent=True) or {}
        mode = "file" if body.get("mode") == "file" else "folder"
        try:
            result = subprocess.run(
                [sys.executable, "-c", _PICKER, mode, body.get("start") or "", body.get("title") or "Ordner wählen"],
                capture_output=True, text=True, encoding="utf-8", timeout=900, creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return fail(f"Auswahlfenster ging nicht auf: {exc}")
        path = result.stdout.strip()
        return jsonify({"ok": True, "path": str(Path(path)) if path else ""})

    @app.post("/api/open")
    def open_path():
        target = (request.get_json(silent=True) or {}).get("path", "")
        path = Path(target)
        if not target or not path.exists():
            return fail("Pfad existiert nicht.")
        if sys.platform != "win32":
            return fail("Nur unter Windows verfügbar.")
        if path.is_file():
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(str(path))  # noqa: S606 – öffnet den Explorer im Ordner
        return jsonify({"ok": True})

    log.info("StickBeat %s bereit – Daten in %s", __version__, data_dir())
    return app


def _usable_dir(path: str) -> bool:
    try:
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".stickbeat-test"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False

