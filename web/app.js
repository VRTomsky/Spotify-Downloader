"use strict";

/* StickBeat – Oberfläche. Spricht nur mit dem lokalen Server auf 127.0.0.1. */

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n, w = 2) => String(n).padStart(w, "0");

const S = {
  info: null,
  jobs: [],
  busy: false,
  expanded: new Set(),
  target: null,
  drives: [],
  selectedDrive: null,
  libRoot: "",
  library: [],
  libSel: new Set(),
  importDir: "",
  playlists: null,
  shownIndexJob: null,
};

const LIVE = new Set(["sucht", "lädt", "wandelt", "taggt", "kopiert"]);
const TRACK_LABEL = {
  wartet: "wartet", sucht: "sucht", "lädt": "lädt", wandelt: "wandelt", taggt: "taggt", kopiert: "kopiert",
  fertig: "fertig", vorhanden: "vorhanden", fehler: "Fehler", abgebrochen: "abgebrochen",
};
const JOB_LABEL = {
  liest: "liest Spotify …", wartet: "wartet", "läuft": "läuft", fertig: "fertig",
  "fertig mit Fehlern": "fertig mit Fehlern", abgebrochen: "abgebrochen", fehler: "Fehler",
};

// --- Helfer ----------------------------------------------------------------------------------

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-StickBeat": "1" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch { /* leer */ }
  if (!res.ok || data.ok === false) throw new Error(data.error || `Fehler ${res.status}`);
  return data;
}

let toastTimer;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = "show" + (isError ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = ""), isError ? 7000 : 3200);
}

function fmtSize(bytes) {
  if (!bytes) return "0 MB";
  if (bytes < 1024 ** 3) return `${Math.max(1, Math.round(bytes / 1024 ** 2))} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1).replace(".", ",")} GB`;
}

// Lange Pfade in der Mitte kürzen: Laufwerk und Zielordner bleiben sichtbar
function shortPath(path, max = 64) {
  if (!path || path.length <= max) return path || "";
  const sep = path.includes("\\") ? "\\" : "/";
  const parts = path.split(sep);
  const head = parts.slice(0, 2).join(sep);
  let tail = parts[parts.length - 1];
  for (let i = parts.length - 2; i >= 2 && (head + tail).length + parts[i].length < max - 4; i--) tail = parts[i] + sep + tail;
  return `${head}${sep}…${sep}${tail}`;
}

function fmtEta(seconds) {
  if (seconds == null) return "";
  if (seconds < 60) return `noch ~${Math.max(5, Math.round(seconds / 5) * 5)} s`;
  return `noch ~${Math.round(seconds / 60)} Min`;
}

async function pick(mode, start, title) {
  toast("Auswahlfenster geöffnet – evtl. hinter dem Browser.");
  const r = await api("/api/pick", { mode, start, title });
  return r.path;
}

// --- Tabs ------------------------------------------------------------------------------------

