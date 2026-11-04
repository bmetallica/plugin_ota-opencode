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
  if (alt) {
    melden(alt, sessionStorage.getItem("gedaechtnis.fehler") === "1");
    sessionStorage.removeItem("gedaechtnis.meldung");
    sessionStorage.removeItem("gedaechtnis.fehler");
  }

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

  document.querySelectorAll("[data-zugriff-nehmen]").forEach((b) => {
    b.addEventListener("click", () => senden({ was: "zugriff_nehmen", quelle: b.dataset.zugriffNehmen }))
  })

  // Dokumente: hochladen (eine Datei je Anfrage, der Name in einer Kopfzeile),
  // „für alle Sitzungen“, Freigabe je Sitzung, löschen.
  const datei = document.getElementById("hochladen");
  const ablage = document.querySelector("[data-ablage]");
  async function hochladen(dateien) {
    const meldungen = [];
    let fehler = false;
    for (const d of dateien) {
      melden(d.name + " …");
      try {
        const r = await fetch(basis + "dokument?sprache=" + sprache, {
          method: "POST", body: d, credentials: "same-origin",
          headers: { "X-OTA-Ansicht": "1", "X-Dateiname": encodeURIComponent(d.name),
                     "Content-Type": "application/octet-stream" },
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j.ok) fehler = true;
        meldungen.push(j.meldung || j.detail || (d.name + ": " + document.body.dataset.fehler));
      } catch (e) {
        fehler = true;
        meldungen.push(d.name + ": " + document.body.dataset.fehler);
      }
    }
    sessionStorage.setItem("gedaechtnis.meldung", meldungen.join(" · "));
    if (fehler) sessionStorage.setItem("gedaechtnis.fehler", "1");
    location.reload();
  }
  datei?.addEventListener("change", () => { if (datei.files.length) hochladen([...datei.files]); });
  ablage?.addEventListener("dragover", (ev) => { ev.preventDefault(); ablage.classList.add("ueber"); });
  ablage?.addEventListener("dragleave", () => ablage.classList.remove("ueber"));
  ablage?.addEventListener("drop", (ev) => {
    ev.preventDefault();
    ablage.classList.remove("ueber");
    if (ev.dataTransfer.files.length) hochladen([...ev.dataTransfer.files]);
  });
  document.querySelectorAll(".dokument").forEach((li) => {
    li.querySelector("[data-fuer-alle]")?.addEventListener("change", (ev) => {
      senden({ was: "dokument_fuer_alle", id: li.dataset.id, wert: ev.currentTarget.checked });
    });
    const weg = li.querySelector("[data-dok-loeschen]");
    weg?.addEventListener("click", () => {
      if (confirm(weg.dataset.frage)) senden({ was: "dokument_loeschen", id: li.dataset.id });
    });
  });
  document.querySelectorAll("[data-freigabe]").forEach((k) => {
    k.addEventListener("change", () => senden({ was: "dokument_freigabe", id: k.dataset.freigabe, wert: k.checked }));
  });
  // Solange etwas ausgelesen wird, regelmässig nachsehen.
  if (document.querySelector("[data-verarbeitung]")) setTimeout(() => location.reload(), 4000);

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
