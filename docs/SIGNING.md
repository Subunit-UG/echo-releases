# Release-Signierung vorbereiten

Diese Datei beschreibt ausschließlich die Signierung in `.github/workflows/build.yml`.
Alle Einstellungen unten sind Namen; Konto-Endpunkte, Zertifikat-Subjects und
Schlüsselwerte gehören ausschließlich in die Repository-Einstellungen.

## Windows

Ohne Auswahlvariable bleibt Echo unsigniert. Die vorhandenen nativen Trust-Gates ab v1 bleiben unverändert und verhindern weiterhin einen unsignierten v1-Release.

| `WINDOWS_SIGNING_BACKEND` | Verhalten |
| --- | --- |
| nicht gesetzt / `none` | Unverändert: keine Authenticode-Konfiguration und kein Azure-Login. Unbekannte Backends brechen ab. |
| `artifact-signing` | Azure-OIDC-Anmeldung, x64-SignTool mit Microsoft.ArtifactSigning.Client 1.0.146 und Laufzeit-`metadata.json`; Signieren während des Tauri-Bundlings. |
| `azure-key-vault` | `relic` 8.2.0 (x64, Download mit festem SHA-256) signiert während des Tauri-Bundlings über den HSM-Schlüssel im Key Vault. |

Cloud-Signierung benutzt eine zur Laufzeit erzeugte Override-Datei
`src-tauri/tauri.windows-signing.json` und Tauris `--config`. Die eingecheckte
App-Konfiguration bleibt unverändert. `signCommand` verwendet ein Argument-Array,
damit Pfade mit Leerzeichen intakt bleiben. Nach jeder Signatur muss
`Get-AuthenticodeSignature` einen gültigen Signer, den **exakten vollständigen
Subject** aus `WINDOWS_PUBLISHER` und einen Zeitstempel melden. Eine weitere
Prüfung erfasst Anwendung und NSIS-Installer nach dem Build. Jeder Fehler stoppt
den Build und damit die nachfolgenden Veröffentlichungsgates.

Für Artifact Signing ruft das eingecheckte `scripts/sign-windows.ps1` SignTool
mit `/dlib`, `/dmdf`, `/fd SHA256`, `/tr http://timestamp.acs.microsoft.com`
und `/td SHA256` auf. Das offizielle NuGet-Paket ist auf 1.0.146 mit SHA-256
gepinnt, die x64-.NET-Laufzeit auf 8.0.32 mit SHA-512. Auch auf ARM64 werden
x64-SignTool, x64-dlib und x64-Laufzeit zusammen benutzt. `metadata.json` entsteht
nur im Job aus den drei `ARTIFACT_SIGNING_*`-Variablen. `ExcludeCredentials`
schließt Environment-, Workload-/Managed-Identity-, Cache-, IDE-, PowerShell-,
Developer-CLI- und interaktive Anmeldung aus. Ausschließlich `AzureCliCredential`
nutzt die vorherige `azure/login`-OIDC-Sitzung; kein Client-Secret erforderlich.
Die Community-CLI wird nicht verwendet.

Für Key Vault wurde `relic` gewählt: Es unterstützt ausdrücklich
Azure-CLI-Zugang und Workload Federation; `pin: ""` wählt die von
`azure/login` per OIDC erzeugte CLI-Sitzung. Es braucht kein Client-Secret.
Signaturdigest: SHA-256; RFC-3161-Dienst:
`http://timestamp.acs.microsoft.com`. Der private Schlüssel bleibt im HSM.
OIDC-Rechte stehen im Build-/Signierjob, weil Tauri beim Bundling signiert.
Die Matrix teilt diese Jobrechte; der Azure-Login läuft ausschließlich in den
Windows-Zeilen mit ausdrücklich ausgewähltem Cloud-Backend.


### Später zu setzen: nur Namen

Repository-Variablen:

- `WINDOWS_SIGNING_BACKEND`
- `WINDOWS_PUBLISHER`
- Für Artifact Signing: `ARTIFACT_SIGNING_ENDPOINT`,
  `ARTIFACT_SIGNING_ACCOUNT`, `ARTIFACT_SIGNING_CERTIFICATE_PROFILE`
- Für Key Vault: `AZURE_KEY_VAULT_CERTIFICATE_URL` (Zertifikat-URL, keine Secret-URL)

Repository-Secrets für Azure-OIDC:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

Azure muss eine Federated Credential für das konkrete Release-Repository und
den erlaubten Ref erhalten. Die notwendigen Rollen werden auf das jeweilige
Zertifikatsprofil bzw. den Key Vault beschränkt: Artifact Signing Certificate
Profile Signer bzw. Key Vault Certificate User und Key Vault Crypto User.
Kein `AZURE_CLIENT_SECRET` anlegen.


## macOS

Ohne `APPLE_NOTARIZE_METHOD` bleibt der tatsächliche heutige Ablauf unverändert: bestehende macOS-Signatur, **keine Notarisierungs-Zugangsdaten**. `apple-id` ist die klassische Methode, aktiviert Notarisierung hier aber erst bei ausdrücklich gesetzter Auswahlvariable. `api-key` aktiviert den API-Key-Weg. Diese Unterscheidung bewahrt den unspezifizierten Standard, obwohl Echo bislang überhaupt keine Apple-ID-Notarisierung konfiguriert hatte.

