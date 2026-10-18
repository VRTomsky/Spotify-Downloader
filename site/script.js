"use strict";

/* Vorschau der App mit erfundenen Beispieldaten animieren und Kopier-Knöpfe. */

const SONGS = [
  ["Midnight Drive", "Neon Harbor"],
  ["Golden Hour Loop", "Mira Vale"],
  ["Sommerloch", "Kiezfunk"],
  ["Salt & Static", "Lumen Nord"],
  ["Bremen bei Nacht", "Die Kassetten"],
  ["Backseat Radio", "The Tapeheads"],
  ["Paper Skies", "Juno Park"],
  ["Lichterkette", "Kiezfunk"],
];
const TOTAL = 24;
const STAGES = [["sucht", 0.08], ["lädt", 0.45], ["lädt", 0.7], ["wandelt", 0.88], ["taggt", 0.96]];

const pad = (n, w = 2) => String(n).padStart(w, "0");
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
const $ = (id) => document.getElementById(id);

let done = 5;
let stage = 0;

function render() {
  const [title, artist] = SONGS[done % SONGS.length];
  const [label, part] = STAGES[stage];
  $("mkBar").style.width = `${((done + part) / TOTAL) * 100}%`;
  $("mkCount").textContent = `${pad(done + 1)}/${TOTAL}`;
  $("mkNow").textContent = `${label}: ${title} – ${artist}`;
  $("mkEta").textContent = `noch ~${Math.max(1, Math.round(((TOTAL - done) * 9) / 60))} Min`;

  const rows = [];
  for (let i = Math.max(0, done - 2); i < Math.max(0, done - 2) + 4; i++) {
    const [t, a] = SONGS[i % SONGS.length];
    const state = i < done ? ["fertig", "done"] : i === done ? [label, "live"] : ["wartet", ""];
    rows.push(`<li><span class="no">${i + 1}</span><b>${esc(t)} <small>– ${esc(a)}</small></b>` +
      `<span class="st ${state[1]}">${state[0]}</span></li>`);
  }
  $("mkTracks").innerHTML = rows.join("");
}

function tick() {
  stage += 1;
  if (stage >= STAGES.length) {
    stage = 0;
    done = done + 1 >= TOTAL ? 3 : done + 1;
  }
  render();
}

render();
if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
  setInterval(tick, 1100);
}

document.querySelectorAll(".copy").forEach((button) => {
  button.addEventListener("click", async () => {
    const text = button.parentElement.querySelector("code").textContent;
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Kopiert ✓";
    } catch {
      button.textContent = "Bitte markieren";
    }
    setTimeout(() => (button.textContent = "Kopieren"), 2000);
  });
});
