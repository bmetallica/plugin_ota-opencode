// Die Ansicht: jede Änderung als POST an aktion, mit der Kopfzeile
// X-OTA-Ansicht (Schutz gegen CSRF), danach neu laden.
"use strict";

const basis = document.body.dataset.basis || "";
const sprache = document.body.dataset.sprache || "de";
const sitzung = () => document.querySelector(".sitzung")?.dataset.sitzung || "";

function melden(text, fehler) {
  const m = document.getElementById("meldung");
  if (!m || !text) return;
  m.textContent = text;
  m.hidden = false;
  m.classList.toggle("fehler", !!fehler);
}

async function senden(daten, nachher) {
  try {
    const r = await fetch(basis + "aktion", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-OTA-Ansicht": "1" },
      body: JSON.stringify({ sprache, sitzung: sitzung(), ...daten }),
      credentials: "same-origin",
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) {
      melden(j.meldung || j.detail || document.body.dataset.fehler, true);
      return;
    }
    if (j.meldung) sessionStorage.setItem("gedaechtnis.meldung", j.meldung);
    if (nachher) nachher(j);
    else location.reload();
  } catch (e) {
    melden(document.body.dataset.fehler, true);
  }
}

function formular(f) {
  const d = Object.fromEntries(new FormData(f).entries());
  if (typeof d.schlagworte === "string") d.schlagworte = d.schlagworte.split(",").map((s) => s.trim()).filter(Boolean);
  return d;
}

document.addEventListener("DOMContentLoaded", () => {
  const alt = sessionStorage.getItem("gedaechtnis.meldung");
  if (alt) { melden(alt); sessionStorage.removeItem("gedaechtnis.meldung"); }

  document.querySelectorAll("form[data-aktion]").forEach((f) => {
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const d = formular(f);
      const li = f.closest(".eintrag");
      if (li) { d.id = li.dataset.id; d.stand = li.dataset.stand; }
      senden({ was: f.dataset.aktion, ...d });
    });
  });

  document.querySelectorAll("button[data-aktion]").forEach((b) => {
    b.addEventListener("click", () => {
      const was = b.dataset.aktion;
      if (was === "alles_loeschen") {
        const name = prompt(b.dataset.frage);
        if (name === null) return;
        senden({ was, bestaetigung: name }, () => { location.href = location.pathname + location.search.replace(/([?&])s=[^&]*&?/, "$1"); });
        return;
      }
      if (b.dataset.frage && !confirm(b.dataset.frage)) return;
      senden({ was }, (j) => {
        if (j.weg) location.href = location.pathname + location.search.replace(/([?&])s=[^&]*&?/, "$1");
        else location.reload();
      });
    });
  });

  document.querySelectorAll(".eintrag").forEach((li) => {
    const form = li.querySelector("form.bearbeiten");
    const inhalt = li.querySelector(".inhalt");
    li.querySelector("[data-bearbeiten]")?.addEventListener("click", () => {
      form.hidden = false; inhalt.hidden = true; form.querySelector("textarea").focus();
    });
    li.querySelector("[data-abbrechen]")?.addEventListener("click", () => {
      form.hidden = true; inhalt.hidden = false;
    });
    li.querySelector("[data-anheften]")?.addEventListener("click", (ev) => {
      senden({ was: "anheften", id: li.dataset.id, wert: ev.currentTarget.dataset.anheften === "1" });
    });
    li.querySelector("[data-loeschen]")?.addEventListener("click", () => {
      senden({ was: "loeschen", ids: [li.dataset.id] });
    });
  });

  document.querySelectorAll("[data-sammel]").forEach((b) => {
    b.addEventListener("click", () => {
      const ids = [...document.querySelectorAll(".eintrag")]
        .filter((li) => li.querySelector(".wahl")?.checked).map((li) => li.dataset.id);
      if (!ids.length) return;
      const was = b.dataset.sammel;
      if (was === "loeschen") { senden({ was, ids }); return; }
      const ziel = document.getElementById("ziel").value;
      if (!ziel) { document.getElementById("ziel").focus(); return; }
      senden({ was, ids, ziel });
    });
  });
});