Bei ausdrücklich gewählter Notarisierung ist eine
`Developer ID Application`-Identität erforderlich. Die bestehenden Secrets
`APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD` und
`APPLE_SIGNING_IDENTITY` bleiben erforderlich.

| Methode | Zusätzliche Secrets |
| --- | --- |
| `apple-id` | `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` |
| `api-key` | `APPLE_API_ISSUER`, `APPLE_API_KEY` (Key-ID), `APPLE_API_PRIVATE_KEY` (vollständiger roher .p8-Inhalt) |

Auswahlvariable: `APPLE_NOTARIZE_METHOD`. Im API-Key-Pfad wird die .p8 nur im
Job unter `RUNNER_TEMP` mit `umask 077` und Modus `0600` geschrieben.
`APPLE_API_KEY_PATH` entsteht ausschließlich zur Laufzeit; kein Pfad-Secret
anlegen. Nur die zur ausgewählten Methode gehörenden Authentifizierungsvariablen
werden an Tauri exportiert. Ein `always()`-Schritt entfernt die Datei auch nach
fehlgeschlagenen Builds. Native Signatur- und Stapler-Prüfungen bleiben Pflicht.

Bei ausdrücklich aktivierter Notarisierung wird das Developer-ID-Zertifikat nicht als selbstsignierte Root importiert. Der bisherige Trust-Import bleibt im unveränderten Standardweg erhalten. Die bestehenden Variablen `APPLE_TEAM_ID`, `APPLE_SIGNING_AUTHORITY` und `WINDOWS_PUBLISHER` für die v1-Trust-Gates bleiben erforderlich. Ein Identitätswechsel kann bestehende macOS-Freigaben betreffen; die Nutzerwirkung ist **nicht verifiziert**.

## Umstellen und zurückkehren

1. Azure-Federation/Rollen bzw. Developer-ID-Zertifikat und Apple-API-Key außerhalb
   dieses PRs bereitstellen. Obige Variablen/Secrets hinterlegen; es wurden im
   Rahmen dieses Auftrags keine Einstellungen oder Geheimnisse gelesen/gesetzt.
2. Vor Windows-Aktivierung die zugangsfreie ARM-/x64-Probe auswerten.
   Auch das erfolgreiche Erreichen eines Authentifizierungsfehlers beweist noch
   keine funktionsfähige Cloud-Signatur mit echtem Konto und OIDC.
3. Backend bzw. Notarisierungsmethode ausdrücklich auswählen. Später einen
   separat freigegebenen Release-Lauf verwenden und Signaturen, Publisher,
   Zeitstempel, Stapler und die vorhandenen Updater-Gates prüfen.
4. Rückweg: Auswahlvariablen auf die bisherigen Standardwerte zurücknehmen bzw.
   entfernen. Echo kehrt ohne Auswahlvariable zu unsigniertem Windows und macOS ohne Notarisierung zurück. Die bestehenden v1-/Market-Ready-Gates bleiben aktiv und
   werden durch einen Rückweg auf unsignierte Artefakte nicht umgangen.

## Probe und offene Verifikation

