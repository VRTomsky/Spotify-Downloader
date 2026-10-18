# StickBeat

Spotify-Link rein, getaggte MP3 raus, ab auf den USB-Stick. StickBeat läuft komplett auf deinem PC
unter `http://127.0.0.1:5000`. Nichts wird irgendwo hochgeladen.

**So funktioniert es:** Von Spotify kommen nur die Infos (Titel, Interpret, Album, Cover). Den Ton sucht
StickBeat auf YouTube Music, lädt ihn mit `yt-dlp` und wandelt ihn mit `FFmpeg` in eine MP3 um. Danach
schreibt `mutagen` alle Infos und das Cover in die Datei. Spotifys Kopierschutz (DRM, verschlüsselter Stream)
wird nicht angefasst.

---

## 1. Einmalige Installation

Gebraucht werden Python 3.13, FFmpeg und Node.js. Sind die schon installiert, direkt zu Schritt 2 springen.

Sonst:

1. Startmenü öffnen, `cmd` tippen, **Eingabeaufforderung** öffnen.
2. Diese drei Zeilen nacheinander eintippen, nach jeder Zeile Enter drücken:
   ```
   winget install Python.Python.3.13
   winget install Gyan.FFmpeg
   winget install OpenJS.NodeJS.LTS
   ```
   - **Python** ist die Programmiersprache, in der StickBeat geschrieben ist.
   - **FFmpeg** wandelt Audio in MP3 um.
   - **Node.js** braucht `yt-dlp`, um YouTube-Downloads zu entschlüsseln.
3. Eingabeaufforderung schließen.

Alles Weitere (Flask, yt-dlp, mutagen …) installiert `start.bat` beim ersten Start selbst.

## 2. Starten

1. Im Ordner `StickBeat` doppelt auf **`start.bat`** klicken.
2. Beim **ersten Start** richtet StickBeat sich ein. Das dauert etwa 1–2 Minuten und braucht Internet.
3. Der Browser öffnet `http://127.0.0.1:5000` von selbst.
4. **Das schwarze Fenster offen lassen.** Wenn du es schließt, ist StickBeat beendet.

Tipp: Rechtsklick auf `start.bat` → *Weitere Optionen anzeigen* → *Senden an* → *Desktop (Verknüpfung erstellen)*.
Dann startest du StickBeat vom Desktop.

Alle zwei Tage sucht `start.bat` automatisch nach Updates für `yt-dlp`. YouTube ändert oft etwas, und alte
Versionen scheitern dann.

## 3. Spotify verbinden (einmalig, braucht Premium)

Ohne Verbindung funktioniert StickBeat auch. Fremde Playlists sind dann aber auf 100 Songs begrenzt.
Mit Verbindung gibt es deine eigenen Playlists komplett (auch private), deine Lieblingssongs und genauere Infos.

In StickBeat auf **Einstellungen** klicken und die Schritte dort abarbeiten:

1. [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) öffnen und mit deinem Spotify-Konto anmelden.
2. **Create app** klicken. Name und Beschreibung sind egal, z. B. „StickBeat privat“.
3. Bei **Redirect URIs** genau `http://127.0.0.1:5000/callback` eintragen und **Add** klicken.
4. Bei **Which API/SDKs** den Haken bei **Web API** setzen und **Save** klicken.
5. In der App auf **Settings** gehen, die **Client ID** kopieren, in StickBeat einfügen und **Speichern** klicken.
6. **Mit Spotify verbinden** klicken und zustimmen.

> Seit Februar 2026 verlangt Spotify für solche Entwickler-Apps ein **Premium-Abo** beim App-Besitzer. Außerdem
> gibt die API Playlist-Inhalte nur für **eigene** Playlists heraus. Bei fremden Playlists nimmt StickBeat deshalb
> automatisch die öffentliche Einbettung (max. 100 Songs).
> Trick für große fremde Playlists: In Spotify alle Songs markieren (Strg+A) → *Zu Playlist hinzufügen* → *Neue Playlist*.
> Diese eigene Kopie lädt StickBeat dann vollständig.

## 4. Musik herunterladen