function showTab(name) {
  $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $$(".tab").forEach((p) => (p.hidden = p.dataset.panel !== name));
  $("#jobs").hidden = name === "settings";
  if (name === "usb") { loadDrives(); loadLibrary(); }
  try { localStorage.setItem("sb-tab", name); } catch { /* egal */ }
}
$$(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
$("#spotifyPill").addEventListener("click", () => showTab("settings"));

// --- Infos & Einstellungen ------------------------------------------------------------------------

async function loadInfo() {
  S.info = await api("/api/info");
  const s = S.info.settings;
  if (!S.target) S.target = s.output_dir;
  $("#optBitrate").value = s.bitrate;
  $("#optLayout").value = s.playlist_layout;
  $("#setOutput").textContent = shortPath(s.output_dir);
  $("#setOutput").title = s.output_dir;
  $("#setBitrate").value = s.bitrate;
  $("#setCover").value = s.cover_size;
  $("#setAscii").checked = !!s.ascii_names;
  $("#setCookies").textContent = shortPath(s.cookies_file) || "nicht gesetzt";
  $("#setCookies").title = s.cookies_file || "";
  $("#clientId").value = s.spotify_client_id || "";
  $("#redirectUri").textContent = S.info.redirect_uri;
  $$("input[name=usbLayout]").forEach((r) => (r.checked = r.value === s.usb_layout));
  renderTarget();
  renderSpotify();
  renderSystem();
}

function renderSpotify() {
  const sp = S.info.spotify;
  const pill = $("#spotifyPill");
  const badge = $("#spotifyState");
  pill.classList.toggle("on", sp.connected && !sp.profile?.error);
  if (sp.connected && sp.profile?.error) {
    pill.textContent = "Spotify: Problem";
    pill.title = sp.profile.error;
    badge.textContent = "Problem";
    badge.className = "badge bad";
  } else if (sp.connected) {
    pill.textContent = sp.profile?.name ? `Spotify: ${sp.profile.name}` : "Spotify verbunden";
    pill.title = "";
    badge.textContent = "verbunden";
    badge.className = "badge ok";
  } else {
    pill.textContent = "Spotify verbinden";
    badge.textContent = sp.client_id_set ? "nicht verbunden" : "Client ID fehlt";
    badge.className = "badge muted";
  }
  $("#connectBtn").hidden = sp.connected;
  $("#disconnectBtn").hidden = !sp.connected;
  $("#myPlaylistsBtn").hidden = !sp.connected;
  $("#likedBtn").hidden = !sp.connected;
  $("#quickHint").textContent = sp.connected
    ? "Oder direkt aus deinem Spotify wählen."
    : "Tipp: In den Einstellungen mit Spotify verbinden – dann gibt’s hier deine Playlists und Lieblingssongs.";
}

function renderSystem() {
  const t = S.info.tools;
  const row = (ok, label, detail) =>
    `<li><span class="badge ${ok ? "ok" : "bad"}">${ok ? "ok" : "fehlt"}</span><span>${esc(label)}</span><span class="hint">${esc(detail)}</span></li>`;
  $("#systemList").innerHTML = [
    row(t.ffmpeg, "FFmpeg", t.ffmpeg ? "wandelt in MP3 um" : "winget install Gyan.FFmpeg"),
    row(!!t.js_runtime, "JavaScript-Laufzeit für YouTube", t.js_runtime || "winget install OpenJS.NodeJS.LTS"),
    `<li><span class="badge muted">v${esc(S.info.version)}</span><span>StickBeat</span><span class="hint">${esc(S.info.data_dir)}</span></li>`,
  ].join("");
}

async function saveSettings(changes, okMessage = "Gespeichert.") {
  try {
    await api("/api/settings", changes);
    await loadInfo();
    toast(okMessage);
  } catch (e) {
    toast(e.message, true);
  }
}

$("#saveClientId").addEventListener("click", () => saveSettings({ spotify_client_id: $("#clientId").value.trim() }, "Client ID gespeichert."));
$("#connectBtn").addEventListener("click", (ev) => {
  if (!S.info?.spotify.client_id_set) {
    ev.preventDefault();
    toast("Zuerst die Client ID eintragen und speichern.", true);
  }
});
$("#disconnectBtn").addEventListener("click", async () => {
  await api("/api/spotify/logout", {});
  await loadInfo();
  toast("Spotify getrennt.");
});
$("#copyRedirect").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("#redirectUri").textContent);
    toast("Kopiert – jetzt bei Spotify einfügen.");
  } catch {
    toast("Kopieren ging nicht – bitte markieren und Strg+C.", true);
  }
});
$("#pickOutput").addEventListener("click", async () => {
  const path = await pick("folder", S.info.settings.output_dir, "Bibliotheksordner wählen");
  if (!path) return;
  const wasDefault = S.target === S.info.settings.output_dir;
  await saveSettings({ output_dir: path }, "Bibliotheksordner gespeichert.");
  if (wasDefault) { S.target = path; renderTarget(); }
});
$("#setBitrate").addEventListener("change", (e) => saveSettings({ bitrate: +e.target.value }));
$("#setCover").addEventListener("change", (e) => saveSettings({ cover_size: +e.target.value }));
$("#setAscii").addEventListener("change", (e) => saveSettings({ ascii_names: e.target.checked }));
$("#pickCookies").addEventListener("click", async () => {
  const path = await pick("file", "", "cookies.txt wählen");
  if (path) saveSettings({ cookies_file: path }, "cookies.txt gespeichert.");
});
$("#clearCookies").addEventListener("click", () => saveSettings({ cookies_file: "" }, "cookies.txt entfernt."));
$("#openLog").addEventListener("click", () => api("/api/open", { path: S.info.log_file }).catch((e) => toast(e.message, true)));
$("#openData").addEventListener("click", () => api("/api/open", { path: S.info.data_dir }).catch((e) => toast(e.message, true)));

