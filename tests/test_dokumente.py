"""Dokumente: auslesen, schwärzen, freigeben, durchsuchen — und was nicht geht.

Die Dateien entstehen hier mit denselben Bibliotheken, die sie lesen; ein
PDF von Hand, mit und ohne Textschicht.
"""
from __future__ import annotations

import io
import time
import uuid
import zipfile

import pytest

from gedaechtnis import auslesen, dokumente, filter

from conftest import _ansicht, _dienst, _mcp, _platz


def _pdf(text: str | None) -> bytes:
    """Ein PDF mit einer Seite; ohne Text wie ein Scan."""
    inhalt = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
    objekte = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(inhalt)).encode() + b" >>\nstream\n" + inhalt + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    aus = io.BytesIO()
    aus.write(b"%PDF-1.4\n")
    stellen = []
    for i, o in enumerate(objekte, 1):
        stellen.append(aus.tell())
        aus.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = aus.tell()
    aus.write(f"xref\n0 {len(objekte) + 1}\n0000000000 65535 f \n".encode())
    for s in stellen:
        aus.write(f"{s:010d} 00000 n \n".encode())
    aus.write(f"trailer\n<< /Size {len(objekte) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return aus.getvalue()


def _docx() -> bytes:
    import docx
    d = docx.Document()
    d.add_heading("Urlaubsregelung", level=1)
    d.add_paragraph("Urlaub wird spätestens vier Wochen vorher im Portal beantragt.")
    d.add_heading("Zugang", level=1)
    d.add_paragraph("Testzugang: password=Sommer2026Geheim!")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Team", "Ansprechpartner"
    t.cell(1, 0).text, t.cell(1, 1).text = "Kartografie", "Frau Lindqvist"
    aus = io.BytesIO()
    d.save(aus)
    return aus.getvalue()


def _xlsx() -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Server"
    ws.append(["Name", "Adresse", "Zweck"])
    ws.append(["geoserver", "10.20.30.40", "WMS-Dienste der Landesvermessung"])
    aus = io.BytesIO()
    wb.save(aus)
    return aus.getvalue()


def _odt() -> bytes:
    from odf.opendocument import OpenDocumentText
    from odf.text import H, P
    d = OpenDocumentText()
    d.text.addElement(H(outlinelevel=1, text="Namenskonvention"))
    d.text.addElement(P(text="Layer heißen immer klein mit Unterstrich, etwa flurstuecke_alt."))
    aus = io.BytesIO()
    d.save(aus)
    return aus.getvalue()


def _pptx() -> bytes:
    from pptx import Presentation
    p = Presentation()
    folie = p.slides.add_slide(p.slide_layouts[1])
    folie.shapes.title.text = "Roadmap"
    folie.placeholders[1].text = "Im dritten Quartal kommt die Vektorkachel-Auslieferung."
    aus = io.BytesIO()
    p.save(aus)
    return aus.getvalue()


def _hoch(c, kopf, name: str, daten: bytes):
    return c.post("/ansicht/dokument", headers={**kopf, "X-Dateiname": name}, content=daten)


def _warten(c, kopf, n: int) -> list[dict]:
    """Bis n Dokumente fertig oder gescheitert sind."""
    m = kopf["X-OTA-Nutzer"]
    for _ in range(150):
        liste = dokumente.liste(m)
        if sum(d["status"] in ("fertig", "fehler") for d in liste) >= n:
            return liste
        time.sleep(0.2)
    pytest.fail(f"nicht verarbeitet: {[(d['name'], d['status']) for d in dokumente.liste(m)]}")


def test_auslesen_formate():
    assert "vier Wochen" in auslesen.auslesen(_docx(), "docx")[0]["text"]
    teile = auslesen.auslesen(_docx(), "docx")
    assert [t["quelle"] for t in teile] == ["Urlaubsregelung", "Zugang"]
    assert "Lindqvist" in teile[1]["text"]
    assert "Landesvermessung" in auslesen.auslesen(_xlsx(), "xlsx")[0]["text"]
    assert auslesen.auslesen(_xlsx(), "xlsx")[0]["quelle"] == "Blatt Server"
    assert "flurstuecke_alt" in auslesen.auslesen(_odt(), "odt")[0]["text"]
    assert "Vektorkachel" in auslesen.auslesen(_pptx(), "pptx")[0]["text"]
    assert auslesen.auslesen(_pdf("Hallo aus dem PDF mit genug Text darin"), "pdf")[0]["quelle"] == "S. 1"
    with pytest.raises(auslesen.Fehler, match="Textschicht"):
        auslesen.auslesen(_pdf(None), "pdf")
    md = auslesen.auslesen(b"# Eins\nerster\n## Zwei\nzweiter", "md")
    assert [t["quelle"] for t in md] == ["Eins", "Zwei"]
    assert "Inhalt" in auslesen.auslesen(b"<html><script>x()</script><p>Inhalt</p></html>", "html")[0]["text"]


def test_zip_bombe_wird_abgewiesen():
    aus = io.BytesIO()
    with zipfile.ZipFile(aus, "w", zipfile.ZIP_DEFLATED) as z:
        with z.open("word/document.xml", "w") as f:
            null = b"\0" * (1 << 20)
            for _ in range(auslesen.ZIP_GRENZE // len(null) + 2):
                f.write(null)
    with pytest.raises(auslesen.Fehler, match="zu groß"):
        auslesen.auslesen(aus.getvalue(), "docx")


def test_schwaerzen():
    text, n = filter.schwaerzen("Login mit password=Sommer2026Geheim! und ghp_" + "a1" * 15)
    assert n == 2 and "Sommer2026" not in text and "ghp_" not in text


def test_zerlegen_ueberlappt():
    teile = [{"quelle": "S. 1", "text": "\n\n".join(f"Absatz {i} " + "wort " * 60 for i in range(10))}]
    stuecke = dokumente.zerlegen(teile)
    assert len(stuecke) > 2 and all(len(s) <= dokumente.ABSCHNITT * 2 + 200 for _, s in stuecke)
    assert all(q == "S. 1" for q, _ in stuecke)


def test_bibliothek_freigabe_und_suche(c):
    m = str(uuid.uuid4())
    platz, token = _platz(c, m, "greta")
    _mcp(c, token, "memory_recall", ota_sitzung="ses_D1")
    _mcp(c, token, "memory_recall", ota_sitzung="ses_D2")
    kopf = {**_ansicht(m, "greta", [platz]), "X-OTA-Ansicht": "1"}
    # Ohne die Kopfzeile der Ansicht: abgewiesen.
    assert c.post("/ansicht/dokument", headers={**_ansicht(m, "greta", [platz]), "X-Dateiname": "a.txt"},
                  content=b"x").status_code == 403
    for name, daten in (("Regeln.docx", _docx()), ("Server.xlsx", _xlsx()), ("Konvention.odt", _odt()),
                        ("Scan.pdf", _pdf(None))):
        assert _hoch(c, kopf, name, daten).json()["ok"], name
    assert not _hoch(c, kopf, "nochmal.docx", _docx()).json()["ok"]  # gleicher Inhalt
    assert "alte Office-Format" in _hoch(c, kopf, "alt.doc", b"x").json()["meldung"]
    liste = {d["name"]: d for d in _warten(c, kopf, 4)}
    assert liste["Scan.pdf"]["status"] == "fehler" and "Textschicht" in liste["Scan.pdf"]["fehler"]
    assert liste["Regeln.docx"]["status"] == "fertig" and liste["Regeln.docx"]["geschwaerzt"] == 1
    # Noch nichts freigegeben: opencode findet nichts.
    assert "No documents" in _mcp(c, token, "document_search", query="Urlaub", ota_sitzung="ses_D1")
    seite = c.get("/ansicht/konto?s=_dokumente", headers=kopf).text
    assert "Regeln.docx" in seite and "nicht lesbar" in seite
    regeln = str(liste["Regeln.docx"]["id"])
    assert c.post("/ansicht/aktion", headers=kopf, json={"was": "dokument_freigabe", "sitzung": "ses_D1",
                                                          "id": regeln, "wert": True}).json()["ok"]
    r = _mcp(c, token, "document_search", query="Wie beantrage ich Urlaub", ota_sitzung="ses_D1")
    assert "vier Wochen" in r and "[Regeln.docx, Urlaubsregelung]" in r
    assert "Sommer2026" not in _mcp(c, token, "document_search", query="Testzugang password", ota_sitzung="ses_D1")
    assert "Regeln.docx" in _mcp(c, token, "document_list", ota_sitzung="ses_D1")
    # Die andere Sitzung sieht es nicht — bis „für alle Sitzungen“.
    assert "No documents" in _mcp(c, token, "document_search", query="Urlaub", ota_sitzung="ses_D2")
    server = str(liste["Server.xlsx"]["id"])
    assert c.post("/ansicht/aktion", headers=kopf, json={"was": "dokument_fuer_alle", "id": server,
                                                          "wert": True}).json()["ok"]
    assert "10.20.30.40" in _mcp(c, token, "document_search", query="geoserver Adresse", ota_sitzung="ses_D2")
    assert "10.20.30.40" in _mcp(c, token, "document_search", query="geoserver", ota_sitzung="ses_ganz_neu")
    assert "Urlaub" not in _mcp(c, token, "document_search", query="Urlaub vier Wochen", ota_sitzung="ses_D2")
    # Auf der Seite einer Sitzung: angehakt, was sie lesen darf.
    assert 'data-freigabe="' + regeln + '"' in c.get("/ansicht/konto?s=ses_D1", headers=kopf).text
    # Herunterladen: das Original.
    assert c.get(f"/ansicht/dokument/{regeln}", headers=kopf).content == bytes(dokumente.original(m, regeln)["original"])
    # Ein anderer Mensch sieht nichts davon, auch nicht über die Kennung.
    fremd = str(uuid.uuid4())
    pf, tf = _platz(c, fremd, "fremd4")
    assert "No documents" in _mcp(c, tf, "document_search", query="geoserver", ota_sitzung="ses_D2")
    assert c.get(f"/ansicht/dokument/{regeln}", headers=_ansicht(fremd, "fremd4", [pf])).status_code == 404
    # Löschen.
    assert c.post("/ansicht/aktion", headers=kopf, json={"was": "dokument_loeschen", "id": server}).json()["ok"]
    assert "geoserver" not in _mcp(c, token, "document_search", query="geoserver", ota_sitzung="ses_D2")


def test_grenzen(c):
    m = str(uuid.uuid4())
    platz, _ = _platz(c, m, "hanna")
    kopf = {**_ansicht(m, "hanna", [platz]), "X-OTA-Ansicht": "1"}
    assert c.put("/ota/v1/konfiguration", headers=_dienst(), json={"dokument_mb": 1}).status_code == 200
    try:
        r = _hoch(c, kopf, "gross.txt", b"x" * (1024 * 1024 + 10))
        assert r.status_code == 413
        assert _hoch(c, kopf, "klein.txt", "Kurzer Text über Tests".encode()).json()["ok"]
    finally:
        c.put("/ota/v1/konfiguration", headers=_dienst(), json={"dokument_mb": 25})