1. In Spotify beim Song, Album oder der Playlist auf **⋯ → Teilen → Link kopieren** klicken.
2. In StickBeat im Tab **Download** den Link einfügen (Strg+V). Rechts im Feld steht dann *Song*, *Album* oder *Playlist*.
3. Auswählen:
   - **Qualität:** 320 kbps ist Standard.
   - **Speichern in:** *Bibliothek* (empfohlen) oder *Direkt auf Stick D:*.
   - **Playlists ablegen:** eigener Ordner (Reihenfolge bleibt) oder einsortiert nach Interpret/Album.
4. **Download starten** klicken. Unten unter **Aufträge** siehst du Fortschrittsbalken, aktuellen Song, Restzeit und Zähler
   (✓ fertig, ↷ schon vorhanden, ✗ Fehler).

Mit Verbindung gibt es zusätzlich die Knöpfe **Meine Playlists** und **Meine Lieblingssongs**.

**Wenn ein Song fehlschlägt,** läuft der Rest trotzdem weiter. Der Song erscheint oben unter **Fehlgeschlagen**:
- **Nochmal** versucht es erneut. YouTube blockt manchmal kurz, StickBeat probiert es ohnehin dreimal automatisch.
- **YouTube-Link** öffnet ein Fenster. Den richtigen Song auf music.youtube.com suchen, Link kopieren, einfügen – StickBeat lädt genau diesen.

Schon vorhandene Songs werden übersprungen. Eine Playlist kannst du also später einfach nochmal laden, dann kommen nur die neuen Songs.

### „YouTube sperrt gerade deine Internet-Adresse“ (Bot-Schutz)

Nach vielen Downloads von derselben Internet-Adresse fragt YouTube „Bist du ein Bot?“ und liefert nichts mehr – egal
welcher Song. Das ist kein Fehler im Link. StickBeat erkennt das, zeigt oben einen orangen Hinweis und überspringt
die restlichen Songs, statt YouTube weiter anzufragen (das würde die Sperre verlängern). Zwischen zwei Downloads macht
StickBeat außerdem immer 2–4 Sekunden Pause, damit es seltener so weit kommt.

So geht es weiter:
1. **Router neu verbinden.** Router 1 Minute vom Strom nehmen, oder bei der FRITZ!Box *Internet → Online-Monitor → Neu verbinden*.
   Bei den meisten Anschlüssen gibt es danach eine neue Adresse. Dann **Fehlgeschlagene nochmal** klicken.
2. **Oder warten.** Meist ist die Sperre nach ein paar Stunden weg.
3. **Dauerhaft: YouTube-Anmeldung** unter *Einstellungen → YouTube-Anmeldung (bei Sperre)*. StickBeat nutzt sie nur,
   wenn YouTube sperrt:
   - **Aus meinem Browser übernehmen:** Browser wählen, der bei youtube.com angemeldet ist. Browser komplett schließen,
     dann **Anmeldung testen**. Bei Chrome klappt das wegen neuer Verschlüsselung oft nicht, Firefox am zuverlässigsten.
   - **cookies.txt-Datei:** Erweiterung „Get cookies.txt LOCALLY“, privates Fenster öffnen, bei YouTube anmelden,
     exportieren, Fenster schließen, Datei in StickBeat wählen, **Anmeldung testen**.
   - **Risiko:** Google kann Konten, die viel herunterladen, vorübergehend einschränken. Am besten ein zweites
     Google-Konto nehmen, nicht dein Hauptkonto.

### Ordnerstruktur

```
Musik\StickBeat\
├── Ed Sheeran\
│   └── ÷ (Deluxe)\
│       ├── 01 - Eraser.mp3
│       └── 02 - Castle on the Hill.mp3
└── Playlists\
    └── Sommer 2026\
        ├── 001 - Ashe - Stop The Wedding!.mp3
        └── 002 - ADÉLA - Nicole Kidman.mp3
```

Jede MP3 enthält: Titel, Interpret, Album, Album-Interpret, Jahr, Tracknummer, CD-Nummer, Genre, ISRC und
das Cover (1000 × 1000 px, Baseline-JPEG). Die Tags sind ID3v2.3 plus ID3v1, das lesen auch alte Geräte.

## 5. Gekaufte Musik (der 100 % legale Weg)