// --- Download --------------------------------------------------------------------------------------

function renderTarget() {
  if (!S.info) return;
  $("#optTarget").textContent = shortPath(S.target);
  $("#optTarget").title = S.target;
  const chips = [{ label: "Bibliothek", path: S.info.settings.output_dir }];
  for (const d of S.drives) chips.push({ label: `Direkt auf Stick ${d.letter}: (${d.fs}, ${d.free_text} frei)`, path: d.root });
  $("#targetChips").innerHTML = chips
    .map((c) => `<button type="button" class="chip ${c.path === S.target ? "active" : ""}" data-path="${esc(c.path)}">${esc(c.label)}</button>`)
    .join("");
}
$("#targetChips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  S.target = chip.dataset.path;
  renderTarget();
});
$("#pickTarget").addEventListener("click", async () => {
  const path = await pick("folder", S.target, "Wohin sollen die MP3s?");
  if (path) { S.target = path; renderTarget(); }
});

let detectTimer;
let detectSeq = 0;
async function detect() {
  const url = $("#link").value.trim();
  const badge = $("#linkType");
  const seq = ++detectSeq;
  if (!url) {
    badge.textContent = "–";
    badge.className = "badge muted";
    $("#startBtn").disabled = true;
    return;
  }
  try {
    const r = await api("/api/detect", { url });
    if (seq !== detectSeq) return;
    badge.textContent = r.valid ? r.label : "ungültig";
    badge.className = r.valid ? "badge" : "badge bad";
    $("#startBtn").disabled = !r.valid;
  } catch { /* Server weg – SSE meldet das */ }
}
$("#link").addEventListener("input", () => { clearTimeout(detectTimer); detectTimer = setTimeout(detect, 220); });
$("#link").addEventListener("keydown", (e) => { if (e.key === "Enter" && !$("#startBtn").disabled) startDownload($("#link").value); });
$("#startBtn").addEventListener("click", () => startDownload($("#link").value));
$("#likedBtn").addEventListener("click", () => startDownload("https://open.spotify.com/collection/tracks"));

async function startDownload(url) {
  try {
    await api("/api/download", {
      url: url.trim(),
      bitrate: +$("#optBitrate").value,
      output_dir: S.target,
      playlist_layout: $("#optLayout").value,
    });
    toast("Auftrag angelegt – geht gleich los.");
    $("#link").value = "";
    detect();
  } catch (e) {
    toast(e.message, true);
  }
}

$("#myPlaylistsBtn").addEventListener("click", async () => {
  const box = $("#playlistPicker");
  if (!box.hidden) { box.hidden = true; return; }
  box.hidden = false;
  if (!S.playlists) {
    box.innerHTML = `<span class="hint">Lade deine Playlists …</span>`;
    try {
      S.playlists = (await api("/api/spotify/playlists")).playlists;
    } catch (e) {
      box.innerHTML = `<span class="hint">${esc(e.message)}</span>`;
      return;
    }
  }
  box.innerHTML = S.playlists.length
    ? S.playlists.map((p) => `<button type="button" data-url="${esc(p.url)}">
        ${p.cover_url ? `<img src="${esc(p.cover_url)}" alt="" loading="lazy">` : `<span class="noimg"></span>`}
        <span>${esc(p.name)}</span><small>${p.total ?? "?"} Songs · ${esc(p.owner || "")}</small></button>`).join("")
    : `<span class="hint">Keine Playlists gefunden.</span>`;
});
$("#playlistPicker").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-url]");
  if (!btn) return;
  $("#link").value = btn.dataset.url;
  $("#playlistPicker").hidden = true;
  detect();
  $("#link").focus();
});

// --- Aufträge ------------------------------------------------------------------------------------------

