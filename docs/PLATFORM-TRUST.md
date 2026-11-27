# Release-Trust-Gates für Echo

## Updater-Signaturen für alle Versionen

Nach den vier Build-Matrix-Jobs fixiert `verify_updater_trust` den Quell-Tag auf
denselben Commit, den jeder Build geprüft hat. Aus dessen
`src-tauri/tauri.conf.json` liest der Job ausschließlich den eingebetteten
Updater-Public-Key. Er lädt diese vier Paare über die numerische Release- und
Asset-ID aus dem kanonischen Draft:

- `echo_aarch64.app.tar.gz` und `.sig`;
- `echo_<version>_amd64.AppImage` und `.sig`;
- `echo_<version>_x64-setup.exe` und `.sig`;
- `echo_<version>_arm64-setup.exe` und `.sig`.

`scripts/verify-updater-signatures.py` dekodiert das Tauri-Signaturformat und
ruft Minisign mit dem öffentlichen Schlüssel auf. Außerdem prüft es den
kryptografisch geschützten Dateinamen im Trusted Comment. Tauri signiert das
macOS-Updaterpaket intern als `echo.app.tar.gz`; Linux und beide Windows-Pakete
tragen ihren vollständigen, versionsgebundenen Asset-Namen. Damit liefert der
Linux-Pfad ohne Ausführung des AppImage eine überprüfte Versionsbindung.

Der Job gibt für alle vier Artefakte und alle vier Signaturen die exakte Asset-ID
und SHA-256 sowie den Hash des Public Keys weiter. `platform_trust` lehnt einen
fehlgeschlagenen, abgebrochenen oder übersprungenen Updater-Job für jede Version
ab. `publish` lädt dieselben acht IDs neu, vergleicht alle Hashes und prüft die IDs
nach dem Upload von `latest.json` nochmals. `latest.json` wird über die numerische
Release-ID hochgeladen; die Tag-basierte Asset-Auswahl ist damit auch an dieser
Stelle ausgeschlossen.

## Native Plattformprüfung

macOS: Jede Version, auch mit Major `0`, braucht eine erfolgreiche native Prüfung
der hochgeladenen macOS-Artefakte (Developer-ID-Signatur mit Team-ID, Notarisierung,
Stapling, Gatekeeper).

Windows: Der Release-Workflow verlangt die native Prüfung für jede Version ab
`v1.0.0`, einschließlich Tags wie `v1.0.0-rc.1`.

Beide Entscheidungen kommen ausschließlich aus `scripts/release_policy.sh`
(`requires_macos_trust`, `requires_platform_trust`).

## Öffentliche Repository-Variablen

In den GitHub-Actions-Variablen des Release-Repositories müssen diese öffentlichen
Identitäten gesetzt sein (die Apple-Werte für jeden Build, der Windows-Wert ab 1.0):

- `APPLE_TEAM_ID`: exakter Apple-Team-Identifier der erwarteten Developer-ID-Signatur.
- `APPLE_SIGNING_AUTHORITY`: vollständiger Wert der erwarteten Leaf-Zeile
  `Authority=` aus `codesign --display --verbose=4`, ohne das Präfix `Authority=`.
- `WINDOWS_PUBLISHER`: exakter X.509-Subject des erwarteten Authenticode-Signerzertifikats.

Diese Werte sind keine Signing-Credentials. Der Workflow liest damit nur die
Identität bereits signierter Dateien. Fehlende oder abweichende Werte stoppen
den Lauf. Die Wahl und Einrichtung eines Signing-Anbieters ist nicht Teil
dieses Gates.

## Geprüfte Artefakte

Nach Abschluss aller vier Build-Matrix-Jobs lesen zwei Trust-Jobs den reservierten
Release über seine numerische `release_id`. Beide verlangen weiterhin exakt den
erwarteten Tag, Draft-Status und Prerelease-Status sowie genau ein Release mit
diesem Tag. Assets werden über ihre numerische Asset-ID geladen; Wildcards werden
nicht zur Auswahl eines Installers verwendet.

Der macOS-Job prüft beide ausgelieferten App-Kopien — im Updater-Archiv und im
DMG — und verlangt:

- `echo_aarch64.app.tar.gz`, einschließlich sicherer Extraktion und der App mit
  Bundle-ID `ai.subunit.echo`;
- `echo_<version>_aarch64.dmg` sowie die darin enthaltene App;
- Signaturkette, exakte Team-ID und Authority, sicheren Timestamp, Apple-Stapling
  und Gatekeeper-Bewertung;
- `CFBundleShortVersionString` und `CFBundleVersion` exakt wie den SemVer-Tag,
  einschließlich eines möglichen `-rc.N`-Suffixes.

Der Windows-Job prüft:

- `echo_<version>_x64-setup.exe`;
- `echo_<version>_arm64-setup.exe`;
- Authenticode-Vertrauen, exakten Publisher und ein vertrauenswürdiges
  Timestamp-Zertifikat mit Windows SignTool und PowerShell;
- die String-Metadaten `ProductVersion` und `FileVersion` exakt wie den
  SemVer-Tag, einschließlich eines möglichen `-rc.N`-Suffixes. Die rein
  numerische PE-Version wird dafür nicht als Ersatz verwendet.

Die nativen Jobs geben SHA-256-Werte der vier geprüften OS-Artefakte weiter.
`platform_trust` verlangt für macOS-Updaterarchiv und beide Windows-Installer
zusätzlich Gleichheit mit den Hashes des Updater-Gates. Der Publish-Job prüft die
Bytes erneut. Für 0.x entfällt ausschließlich die native Windows-Prüfung; die
native macOS-Prüfung und die kryptografische Vier-Plattform-Updaterprüfung bleiben
Pflicht.

## Externe Voraussetzungen vor GA

Die Gates wählen oder beschaffen keinen Signing-Anbieter und lesen keine privaten
Schlüssel. Ein 1.0-Kandidat bleibt deshalb geschlossen, bis der Build tatsächlich
Developer-ID-signierte und von Apple notarisierte macOS-Artefakte sowie
Authenticode-signierte Windows-Artefakte erzeugt und die drei öffentlichen
Identitätsvariablen gesetzt sind. Welche Windows-Zertifikats- oder HSM-Lösung
verwendet wird, ist bewusst nicht vorgegeben; die Freigabe hängt vom überprüften
Publisher, Timestamp und Artefakt ab.

## Quellen

- [GitHub REST API für Releases und Release Assets](https://docs.github.com/en/rest/releases)
- [GitHub Actions: Jobs und `needs`](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-jobs)
- [Apple: Notarizing macOS software before distribution](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
- [Apple: Customizing the notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow)
- [Microsoft: SignTool](https://learn.microsoft.com/windows/win32/seccrypto/signtool)
- [Microsoft: Get-AuthenticodeSignature](https://learn.microsoft.com/powershell/module/microsoft.powershell.security/get-authenticodesignature)
- [Tauri 2: Updater-Signaturen](https://v2.tauri.app/plugin/updater/#signing-updates)
- [Tauri 2.11.2: macOS-Bundle-Metadaten](https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.11.2/crates/tauri-bundler/src/bundle/macos/app.rs)
- [Tauri 2.11.2: NSIS-Version-Metadaten](https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.11.2/crates/tauri-bundler/src/bundle/windows/nsis/installer.nsi)
- [Minisign: kryptografisch geschützter Trusted Comment](https://github.com/jedisct1/minisign/blob/master/src/minisign.c)