Musik als DRM-freie Datei kaufen, z. B. bei **Bandcamp**, im **Amazon Music Store**, bei **7digital** oder **Qobuz**.
ZIP herunterladen und entpacken. Dann in StickBeat **Gekaufte Musik → Ordner wählen → Import starten** klicken.

- MP3s bleiben unverändert (kein Qualitätsverlust) und bekommen nur saubere Tags.
- FLAC, WAV, M4A & Co. werden mit deiner Qualitätseinstellung zu MP3.
- Fehlende Infos und Cover holt StickBeat von Deezer (kostenlos, ohne Konto).

## 6. Auf den USB-Stick (Beispiel: Plattenspieler IMG Stage Line DJP-106SD)

1. Stick einstecken und in StickBeat den Tab **USB-Stick** öffnen.
2. **Stick wählen.** Steht dort **FAT32 ✓**, passt alles.
3. **Ordnung wählen:** *Ein Ordner pro Album/Playlist* (empfohlen). Die Taste **FOLDER** am Plattenspieler springt dann genau ein Album weiter.
4. Alben und Playlists anhaken und **Auf Stick kopieren** klicken.
5. Danach erscheint der **Spickzettel**: Ordner- und Titelnummern, so wie der Plattenspieler zählt.
   Er liegt auch als `INHALT.txt` auf dem Stick. Mit **Drucken** kannst du ihn ausdrucken.
6. **Stick auswerfen:** Unten rechts in der Taskleiste auf das USB-Symbol klicken → *Auswerfen*. Erst dann abziehen.

Am DJP-106SD:
- Stick einstecken, warten bis `REAd` verschwindet. Links steht dann die Ordnerzahl, rechts die Songzahl.
- **PLAY/PAUSE** startet die Wiedergabe.
- **FOLDER** springt zum nächsten Ordner.
- **SKIP +/−** springt zum nächsten oder vorherigen Titel.
- **PLAY MODE** schaltet Wiederholen und Zufall.

**Warum der Spickzettel?** Laut Anleitung zeigt das Display des DJP-106SD nur Ordner-/Titelnummer und Spielzeit, keine Songnamen.

**Warum die Reihenfolge stimmt:** Einfache Player spielen in der Reihenfolge, in der die Dateien auf den Stick geschrieben
wurden, nicht alphabetisch. StickBeat kopiert deshalb streng sortiert, Datei für Datei.
[Annahme] Wie genau der DJP-106SD Unterordner zählt, steht nicht in der Anleitung. Der Spickzettel geht von der üblichen
Reihenfolge aus. Weicht dein Gerät ab, nimm die flache Ordnung (Standard).

**Stick neu formatieren?** Nur nötig, wenn er nicht FAT32 ist. **Achtung: Formatieren löscht alles auf dem Stick.**
Windows bietet FAT32 im Formatieren-Dialog nur bis 32 GB an. Für größere Sticks das kostenlose Programm *Rufus* nehmen
oder einen Stick bis 32 GB verwenden.

## 7. Ehrlich zur Qualität

YouTube Music liefert meist 130–160 kbps (Opus). StickBeat zeigt bei jedem Song an, was die Quelle wirklich hatte
(z. B. „Quelle opus 137 kbps“). Die MP3 ist echte 320 kbps CBR (konstante Bitrate, 44,1 kHz Stereo, liest dein
Plattenspieler bis 320 kbps). Sie klingt so gut wie die Quelle, aber nicht besser.

- Mit **YouTube Music Premium** und eingerichteter YouTube-Anmeldung (Einstellungen) gibt es bis 256 kbps als Quelle.
- **Echte** 320 kbps oder verlustfrei gibt es nur mit gekauften Dateien (Abschnitt 5).

## 8. Rechtliches

- **Privatkopie** (§ 53 UrhG): private Kopien sind grundsätzlich erlaubt.
- Das **Umgehen technischer Schutzmaßnahmen** (§ 95a UrhG) ist verboten, die Privatkopie hilft da nicht. Das LG Hamburg
  hat YouTubes Download-Sperre im youtube-dl-Verfahren als solche Maßnahme eingestuft. Die YouTube-AGB verbieten
  Downloads ohne Download-Knopf ohnehin.