function trackHtml(job, t) {
  const live = LIVE.has(t.status);
  const cls = live ? "s-live" : `s-${t.status}`;
  const bar = live ? `<span class="minibar"><i style="width:${Math.round(t.progress * 100)}%"></i></span>` : "";
  let acts = "";
  if ((t.status === "fehler" || t.status === "abgebrochen") && job.kind !== "usb") {
    acts = `<button class="ghost small" data-act="retry-one" data-job="${job.id}" data-index="${t.index}">Nochmal</button>`;
    if (job.kind === "spotify") acts += `<button class="ghost small" data-act="yt" data-job="${job.id}" data-index="${t.index}">YouTube-Link</button>`;
  } else if ((t.status === "fertig" || t.status === "vorhanden") && t.path) {
    acts = `<button class="ghost small" data-act="reveal" data-path="${esc(t.path)}">Zeigen</button>`;
  }
  const detail = [t.artist, t.source].filter(Boolean).join(" · ");
  return `<li>
    <span class="no">${t.index + 1}</span>
    <div class="t"><b title="${esc(t.title)}">${esc(t.title)}</b>
      ${detail ? `<small title="${esc(detail)}">${esc(detail)}</small>` : ""}
      ${t.message ? `<small class="msg">${esc(t.message)}</small>` : ""}</div>
    <div class="r">${bar}<span class="tstat ${cls}">${esc(TRACK_LABEL[t.status] || t.status)}</span>${acts}</div>
  </li>`;
}

function jobHtml(j) {
  const c = j.counts;
  const done = c.fertig + c.vorhanden + c.fehler + c.abgebrochen;
  const running = ["läuft", "wartet", "liest"].includes(j.status);
  const slug = j.status.toLowerCase().replace(/\s+/g, "-");
  const pillCls = { fertig: "ok", fehler: "bad", "fertig mit Fehlern": "bad", abgebrochen: "muted", wartet: "muted" }[j.status] || "";
  const cover = j.cover_url ? `<img class="cover" src="${esc(j.cover_url)}" alt="" loading="lazy">` : `<div class="cover ph"></div>`;
  const sub = [j.label, j.subtitle, j.kind !== "usb" && j.target ? `→ ${shortPath(j.target, 48)}` : ""].filter(Boolean).join(" · ");
  const width = j.status === "liest" ? 3 : Math.round(j.progress * 1000) / 10;

  const meta = [];
  if (j.tracks.length) meta.push(`<span class="seg">${pad(done)}/${pad(c.gesamt)}</span>`);
  if (j.current_title) meta.push(`<span class="now" title="${esc(j.current_title)}">${esc(TRACK_LABEL[j.current_status] || "")}: ${esc(j.current_title)}</span>`);
  if (j.eta != null) meta.push(`<span>${fmtEta(j.eta)}</span>`);
  if (j.tracks.length && j.kind !== "usb") {
    meta.push(`<span class="counts"><span class="c-ok" title="fertig">✓ ${c.fertig}</span>` +
      `<span class="c-skip" title="schon vorhanden, übersprungen">↷ ${c.vorhanden}</span>` +
      `<span class="c-err" title="fehlgeschlagen">✗ ${c.fehler}</span></span>`);
  }

  const acts = [];
  if (running) acts.push(`<button class="ghost small" data-act="cancel" data-job="${j.id}">Abbrechen</button>`);
  const failed = c.fehler + c.abgebrochen;
  if (!running && failed) acts.push(`<button class="ghost small" data-act="retry-all" data-job="${j.id}">${j.kind === "usb" ? "Nochmal kopieren" : `Fehlgeschlagene nochmal (${failed})`}</button>`);
  if (j.tracks.length) {
    const open = S.expanded.has(j.id);
    acts.push(`<button class="ghost small" data-act="toggle" data-job="${j.id}">${open ? "Liste ausblenden" : `${j.kind === "usb" ? "Ordner" : "Songs"} anzeigen (${c.gesamt})`}</button>`);
  }
  const folder = j.kind === "usb" ? j.target : (j.tracks.find((t) => t.path)?.path || "");
  if (folder) acts.push(`<button class="ghost small" data-act="reveal" data-path="${esc(folder)}">${j.kind === "usb" ? "Stick öffnen" : "Im Explorer zeigen"}</button>`);
  if (!running) acts.push(`<button class="ghost small" data-act="remove" data-job="${j.id}">Entfernen</button>`);

  return `<article class="job st-${esc(slug)}" data-job="${j.id}">
    ${cover}
    <div class="jobmain">
      <div class="jobtop"><h3 title="${esc(j.title)}">${esc(j.title)}</h3>
        <span class="badge status ${pillCls}">${esc(JOB_LABEL[j.status] || j.status)}</span></div>
      <p class="sub" title="${esc(sub)}">${esc(sub)}</p>
      <div class="bar"><i style="width:${width}%"></i></div>
      <div class="jobmeta">${meta.join("")}</div>
      ${j.note ? `<p class="note">${esc(j.note)}</p>` : ""}
      ${j.error ? `<p class="note err">${esc(j.error)}</p>` : ""}
      <div class="jobacts">${acts.join("")}</div>
      ${S.expanded.has(j.id) ? `<ol class="tracks">${j.tracks.map((t) => trackHtml(j, t)).join("")}</ol>` : ""}
    </div>
  </article>`;
}

