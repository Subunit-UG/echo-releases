# FIX-BERICHT — AUD-0135 / A07-E-2

Stand: 2026-11-06

## Ergebnis

Der bisher gemischte Release-Job ist in drei Sicherheitszonen getrennt:

1. `build_unsigned` führt privaten Quellcode und npm-Lifecycle-Code aus. Der Job hat ausschließlich `contents: read`, kein `release-signing`-Environment, kein OIDC und keine Signier- oder Release-Secrets.
2. `bind_unsigned_artifacts` bindet die vier Plattformarchive in ein festes `MANIFEST.sha256` und lädt sie als ein unveränderliches GitHub-Actions-Artefakt hoch.
3. `build` läuft als Signier-/Publish-Phase auf neuen Runnern. Nur dieser Job hat `environment: release-signing`, `contents: write`, `id-token: write` und die benötigten Signier-/Publish-Secrets. Er checkt keinen privaten Quellcode aus und führt keine private lokale Action oder private npm-Lifecycle-Kommandos aus.

Die E-2-Eindämmung `if: ${{ false }}` ist damit durch eine aktive, gesplittete und fail-closed Pipeline ersetzt.

## Geänderte und neue Dateien

- `.github/workflows/build.yml`
  - unprivilegierter Build mit `tauri build --no-bundle`;
  - kuratierter Bundle-Handoff ohne privaten Rust-/JavaScript-Quellbaum;
  - separater Binder mit Plattformmanifest;
  - frische privilegierte Runner für Bundle, Signatur, Notarisierung und Upload;
  - exakter Checkout der öffentlichen CI-Kontrollen über `${{ github.sha }}`;
  - gepinnte öffentliche Tauri-CLI, Installation ohne npm-Skripte;
  - private Tauri-Hooks und benutzerdefinierte NSIS-Kontrollpfade werden in der privilegierten Phase fail-closed überschrieben;
  - Authenticode-, Notarisierungs- und bestehende Updater-Prüfungen bleiben vor dem Publish erhalten.
- `tests/test_confidential_output.py`
  - Job-Parser und Strukturvertrag für die Autoritätstrennung;
  - Mutationen für `release-signing`, Schreibrecht, OIDC, Publish-Secrets, private Lifecycle-Ausführung und fehlende Digest-Bindung;
  - Binder muss read-only bleiben;
  - privilegierte Phase muss vom Binder abhängen und darf keinen privaten Kontrollpfad laden.
- `FIX-BERICHT.md`
  - dieser Nachweis mit Rückweg und Voraussetzungen.

Keine Datei wurde gelöscht. Die bestehende lokale Confidential-Tauri-Action bleibt im Repository erhalten, wird vom privilegierten Release-Job aber nicht mehr verwendet.

## Digest-gebundener Artefakt-Handoff

Jeder unprivilegierte Plattform-Runner lädt genau ein kurzlebiges, mit Lauf-ID und Laufversuch benanntes `upload-artifact`-v4-Artefakt hoch. Der Binder lädt ausschließlich diese vier Artefakte, erzeugt `MANIFEST.sha256`, prüft es unmittelbar und lädt Manifest plus Archive als ein neues gebundenes Artefakt hoch.

Die privilegierte Phase erhält aus dem Binder sowohl die numerische `artifact-id` als auch `artifact-digest`. Sie lädt genau diese ID über die GitHub-API, vergleicht den SHA-256 des Downloads mit dem von GitHub ausgegebenen Digest, prüft anschließend `MANIFEST.sha256` und extrahiert erst danach mit Pfad-, Link- und Typprüfung nach `$RUNNER_TEMP`. Name oder veränderlicher Workspace-Pfad allein reicht deshalb nicht als Vertrauensbeleg.

Die Signierphase checkt nur die öffentliche CI-Revision aus. Das Bundle wird durch die exakt gepinnte öffentliche Tauri-CLI aus dem kompilierten Binary und den erforderlichen Bundle-Ressourcen erzeugt. Ein privater Commit kann weder `_release-ci` ersetzen noch einen später privilegiert gestarteten npm-Hook einschleusen.

## Lokale Verifikation

- `python3 tests/test_confidential_output.py` — **grün: 13 Tests**.
- `actionlint .github/workflows/build.yml` — **grün**.
- `git diff --check` — **grün**.
- `python3 -m pytest tests/test_confidential_output.py` — nicht ausführbar, weil `pytest` lokal nicht installiert ist; bewusst keine neue Abhängigkeit installiert. Derselbe Testbestand lief direkt über sein `unittest`-Entry-Point vollständig grün.
- `python3 tests/test_release_workflow.py` — **42 von 44 grün**. Zwei bestehende Strukturannahmen beschreiben noch die alte Ein-Job-Architektur: `build` dürfe nur von `[prepare, reserve]` abhängen, und alle macOS-Bedingungen müssten `matrix.platform` verwenden. Der sichere Split benötigt zusätzlich `bind_unsigned_artifacts`; die frische Signiermatrix verwendet absichtlich `matrix.runner`, damit der bestehende Inventartest weiterhin nur die vier privaten Build-Runner zählt. Diese Tests wurden wegen der Auftragsgrenze nicht geändert.
- Kein GitHub-Workflow und kein echter Release wurde ausgelöst. Runner-, Signatur- und Notarisierungsverhalten ist daher lokal strukturell, aber noch nicht auf Hosted Runnern nachgewiesen.

