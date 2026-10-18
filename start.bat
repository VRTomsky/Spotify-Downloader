@echo off
setlocal EnableExtensions
title StickBeat
chcp 65001 >nul
cd /d "%~dp0"

set "SB_HOME=%LOCALAPPDATA%\StickBeat"
set "SB_PY=%SB_HOME%\venv\Scripts\python.exe"

echo.
echo   StickBeat - Spotify-Links als MP3 fuer deinen USB-Stick
echo   -------------------------------------------------------
echo.

rem --- FFmpeg pruefen (wandelt Audio in MP3 um) ---
where ffmpeg >nul 2>nul
if errorlevel 1 goto :noffmpeg

rem --- JavaScript-Laufzeit pruefen (braucht yt-dlp fuer YouTube) ---
where node >nul 2>nul
if not errorlevel 1 goto :python
where deno >nul 2>nul
if not errorlevel 1 goto :python
echo   [Hinweis] Weder Node.js noch Deno gefunden. YouTube-Downloads brauchen eins davon.
echo             Installieren mit:  winget install OpenJS.NodeJS.LTS
echo.

:python
if exist "%SB_PY%" goto :run

echo   Erster Start: StickBeat wird eingerichtet (ca. 1-2 Minuten) ...
echo.
set "BASEPY="
where py >nul 2>nul
if not errorlevel 1 set "BASEPY=py -3"
if defined BASEPY goto :makevenv
where python >nul 2>nul
if not errorlevel 1 set "BASEPY=python"
if not defined BASEPY goto :nopython

:makevenv
%BASEPY% -m venv "%SB_HOME%\venv"
if errorlevel 1 goto :venvfail

:run
"%SB_PY%" "%~dp0run.py" %*
if errorlevel 1 goto :crashed
exit /b 0

:crashed
echo.
echo   StickBeat wurde mit einem Fehler beendet - Details stehen oben.
echo   Log-Datei: %SB_HOME%\logs\stickbeat.log
echo.
pause
exit /b 1

:noffmpeg
echo   [Fehler] FFmpeg wurde nicht gefunden.
echo   Installieren: Eingabeaufforderung oeffnen und eintippen:
echo       winget install Gyan.FFmpeg
echo   Danach dieses Fenster schliessen und start.bat erneut doppelklicken.
echo.
pause
exit /b 1

:nopython
echo   [Fehler] Python wurde nicht gefunden.
echo   Installieren: Eingabeaufforderung oeffnen und eintippen:
echo       winget install Python.Python.3.13
echo   Danach dieses Fenster schliessen und start.bat erneut doppelklicken.
echo.
pause
exit /b 1

:venvfail
echo   [Fehler] Die Python-Umgebung konnte nicht angelegt werden.
echo   Ordner loeschen und nochmal starten:  %SB_HOME%\venv
echo.
pause
exit /b 1