let lastJobsHtml = "";
function renderJobs() {
  const html = S.jobs.length
    ? S.jobs.map(jobHtml).join("")
    : `<div class="empty">Noch keine Aufträge. Spotify-Link oben einfügen und loslegen.</div>`;
  if (html !== lastJobsHtml) {
    const list = $("#jobList");
    const scroll = {};
    $$(".job", list).forEach((el) => { const tl = $(".tracks", el); if (tl) scroll[el.dataset.job] = tl.scrollTop; });
    list.innerHTML = html;
    $$(".job", list).forEach((el) => { const tl = $(".tracks", el); if (tl && scroll[el.dataset.job]) tl.scrollTop = scroll[el.dataset.job]; });
    lastJobsHtml = html;
  }
  renderErrors();
  document.body.classList.toggle("busy", S.busy);

  // Nach dem Kopieren auf den Stick: Spickzettel gleich anzeigen
  const usbJob = S.jobs.find((j) => j.kind === "usb" && j.extra?.index);
  if (usbJob && S.shownIndexJob !== usbJob.id + usbJob.finished) {
    S.shownIndexJob = usbJob.id + usbJob.finished;
    renderIndex(usbJob.extra.index);
    loadLibrary();
    loadDrives();
  }
}

function renderErrors() {
  const rows = [];
  for (const j of S.jobs) {
    if (j.kind === "usb") continue;
    for (const t of j.tracks) {
      if (t.status !== "fehler") continue;
      rows.push(`<li>
        <span class="what">${esc(t.title)} <span class="hint">– ${esc(t.artist || j.title)}</span></span>
        <span class="why">${esc(t.message)}</span>
        <span class="acts">
          <button class="ghost small" data-act="retry-one" data-job="${j.id}" data-index="${t.index}">Nochmal</button>
          ${j.kind === "spotify" ? `<button class="ghost small" data-act="yt" data-job="${j.id}" data-index="${t.index}">YouTube-Link</button>` : ""}
        </span></li>`);
    }
  }
  $("#errorsPanel").hidden = rows.length === 0;
  const html = rows.slice(0, 60).join("");
  if ($("#errorList").dataset.html !== html) {
    $("#errorList").innerHTML = html;
    $("#errorList").dataset.html = html;
  }
}

let ytTarget = null;
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const { act, job, index, path } = btn.dataset;
  try {
    if (act === "toggle") {
      S.expanded.has(job) ? S.expanded.delete(job) : S.expanded.add(job);
      lastJobsHtml = "";
      renderJobs();
    } else if (act === "cancel") {
      await api(`/api/jobs/${job}/cancel`, {});
      toast("Wird abgebrochen …");
    } else if (act === "retry-all") {
      const r = await api(`/api/jobs/${job}/retry`, {});
      toast(`${r.count} ${r.count === 1 ? "Eintrag" : "Einträge"} wieder in der Warteschlange.`);
    } else if (act === "retry-one") {
      await api(`/api/jobs/${job}/retry`, { index: +index });
      toast("Neuer Versuch läuft gleich.");
    } else if (act === "yt") {
      const j = S.jobs.find((x) => x.id === job);
      const t = j?.tracks[+index];
      ytTarget = { job, index: +index };
      $("#ytFor").textContent = t ? `Für: ${t.title} – ${t.artist}` : "";
      $("#ytUrl").value = "";
      $("#ytDialog").showModal();
    } else if (act === "remove") {
      await api(`/api/jobs/${job}/remove`, {});
    } else if (act === "reveal") {
      await api("/api/open", { path });
    }
  } catch (err) {
    toast(err.message, true);
  }
});