Der Mutationsnachweis deckt insbesondere ab: Wird dem privaten Build Environment-, Schreib-, OIDC- oder Publish-Autorität gegeben, wird der Test rot. Wird der privilegierten Phase ein privates npm-Lifecycle-Kommando oder die lokale Tauri-Action hinzugefügt, wird der Test ebenfalls rot. Entfernt man die Digest-Kontrolle, wird er rot.

## Rückweg

Bei einem Problem im ersten Hosted-Runner-Lauf darf der alte gemischte Job nicht wiederhergestellt werden. Der sichere Sofortrückweg ist die E-2-Eindämmung auf dem heutigen privilegierten `build`-Job (`if: ${{ false }}`); soll auch der unprivilegierte Build keine Runnerzeit verbrauchen, wird zusätzlich `build_unsigned` deaktiviert. Damit bleibt Release-Autorität eingefroren, ohne die geschlossene Sicherheitsgrenze wieder zu öffnen. Der vollständige vorherige Stand bleibt außerdem über Git rekonstruierbar, wird aber wegen AUD-0135 nicht als Release-Weg empfohlen.

## Auslieferungsurteil und Voraussetzungen

**Gestaffelt ausliefern, nicht direkt als Stable.** Der Stand ist strukturell gegen AUD-0135 abgesichert, braucht vor einer Veröffentlichung aber das unabhängige u1-Gate und einen echten `draft_rc`-Probelauf. Der Probelauf muss alle vier Runner, den Handoff-Digest, Tauri-Updater-Signaturen, Windows-Authenticode sowie macOS-Signatur und Notarisierung erfolgreich belegen. Erst dieser Nachweis macht den Split sicher release-fähig.

Voraussetzungen für den ersten Lauf:

- GitHub-Environment `release-signing` bleibt auf den freigegebenen Release-Zweig und die vorgesehenen Freigaberegeln begrenzt.
- Der private Deploy-Key bleibt read-only und ist nur für Quellauflösung bzw. `build_unsigned` nötig; er gehört nicht in die privilegierte Phase.
- Tauri-Updater-, Apple- und Windows-Cloud-Signierdaten liegen ausschließlich im `release-signing`-Environment. Werte dürfen nicht in Logs oder Dateien geschrieben werden.
- Die früher als Secrets eingespeisten Sentry-DSNs werden dem privaten Build bewusst nicht mehr gegeben. Falls Telemetrie sie zwingend benötigt, müssen sie separat als ausdrücklich öffentliche Client-Konfiguration klassifiziert und geprüft werden; sie dürfen nicht wieder aus dem Signier-Environment in den Build wandern.
- GitHub stellt für die Signierphase `actions: read`, `contents: write` und OIDC bereit; der private Build bleibt bei `contents: read`.
- Runner verfügbar: `ubuntu-24.04`, `windows-latest`, `windows-11-arm`, `macos-15`; auf macOS muss `/Applications/Xcode_16.4.app` vorhanden sein.
- Ausgehender Zugriff auf die bereits verwendeten, gepinnten GitHub-/npm-/NuGet-/Apple-/Windows-Signierquellen ist erlaubt.
- Die beiden veralteten Annahmen in `tests/test_release_workflow.py` werden in einem separaten, ausdrücklich freigegebenen Test-Update auf die neue Jobgrenze umgestellt und danach die komplette Release-Vertragssuite erneut ausgeführt.

## Nachtrag: test_release_workflow.py

Die zwei noch auf die frühere Ein-Job-Topologie zugeschnittenen Tests sind an den sicheren Job-Split angepasst:

- `test_matrix_uploads_only_to_reserved_release_id` prüft jetzt den Signier-/Publish-Job `build` auf `needs: [prepare, reserve, bind_unsigned_artifacts]`. Zusätzlich bleibt der Upload ausdrücklich an `needs.reserve.outputs.release_id` gebunden. Die Quell-Commit-Verifikation wurde nicht entfernt oder gelockert: Der Test prüft `[ "$(git rev-parse HEAD)" = "$SOURCE_COMMIT" ]` jetzt gezielt im unprivilegierten Job `build_unsigned`, an dem der private Quellcode tatsächlich ausgecheckt wird; die zweite Commit-Prüfung im späteren Verifikationspfad bleibt ebenfalls abgedeckt.
- `test_one_current_macos_runner_and_no_stale_platform_condition` prüft beide Vierer-Matrizen explizit: `build_unsigned` über `platform`, den frischen Signierjob `build` über `runner`. Beide enthalten genau einen macOS-Eintrag `macos-15`; beide wählen `/Applications/Xcode_16.4.app`. Im Signierjob ist `matrix.platform` verboten und die neuen `matrix.runner`-Bedingungen werden ausdrücklich verlangt, sodass eine nur teilweise umgestellte Signiermatrix weiter rot wird.

Lokale Verifikation nach der Anpassung:

- `python3 tests/test_release_workflow.py` — **grün: 44 Tests**.
- `python3 tests/test_confidential_output.py` — **grün: 13 Tests**.

An Workflow-Dateien oder `tests/test_confidential_output.py` wurde in diesem Nachtrag nichts geändert. Es wurde nichts gelöscht.