- **Fazit:** StickBeat bewegt sich in einer Grauzone. Abmahnungen treffen in der Praxis fast immer Leute, die hochladen
  oder Tauschbörsen nutzen. Trotzdem gilt: **Dateien nie weitergeben, nie hochladen.** Sauber legal ist Abschnitt 5.

## 9. Wenn etwas nicht klappt

| Problem | Lösung |
|---|---|
| `start.bat` meldet „FFmpeg wurde nicht gefunden“ | `winget install Gyan.FFmpeg`, Fenster schließen, neu starten |
| Browser zeigt „Seite nicht erreichbar“ | Läuft das schwarze Fenster noch? Sonst `start.bat` doppelklicken |
| „Port 5000 ist belegt“ | StickBeat läuft schon in einem anderen Fenster – das nutzen |
| „YouTube sperrt gerade deine Internet-Adresse“ | Router neu verbinden oder ein paar Stunden warten, dann **Fehlgeschlagene nochmal**. Dauerhaft: YouTube-Anmeldung (Abschnitt 4) |
| Einzelne Songs: „HTTP 403“ | StickBeat neu starten (holt yt-dlp-Updates), dann **Nochmal** |
| Falscher Song geladen | Song im Explorer löschen, beim Auftrag **Songs anzeigen** öffnen, dann **YouTube-Link** mit dem richtigen Link |
| Spotify: „403“ / „Premium“ | Premium aktiv? In der Developer-App unter *User Management* dein Konto eingetragen? |
| Spotify: „Abfragelimit erreicht“ | Spotify bremst. Später nochmal – oder ohne Verbindung (Einbettung) weitermachen |
| Plattenspieler zeigt `NO dEV` | Stick ist nicht FAT32 oder nicht richtig eingesteckt |
| Player zählt Ordner anders als der Spickzettel | Beim Kopieren *Ein Ordner pro Album/Playlist* wählen (keine Unterordner) |
| Ordner-Auswahlfenster erscheint nicht | Liegt oft hinter dem Browser – in der Taskleiste nachsehen |

Das Fehler-Log findest du unter **Einstellungen → Fehler-Log öffnen**
(`%LOCALAPPDATA%\StickBeat\logs\stickbeat.log`).

## 10. Wo liegt was?

| Was | Wo |
|---|---|
| Programm | dieser Ordner (`StickBeat`) |
| Python-Umgebung, Einstellungen, Spotify-Login, Log | `%LOCALAPPDATA%\StickBeat\` |
| Deine Musik (Standard) | `Musik\StickBeat\` |

Komplett entfernen: diesen Ordner und `%LOCALAPPDATA%\StickBeat` löschen.

## 11. Technik (für Neugierige)

| Datei | Aufgabe |
|---|---|
| `start.bat`, `run.py` | Start per Doppelklick, Pakete installieren und aktuell halten |
| `stickbeat/server.py` | lokaler Flask-Server, JSON-API, Live-Updates per Server-Sent Events |
| `stickbeat/spotify.py` | Link-Erkennung, Spotify-Login (PKCE), Metadaten, Einbettungs-Fallback |
| `stickbeat/matcher.py` | passenden Song auf YouTube Music finden (ISRC, Titel, Interpret, Länge) |
| `stickbeat/audio.py` | Download (yt-dlp), MP3 (FFmpeg, CBR 44,1 kHz), Tags (mutagen, ID3v2.3+v1) |
| `stickbeat/enrich.py` | Deezer: Cover 1000 px, fehlende Infos |
| `stickbeat/jobs.py` | Warteschlange, Fortschritt, Restzeit, Fehler und Retry |
| `stickbeat/library.py` | Ordnerstruktur, Import gekaufter Dateien |
| `stickbeat/usb.py` | USB-Sticks finden, sortiert kopieren, Spickzettel |
| `web/` | Oberfläche (HTML/CSS/JS, keine externen Bibliotheken) |

Sicherheit: Der Server hört nur auf `127.0.0.1`. Er lehnt Anfragen fremder Webseiten ab (Host-Prüfung plus eigener
Header bei jeder Aktion), damit keine Webseite heimlich Downloads starten kann.