$("#ytDialog").addEventListener("close", async () => {
  if ($("#ytDialog").returnValue !== "ok" || !ytTarget) return;
  try {
    await api(`/api/jobs/${ytTarget.job}/retry`, { index: ytTarget.index, url: $("#ytUrl").value.trim() });
    toast("Lädt jetzt mit deinem Link.");
  } catch (err) {
    toast(err.message, true);
  }
  ytTarget = null;
});

$("#clearJobs").addEventListener("click", () => api("/api/jobs/clear", {}).catch((e) => toast(e.message, true)));

// --- Gekaufte Musik ----------------------------------------------------------------------------------

$("#pickImport").addEventListener("click", async () => {
  const path = await pick("folder", S.importDir, "Ordner mit gekaufter Musik wählen");
  if (!path) return;
  S.importDir = path;
  $("#importSource").textContent = shortPath(path, 90);
  $("#importSource").title = path;
  $("#startImport").disabled = false;
});
$("#startImport").addEventListener("click", async () => {
  try {
    await api("/api/import", { folder: S.importDir });
    toast("Import läuft – Fortschritt unten bei den Aufträgen.");
  } catch (e) {
    toast(e.message, true);
  }
});

// --- USB-Stick -----------------------------------------------------------------------------------------

async function loadDrives() {
  try {
    S.drives = (await api("/api/drives")).drives;
  } catch { return; }
  if (!S.drives.some((d) => d.root === S.selectedDrive)) S.selectedDrive = S.drives[0]?.root || null;
  renderDrives();
  renderTarget();
  updateCopyBar();
}

function renderDrives() {
  if (!S.drives.length) {
    $("#driveList").innerHTML = `<div class="empty">Kein USB-Stick gefunden. Einstecken und „Neu suchen“ klicken.</div>`;
    return;
  }
  $("#driveList").innerHTML = S.drives.map((d) => {
    const used = Math.round((1 - d.free / d.total) * 100);
    const fs = d.fat32
      ? `<span class="badge ok">FAT32 ✓</span>`
      : `<span class="badge bad">${esc(d.fs)} – dein Player liest sicher nur FAT32</span>`;
    return `<label class="drive ${d.root === S.selectedDrive ? "sel" : ""}">
      <input type="radio" name="drive" value="${esc(d.root)}" ${d.root === S.selectedDrive ? "checked" : ""}>
      <span class="dname"><span class="seg">${esc(d.letter)}:</span>${esc(d.label)}</span>
      <span class="meter"><i style="width:${used}%"></i></span>
      <small>${esc(d.free_text)} frei von ${esc(d.total_text)} · ${fs}</small>
    </label>`;
  }).join("");
}
$("#driveList").addEventListener("change", (e) => {
  if (e.target.name !== "drive") return;
  S.selectedDrive = e.target.value;
  renderDrives();
  updateCopyBar();
});
$("#refreshDrives").addEventListener("click", loadDrives);

async function loadLibrary() {
  try {
    const r = await api("/api/library");
    S.libRoot = r.root;
    S.library = r.folders;
  } catch { return; }
  const known = new Set(S.library.map((f) => f.rel));
  S.libSel = new Set([...S.libSel].filter((rel) => known.has(rel)));
  renderLibrary();
}

function renderLibrary() {
  const filter = $("#libFilter").value.trim().toLowerCase();
  const items = S.library.filter((f) => !filter || f.label.toLowerCase().includes(filter));
  $("#libList").innerHTML = S.library.length === 0
    ? `<div class="empty">Bibliothek ist noch leer – erst Musik herunterladen.<br><small>${esc(S.libRoot)}</small></div>`
    : items.map((f) => `<label class="libitem">
        <input type="checkbox" data-rel="${esc(f.rel)}" ${S.libSel.has(f.rel) ? "checked" : ""}>
        <span class="lname" title="${esc(f.label)}">${esc(f.label)}</span>
        <span class="badge ${f.kind === "playlist" ? "" : "muted"}">${f.kind === "playlist" ? "Playlist" : "Album"}</span>
        <small>${f.files} ${f.files === 1 ? "Song" : "Songs"} · ${fmtSize(f.size)}</small>
      </label>`).join("") || `<div class="empty">Nichts passt zum Filter.</div>`;
  updateCopyBar();
}
$("#libList").addEventListener("change", (e) => {
  const rel = e.target.dataset.rel;
  if (!rel) return;
  e.target.checked ? S.libSel.add(rel) : S.libSel.delete(rel);
  updateCopyBar();
});
$("#libFilter").addEventListener("input", renderLibrary);
$("#libAll").addEventListener("click", () => {
  const filter = $("#libFilter").value.trim().toLowerCase();
  S.library.filter((f) => !filter || f.label.toLowerCase().includes(filter)).forEach((f) => S.libSel.add(f.rel));
  renderLibrary();
});
$("#libNone").addEventListener("click", () => { S.libSel.clear(); renderLibrary(); });
$("#libOpen").addEventListener("click", () => api("/api/open", { path: S.libRoot }).catch((e) => toast(e.message, true)));

