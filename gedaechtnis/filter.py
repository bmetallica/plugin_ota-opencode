"""Was nicht ins Gedächtnis gehört: Zugangsdaten.

Ein Eintrag kommt bei jedem Abruf wieder in den Kontext des Modells und
liegt in der Datenbank und den Sicherungen. Ein Schlüssel darin wäre an
drei Stellen, an denen ihn niemand vermutet. Lieber einmal zu viel
abgewiesen — die Meldung sagt dem Modell, was es stattdessen tun soll.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_MUSTER = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "privater Schlüssel"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "JWT"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}"), "GitHub-Token"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"), "GitLab-Token"),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"), "API-Schlüssel"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "Slack-Token"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "AWS-Schlüssel"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "Google-API-Schlüssel"),
    (re.compile(r"(?i)\b(?:password|passwort|passwd|pwd|secret|geheimnis|token|api[_-]?key)\s*[:=]\s*"
                r"[\"']?[^\s\"']{8,}"), "Zugangsdaten"),
    (re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]{4,}@"), "Passwort in einer Adresse"),
]


def _entropie(s: str) -> float:
    zaehler = Counter(s)
    return -sum(n / len(s) * math.log2(n / len(s)) for n in zaehler.values())


def geheimnis(text: str) -> str | None:
    """Der Grund, wenn `text` nach Zugangsdaten aussieht — sonst None."""
    for muster, grund in _MUSTER:
        if muster.search(text):
            return grund
    # Lange Zeichenketten ohne Leerzeichen mit hoher Entropie: zufällige
    # Schlüssel. Hashes von Commits (40 hex) sind zu gleichförmig dafür;
    # Pfade und Adressen haben Schrägstriche.
    for wort in re.findall(r"[A-Za-z0-9+/_=-]{32,}", text):
        if "/" in wort:
            continue
        if re.fullmatch(r"[0-9a-f]+", wort):
            continue
        if _entropie(wort) > 4.3 and re.search(r"[A-Z]", wort) and re.search(r"[0-9]", wort):
            return "zufälliger Schlüssel"
    return None


SCHWARZ = "[entfernt]"


def schwaerzen(text: str) -> tuple[str, int]:
    """Für Dokumente: Zugangsdaten durch [entfernt] ersetzen, statt das ganze
    Dokument abzulehnen. Gibt den Text und die Zahl der Stellen zurück."""
    n = 0
    for muster, _ in _MUSTER:
        text, k = muster.subn(SCHWARZ, text)
        n += k

    def zufall(m: re.Match) -> str:
        nonlocal n
        wort = m.group(0)
        if "/" in wort or re.fullmatch(r"[0-9a-f]+", wort):
            return wort
        if _entropie(wort) > 4.3 and re.search(r"[A-Z]", wort) and re.search(r"[0-9]", wort):
            n += 1
            return SCHWARZ
        return wort
    text = re.sub(r"[A-Za-z0-9+/_=-]{32,}", zufall, text)
    return text, n