Die separate [Echo-Probe](https://github.com/subunit-ai/echo-releases/blob/feat/signing-ready-20261024/.github/workflows/signing-probe.yml)
läuft per `pull_request` ausschließlich bei Änderungen an der Probe-Datei oder
`scripts/sign-windows.ps1`, außerdem manuell per `workflow_dispatch`, sobald
GitHub den Workflow auf dem Default-Branch registriert hat. Die einzige
Berechtigung ist `contents: read`; Checkout speichert keine Zugangsdaten.
Keine Secrets, kein Azure-Login und kein OIDC-Token werden verwendet.

Auf `windows-latest` und `windows-11-arm` lädt sie dieselben gepinnten Pakete
wie der Build und ruft das eingecheckte Signierskript auf einer wegwerfbaren
EXE-Kopie auf. Ein leeres Azure-CLI-Profil verhindert Zugangsdatenzugriff.
Ein expliziter Authentifizierungsfehler bei unveränderten EXE-Bytes ist das
Erfolgskriterium; DLL-Ladefehler und falsche Architektur schlagen fehl.
`relic` 8.2.0 wird unabhängig per `--version` und `--help` gestartet.
Es werden keine Release-Dateien signiert.

[Probe vom 25.10.2026: erfolgreich](https://github.com/subunit-ai/echo-releases/actions/runs/40195087632)
auf dem Code-Stand `c79cdd6d8dce4f4a28868b5fd21e1bdcf0d8abb2`:

| Runner | SignTool + dlib | relic |
| --- | --- | --- |
| `windows-latest` | `signtool+dlib=success` | `relic=success`, Version 8.2.0 |
| `windows-11-arm` | `signtool+dlib=success` (x64-Emulation) | `relic=success`, Version 8.2.0 (x64-Emulation) |

Beide Logs enthalten `AzureCliCredential authentication failed: Please run 'az login' to set up account`
und `PROBE signtool+dlib PASS (x64; authentication failure reached; dummy EXE unchanged)`.
Die dlib wird also tatsächlich über SignTool aufgerufen und erreicht ihre
Authentifizierung. Der erste Lauf war wegen eines nicht zurückgesetzten,
erwarteten nativen Fehlercodes rot; die Probe setzt ihn nach allen Prüfungen
auf null. Echte Signierfehler bleiben im Produktionsskript unverändert fatal.

Tatsächliche OIDC-Anmeldung, Zertifikatszugriff, Signierung, Zeitstempel,
vollständiges Plattform-Bundling und Apple-Notarisierung sind **nicht verifiziert**.

### Plan B, falls ARM-Werkzeuge nicht starten

In einem gesonderten Umbau: auf ARM64 nur kompilieren und die benötigten
ARM64-Anwendungsdateien, Sidecars und Bundle-Eingaben an einen Windows-x64-Job
übergeben. Dort zuerst die ARM64-Anwendung und Sidecars signieren, danach mit
dem ARM64-Target den NSIS-Installer paketieren und auch diesen signieren.
Nur den fertigen Installer nachträglich zu signieren würde eine unsignierte
eingebettete Anwendung nicht beheben. Den fertigen Installer anschließend
auf ARM64 für Paket-Smoke und Evidenz über die endgültigen Bytes prüfen.

Authenticode verändert die Dateibytes. Deshalb erst **nach** der letzten
Authenticode-Signatur die Tauri-Updater-Signatur `.sig` erzeugen bzw. neu erzeugen.
`latest.json` muss diese endgültige Signatur referenzieren. Sämtliche SHA-256-Werte
in Trust-Evidenz, Manifesten und Prüfsummen sowie die Asset-IDs müssen an die
endgültig hochgeladenen Dateien gebunden werden. Bereits veröffentlichte Assets
oder Signaturen dürfen nicht ersetzt werden. Der aktuelle PR setzt diesen
Job-Umbau nicht um und verändert weder die Updater- noch die Lockstep-Logik.

## Lokale Prüfung dieses Standes

`actionlint` 1.7.12 besteht für Build- und Probe-Workflow mit `-shellcheck=`.
Der vollständige Build-Lauf meldet unverändert den bereits auf main vorhandenen
SC2015-Hinweis; der Probe-Workflow besteht auch den vollständigen Lauf.
Die vollständige Echo-Suite umfasst 71 Tests: lokal 68 bestanden, drei wegen
fehlendem `minisign` übersprungen. Im [GitHub-Contract-Lauf](https://github.com/subunit-ai/echo-releases/actions/runs/40195087639)
bestehen alle 71 Tests ohne Auslassung; die synthetischen Hosted-Prüfungen
bestehen ebenfalls auf allen vier Build-Runnern. Der Azure-Vertrag erlaubt Azure-Aktionen und
-Variablen ausschließlich in Schritten mit der exakten Windows-/Backend-Bedingung.
`id-token: write` ist ausschließlich auf Job-Ebene im Build-/Signierjob erlaubt;
GitHub bietet keine Berechtigungen pro Schritt. Workflow-weite Berechtigungen
und OIDC in anderen Jobs werden abgewiesen. Negative Tests erfassen alle drei
Azure-Schritte sowie beide OIDC-Regeln und eine globale Azure-Variable.
Der eigentliche Vertragstest wurde zusätzlich mit drei Änderungen im Speicher
jeweils rot ausgeführt: Login-Bedingung entfernt, workflow-weites `id-token`
eingefügt und `id-token` in einem anderen Job eingefügt. Keine Workflow-Datei
wurde für diese Mutationsprüfung verändert.

Ein zusätzlicher Vergleich bestätigt unveränderte Prepare-, Reserve-, Trust-
und Publish-Jobs und identische Tauri-Argumente ohne Backend-Auswahl. Ein
synthetischer Bash-Lauf bestätigt API-Key-Dateimodus 0600, ausschließliche
API-Variablen und anschließendes Entfernen; keine echten Zugangsdaten verwendet.

## Quellen

- [Tauri: Windows-Signierung und signCommand](https://v2.tauri.app/distribute/sign/windows/)
- [Tauri: signCommand mit Argument-Array](https://v2.tauri.app/reference/config/#customsigncommandconfig)
- [Microsoft.ArtifactSigning.Client 1.0.146: dlib und Voraussetzungen](https://www.nuget.org/packages/Microsoft.ArtifactSigning.Client/1.0.146)
- [.NET 8: offizielle Versionen und Download-Prüfsummen](https://builds.dotnet.microsoft.com/dotnet/release-metadata/8.0/releases.json)
- [relic 8.2.0: Azure-Authentifizierung](https://github.com/sassoftware/relic/blob/v8.2.0/doc/azure.md)
- [Azure Login: OIDC](https://github.com/Azure/login/tree/v2#login-with-openid-connect-oidc-recommended)
- [Microsoft: SignTool, dlib und Authentifizierung](https://learn.microsoft.com/en-us/azure/artifact-signing/how-to-signing-integrations)
- [GitHub: Dispatch und Default-Branch](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)