function updateCopyBar() {
  const chosen = S.library.filter((f) => S.libSel.has(f.rel));
  const size = chosen.reduce((sum, f) => sum + f.size, 0);
  const songs = chosen.reduce((sum, f) => sum + f.files, 0);
  const drive = S.drives.find((d) => d.root === S.selectedDrive);
  let text = chosen.length ? `${chosen.length} Ordner · ${songs} Songs · ${fmtSize(size)} ausgewählt` : "Noch nichts ausgewählt";
  if (drive) text += ` · Stick ${drive.letter}: ${drive.free_text} frei`;
  const tooBig = drive && size > drive.free;
  if (tooBig) text += " – passt nicht drauf!";
  $("#libSummary").textContent = text;
  $("#copyBtn").disabled = !chosen.length || !drive || tooBig;
}

$("#copyBtn").addEventListener("click", async () => {
  const layout = $("input[name=usbLayout]:checked")?.value || "flat";
  try {
    await api("/api/usb/copy", { drive: S.selectedDrive, folders: [...S.libSel], layout });
    toast("Kopieren läuft – Fortschritt unten bei den Aufträgen.");
  } catch (e) {
    toast(e.message, true);
  }
});

function renderIndex(folders) {
  $("#indexView").innerHTML = folders.length
    ? folders.map((f) => `<div class="ifolder">
        <h4><span class="seg">${pad(f.number)}</span>${esc(f.name)}</h4>
        <ol>${f.tracks.map((t) => `<li><span class="seg">${pad(t.no, 3)}</span>${esc(t.name)}</li>`).join("")}</ol>
      </div>`).join("")
    : `<div class="empty">Auf dem Stick liegen noch keine MP3s.</div>`;
}
$("#loadIndex").addEventListener("click", async () => {
  if (!S.selectedDrive) return toast("Erst einen Stick wählen.", true);
  try {
    renderIndex((await api(`/api/usb/index?drive=${encodeURIComponent(S.selectedDrive)}`)).folders);
  } catch (e) {
    toast(e.message, true);
  }
});
$("#printIndex").addEventListener("click", () => window.print());

// --- Live-Verbindung zum Server ------------------------------------------------------------------------

let offlineTimer;
function connectEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = (e) => {
    clearTimeout(offlineTimer);
    const data = JSON.parse(e.data);
    S.jobs = data.jobs;
    S.busy = data.busy;
    renderJobs();
  };
  es.onerror = () => {
    clearTimeout(offlineTimer);
    offlineTimer = setTimeout(() => toast("Keine Verbindung zu StickBeat – läuft das schwarze Fenster noch? Sonst start.bat doppelklicken.", true), 4000);
  };
}

// --- Start ------------------------------------------------------------------------------------------------

(async function init() {
  const params = new URLSearchParams(location.search);
  let tab = "download";
  try { tab = localStorage.getItem("sb-tab") || "download"; } catch { /* egal */ }
  if (params.get("spotify") === "ok") { toast("Mit Spotify verbunden."); tab = "download"; }
  if (params.get("spotify_error")) { toast(params.get("spotify_error"), true); tab = "settings"; }
  if (params.toString()) history.replaceState(null, "", "/");
  try {
    await loadInfo();
  } catch (e) {
    toast("StickBeat-Server antwortet nicht: " + e.message, true);
  }
  showTab(tab);
  loadDrives();
  connectEvents();
  setInterval(() => { if (!document.hidden) loadDrives(); }, 6000);
})();
