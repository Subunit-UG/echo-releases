#!/usr/bin/env python3
"""Öffentliches Repo: PR-Texte, Commits und neue Zeilen bleiben neutral.

Dieses Repo ist öffentlich. Interne Zitate mit Datum, Verweise auf das interne Regelwerk und
Wissenssystem sowie Gerätenamen und Heimnetz-Adressen gehören nicht hinein.
Geprüft werden PR-Titel und -Text (aus PR_TITLE/PR_BODY), alle Commit-Nachrichten und
Autor-/Committer-Adressen zwischen Basis und Head sowie jede hinzugefügte Zeile im Diff.
Gelöschte Zeilen zählen nicht, damit Bereinigungen durchgehen.

Die Muster nutzen Zeichenklassen wie [-] oder [@], damit diese Datei sich nicht selbst meldet.
Dieselbe Datei liegt byte-gleich in scai-releases und echo-releases.

Exit 0 = sauber, 1 = Fund, 2 = Aufruf- oder Git-Fehler (fail-closed).
"""
import argparse
import os
import re
import subprocess
import sys

MUSTER = [
    ("internes Zitat mit Datum", re.compile(r"\bTJ[ -](?:20\d\d-\d\d-\d\d|\d\d?\.\d\d?\.)")),
    ("internes Zitat", re.compile(r"“\s*\(TJ\b")),
    ("internes Regelwerk", re.compile(r"\bC[a]non\b|\bS[I]CHERHEITSPROGRAMM\b|\bincidents\/20\d\d")),
    ("internes Wissenssystem", re.compile(r"\bu1[-]brain\b|~\/subunit\/|\bworkspace\/(?:docs|starts|handoffs|scripts|memory|config)\/|\bhandoffs\/")),
    ("Gerätename oder Heimnetz", re.compile(r"fritz\.box|MacBook[-]Pro|\btomsync[@]")),
]


def funde(text):
    out = []
    for nr, zeile in enumerate(text.splitlines(), 1):
        for art, rx in MUSTER:
            m = rx.search(zeile)
            if m:
                out.append((nr, art, m.group(0)))
    return out


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def pruefen(base, head):
    meldungen = []
    for quelle in ("PR_TITLE", "PR_BODY"):
        for nr, art, treffer in funde(os.environ.get(quelle, "")):
            meldungen.append(f"{quelle} Zeile {nr}: {art} ({treffer})")

    log = git("log", "--format=%x1e%H%x1f%ae%x1f%ce%x1f%B", f"{base}..{head}")
    for eintrag in filter(None, log.split("\x1e")):
        sha, autor, committer, nachricht = (eintrag.split("\x1f", 3) + ["", "", ""])[:4]
        for nr, art, treffer in funde(f"{autor}\n{committer}"):
            meldungen.append(f"Commit {sha[:12]} Adresse: {art} ({treffer})")
        for nr, art, treffer in funde(nachricht):
            meldungen.append(f"Commit {sha[:12]} Nachricht Zeile {nr}: {art} ({treffer})")

    # Hunk-Köpfe geben die Zahl der neuen Zeilen vor; nur so wird eine hinzugefügte Zeile, die
    # selbst mit "++ " beginnt, nicht als Dateikopf gelesen.
    diff = git("-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff", "--unified=0",
               "--text", f"{base}...{head}")
    datei, zeile, rest = "?", 0, 0
    for z in diff.splitlines():
        if rest > 0:
            if z.startswith("+"):
                for _, art, treffer in funde(z[1:]):
                    meldungen.append(f"{datei}:{zeile}: {art} ({treffer})")
                    print(f"::error file={datei},line={zeile}::{art}: bitte neutral formulieren")
                zeile += 1
                rest -= 1
            continue
        if z.startswith("+++ "):
            datei = z[4:].strip('"')
            datei = datei[2:] if datei.startswith("b/") else datei
        elif z.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", z)
            if not m:
                raise subprocess.CalledProcessError(1, "git diff", stderr=f"unlesbarer Hunk-Kopf: {z[:80]}")
            zeile, rest = int(m.group(1)), int(m.group(2) or 1)
    return meldungen


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    a = ap.parse_args()
    try:
        meldungen = pruefen(a.base, a.head)
    except subprocess.CalledProcessError as e:
        print(f"::error::Git-Fehler, Prüfung nicht möglich: {(e.stderr or '').strip()[:300]}")
        return 2
    if meldungen:
        print("Öffentliches Repo: interne Wendungen gefunden (neutral formulieren, Zitate und interne Pfade weglassen):")
        for m in meldungen:
            print(f"  - {m}")
        return 1
    print("Öffentliche Texte neutral: PR-Text, Commits und neue Zeilen ohne Fund.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
