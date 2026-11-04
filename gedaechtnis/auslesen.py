"""Text aus Dokumenten holen — in einem eigenen Prozess.

`python -m gedaechtnis.auslesen <typ>` liest die Datei von stdin und
schreibt JSON nach stdout: `{"teile": [{"quelle": "S. 3", "text": "…"}]}`
oder `{"fehler": "…"}`. Der Aufrufer (dokumente.py) setzt Grenzen für Zeit
und Speicher: Eine Datei aus dem Netz ist fremder Code für die Bibliotheken,
die sie lesen, und eine kaputte oder böswillige soll höchstens diesen
Prozess umwerfen, nicht den Dienst.

Ohne Texterkennung: Ein gescanntes PDF hat keine Textschicht und wird mit
Hinweis abgelehnt.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import zipfile
from html.parser import HTMLParser

# Was als Text gilt, und was ein ZIP-Archiv ist (DOCX, XLSX, PPTX, ODF).
TEXT = ("txt", "md", "csv", "tsv", "json", "xml", "yaml", "yml", "log", "rst", "ini", "toml")
ZIP = ("docx", "xlsx", "pptx", "odt", "ods", "odp")
TYPEN = ("pdf", "html", "htm", *TEXT, *ZIP)
# Entpackt höchstens so viel: Schutz vor ZIP-Bomben.
ZIP_GRENZE = 300 * 1024 * 1024
ZEILEN_JE_BLATT = 20000


class Fehler(Exception):
    pass


def _zip_pruefen(daten: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(daten)) as z:
            gesamt = sum(i.file_size for i in z.infolist())
    except zipfile.BadZipFile as exc:
        raise Fehler("Die Datei ist beschädigt oder kein Office-Dokument.") from exc
    if gesamt > ZIP_GRENZE:
        raise Fehler(f"Entpackt über {ZIP_GRENZE // 1048576} MB — zu groß zum Auslesen.")


def _dekodieren(daten: bytes) -> str:
    for kod in ("utf-8-sig", "cp1252"):
        try:
            return daten.decode(kod)
        except UnicodeDecodeError:
            continue
    return daten.decode("latin-1")


def _text(daten: bytes, typ: str) -> list[dict[str, str]]:
    text = _dekodieren(daten)
    if typ in ("csv", "tsv"):
        trenner = "\t" if typ == "tsv" else (csv.Sniffer().sniff(text[:4096]).delimiter
                                             if text.strip() else ",")
        zeilen = [" | ".join(z) for z in csv.reader(io.StringIO(text), delimiter=trenner)]
        return [{"quelle": "", "text": "\n".join(zeilen[:ZEILEN_JE_BLATT])}]
    if typ == "md":
        # Überschriften als Quelle: „## Installation“ steht dann am Treffer.
        teile, quelle, puffer = [], "", []
        for zeile in text.splitlines():
            m = re.match(r"#{1,6}\s+(.+)", zeile)
            if m:
                if puffer:
                    teile.append({"quelle": quelle, "text": "\n".join(puffer)})
                quelle, puffer = m.group(1).strip()[:120], [zeile]
            else:
                puffer.append(zeile)
        if puffer:
            teile.append({"quelle": quelle, "text": "\n".join(puffer)})
        return teile
    return [{"quelle": "", "text": text}]


class _Html(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.teile: list[str] = []
        self._aus = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._aus += 1
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.teile.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._aus:
            self._aus -= 1

    def handle_data(self, data):
        if not self._aus:
            self.teile.append(data)


def _html(daten: bytes) -> list[dict[str, str]]:
    p = _Html()
    p.feed(_dekodieren(daten))
    return [{"quelle": "", "text": re.sub(r"\n\s*\n+", "\n\n", "".join(p.teile))}]


def _pdf(daten: bytes) -> list[dict[str, str]]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        r = PdfReader(io.BytesIO(daten))
        if r.is_encrypted:
            raise Fehler("Das PDF ist verschlüsselt.")
        teile = []
        for i, seite in enumerate(r.pages, 1):
            teile.append({"quelle": f"S. {i}", "text": seite.extract_text() or ""})
    except PdfReadError as exc:
        raise Fehler(f"Das PDF lässt sich nicht lesen: {exc}") from exc
    zeichen = sum(len(t["text"].strip()) for t in teile)
    if teile and zeichen < 20 * len(teile):
        raise Fehler("Das PDF hat (fast) keine Textschicht — vermutlich gescannt. "
                     "Texterkennung gibt es hier nicht.")
    return teile


def _docx(daten: bytes) -> list[dict[str, str]]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    d = docx.Document(io.BytesIO(daten))
    teile, quelle, puffer = [], "", []
    # Absätze und Tabellen in der Reihenfolge des Dokuments.
    for block in d.element.body.iterchildren():
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(block, d)
            stil = (p.style.name if p.style is not None else "") or ""
            if p.text.strip() and re.match(r"(Heading|Überschrift|Title|Titel)", stil):
                if puffer:
                    teile.append({"quelle": quelle, "text": "\n".join(puffer)})
                quelle, puffer = p.text.strip()[:120], []
            puffer.append(p.text)
        elif tag == "tbl":
            for zeile in Table(block, d).rows:
                puffer.append(" | ".join(z.text.strip() for z in zeile.cells))
    if puffer:
        teile.append({"quelle": quelle, "text": "\n".join(puffer)})
    return teile


def _xlsx(daten: bytes) -> list[dict[str, str]]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(daten), read_only=True, data_only=True)
    teile = []
    for blatt in wb.worksheets:
        zeilen = []
        for zeile in blatt.iter_rows(values_only=True):
            werte = ["" if v is None else str(v) for v in zeile]
            if any(w.strip() for w in werte):
                zeilen.append(" | ".join(werte).rstrip(" |"))
            if len(zeilen) >= ZEILEN_JE_BLATT:
                break
        teile.append({"quelle": f"Blatt {blatt.title}", "text": "\n".join(zeilen)})
    return teile


def _pptx(daten: bytes) -> list[dict[str, str]]:
    from pptx import Presentation
    teile = []
    for i, folie in enumerate(Presentation(io.BytesIO(daten)).slides, 1):
        texte = [f.text_frame.text for f in folie.shapes if f.has_text_frame and f.text_frame.text.strip()]
        teile.append({"quelle": f"Folie {i}", "text": "\n".join(texte)})
    return teile


def _odf(daten: bytes, typ: str) -> list[dict[str, str]]:
    from odf import teletype
    from odf.opendocument import load
    from odf.table import Table, TableCell, TableRow
    from odf.text import P
    d = load(io.BytesIO(daten))
    if typ == "ods":
        teile = []
        for tabelle in d.spreadsheet.getElementsByType(Table):
            zeilen = []
            for zeile in tabelle.getElementsByType(TableRow):
                werte = [teletype.extractText(z) for z in zeile.getElementsByType(TableCell)]
                if any(w.strip() for w in werte):
                    zeilen.append(" | ".join(werte).rstrip(" |"))
                if len(zeilen) >= ZEILEN_JE_BLATT:
                    break
            teile.append({"quelle": f"Blatt {tabelle.getAttribute('name')}", "text": "\n".join(zeilen)})
        return teile
    if typ == "odp":
        from odf.draw import Page
        return [{"quelle": f"Folie {i}", "text": "\n".join(teletype.extractText(p) for p in s.getElementsByType(P))}
                for i, s in enumerate(d.presentation.getElementsByType(Page), 1)]
    teile, quelle, puffer = [], "", []
    # Überschriften (text:h) beginnen einen Abschnitt; Absätze, Listen und
    # Tabellen gehören zum laufenden.
    for el in d.text.childNodes:
        name = getattr(el, "qname", (None, ""))[1]
        if name == "h":
            if puffer:
                teile.append({"quelle": quelle, "text": "\n".join(puffer)})
            quelle, puffer = teletype.extractText(el).strip()[:120], [teletype.extractText(el)]
        elif name in ("p", "list", "table", "section"):
            puffer.append(teletype.extractText(el))
    if puffer:
        teile.append({"quelle": quelle, "text": "\n".join(puffer)})
    return teile


def auslesen(daten: bytes, typ: str) -> list[dict[str, str]]:
    if typ not in TYPEN:
        raise Fehler("Dieses Format wird nicht unterstützt.")
    if typ in ZIP:
        _zip_pruefen(daten)
    if typ == "pdf":
        teile = _pdf(daten)
    elif typ in ("html", "htm"):
        teile = _html(daten)
    elif typ == "docx":
        teile = _docx(daten)
    elif typ == "xlsx":
        teile = _xlsx(daten)
    elif typ == "pptx":
        teile = _pptx(daten)
    elif typ in ("odt", "ods", "odp"):
        teile = _odf(daten, typ)
    else:
        teile = _text(daten, typ)
    teile = [{"quelle": t["quelle"], "text": t["text"].replace("\x00", "")} for t in teile if t["text"].strip()]
    if not teile:
        raise Fehler("Im Dokument steht kein Text.")
    return teile


def main() -> None:
    typ = sys.argv[1] if len(sys.argv) > 1 else ""
    daten = sys.stdin.buffer.read()
    try:
        erg = {"teile": auslesen(daten, typ)}
    except Fehler as exc:
        erg = {"fehler": str(exc)}
    except Exception as exc:  # noqa: BLE001 — jede Bibliothek wirft anders
        erg = {"fehler": f"Lässt sich nicht auslesen ({type(exc).__name__}: {str(exc)[:200]})"}
    sys.stdout.write(json.dumps(erg, ensure_ascii=False))


if __name__ == "__main__":
    main()
