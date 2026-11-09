"""Tests für verify_public_neutral.py (byte-gleich in scai-releases und echo-releases).

Jeder Fall baut ein Wegwerf-Repo mit Basis- und Head-Commit und ruft das Skript wie die CI auf.
Interne Wendungen in den Testdaten werden aus Teilen zusammengesetzt ("T" "J ..."), damit diese
Datei die Prüfung im eigenen PR nicht auslöst.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HIER = Path(__file__).resolve().parent
SKRIPT = next(p for p in (HIER / "verify_public_neutral.py", HIER.parent / "scripts" / "verify_public_neutral.py") if p.exists())

ZITAT = "T" "J 2026-10-24"
ZITAT_KURZ = "(T" "J 04.11.2026)"
REGELWERK = "Ca" "non Release-Gate"
WISSEN = "~/sub" "unit/u1" "-brain/workspace/" "starts/x.md"
GERAET = "tomsync" "@MacBook" "-Pro-von-Tom.fritz" ".box"
NEUTRAL = "Kanonische Pfade, Canonical URL, TJ als Kürzel, workspace/foo und handoff-frei."


class Repo:
    def __init__(self):
        self.pfad = Path(tempfile.mkdtemp(prefix="neutral-"))
        self.git("init", "-q", "-b", "main")
        self.schreiben("README.md", "Start\nzu löschen: " + ZITAT + "\n")
        self.base = self.commit("init")

    def git(self, *args, env=None):
        e = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.org",
             "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.org",
             "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(self.pfad), **(env or {})}
        return subprocess.run(["git", "-C", str(self.pfad), *args], check=True, capture_output=True,
                              text=True, env=e).stdout.strip()

    def schreiben(self, name, inhalt):
        (self.pfad / name).parent.mkdir(parents=True, exist_ok=True)
        (self.pfad / name).write_text(inhalt, encoding="utf-8")
        self.git("add", "-A")

    def commit(self, nachricht, env=None):
        self.git("commit", "-q", "--allow-empty", "-m", nachricht, env=env)
        return self.git("rev-parse", "HEAD")

    def pruefen(self, head, titel="ci: neutral", text="Neutrale Beschreibung."):
        e = {**os.environ, "PR_TITLE": titel, "PR_BODY": text}
        return subprocess.run(["python3", str(SKRIPT), "--base", self.base, "--head", head],
                              cwd=self.pfad, capture_output=True, text=True, env=e)

    def weg(self):
        shutil.rmtree(self.pfad, ignore_errors=True)


class NeutralTest(unittest.TestCase):
    def setUp(self):
        self.r = Repo()

    def tearDown(self):
        self.r.weg()

    def assertFund(self, p, wort):
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(wort, p.stdout)

    def test_sauberer_pr_ist_gruen(self):
        self.r.schreiben("a.txt", NEUTRAL + "\n")
        p = self.r.pruefen(self.r.commit("feat: neutral"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_zitat_im_titel(self):
        self.assertFund(self.r.pruefen(self.r.commit("x"), titel="fix: " + ZITAT), "PR_TITLE")

    def test_kurzes_zitat_im_text(self):
        self.assertFund(self.r.pruefen(self.r.commit("x"), text="Warum\n" + ZITAT_KURZ), "PR_BODY Zeile 2")

    def test_wissenssystem_im_text(self):
        self.assertFund(self.r.pruefen(self.r.commit("x"), text="Lesen: " + WISSEN), "internes Wissenssystem")

    def test_regelwerk_in_commit_nachricht(self):
        self.assertFund(self.r.pruefen(self.r.commit("ci: laut " + REGELWERK)), "Nachricht")

    def test_geraet_in_co_author(self):
        head = self.r.commit("ci: x\n\nCo-authored-by: Tom <" + GERAET + ">")
        self.assertFund(self.r.pruefen(head), "Gerätename")

    def test_geraet_als_autor(self):
        head = self.r.commit("ci: x", env={"GIT_AUTHOR_EMAIL": GERAET})
        self.assertFund(self.r.pruefen(head), "Adresse")

    def test_neue_zeile_mit_zeilennummer(self):
        self.r.schreiben(".github/workflows/a.yml", "name: a\n# " + ZITAT + "\n")
        p = self.r.pruefen(self.r.commit("ci: a"))
        self.assertFund(p, ".github/workflows/a.yml:2")
        self.assertIn("::error file=.github/workflows/a.yml,line=2::", p.stdout)

    def test_neue_zeile_die_wie_dateikopf_aussieht(self):
        self.r.schreiben("b.txt", "++ b/tarnung " + ZITAT + "\n")
        self.assertFund(self.r.pruefen(self.r.commit("x")), "b.txt:1")

    def test_dateiname_mit_umlaut(self):
        self.r.schreiben("prüfung.md", "# " + WISSEN + "\n")
        self.assertFund(self.r.pruefen(self.r.commit("x")), "prüfung.md:1")

    def test_geloeschte_zeile_zaehlt_nicht(self):
        self.r.schreiben("README.md", "Start\n")
        p = self.r.pruefen(self.r.commit("chore: bereinigt"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_eigene_dateien_melden_sich_nicht(self):
        for f in ("verify_public_neutral.py", "test_verify_public_neutral.py"):
            shutil.copy(HIER / f if (HIER / f).exists() else SKRIPT.parent / f, self.r.pfad / f)
        self.r.git("add", "-A")
        p = self.r.pruefen(self.r.commit("ci: Prüfung"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_git_fehler_ist_fail_closed(self):
        p = self.r.pruefen("0" * 40)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
