# Echo — Releases

Offizielle Download- und Auto-Update-Distribution für **Echo** (Subunit).

Dieses Repository enthält kompilierte Installer und das Updater-Manifest
(`latest.json`). Der Quellcode ist proprietär und nicht Teil dieses Repositories.

**Downloads:** [Releases](../../releases/latest) — macOS (Apple Silicon), Windows x64, Windows ARM64, Linux x86_64.

**Auto-Update-Feed:** `https://github.com/Subunit-UG/echo-releases/releases/latest/download/latest.json`

Das Repository liegt seit dem 30.10.2026 in der Organisation `Subunit-UG` (vorher Benutzerkonto
`subunit-ai`). Bereits installierte Apps fragen noch die alte Adresse
`https://github.com/subunit-ai/echo-releases/releases/latest/download/latest.json` ab; GitHub leitet
sie auf dieses Repository weiter.

Echo-Releases enthalten ausschließlich die Diktat-Engine (`local-whisper` bzw.
`local-whisper-gpu`). Meeting-Aufzeichnung, Diarisierung und Voiceprints gehören
zum eigenständigen SCAI-Meet-Plugin und sind kein Echo-Releasebestandteil mehr.

Automatische Poller bauen ausschließlich suffixlose Stable-Tags wie `v0.5.166`;
dieser Kanal veröffentlicht weiterhin atomar als normalen GitHub-`Latest` und
speist Echos Standard-Updater.

Manuelle Builds, Poller und externe Dispatches teilen ein workflowweites
Release-Schloss. Überschneiden sich zwei Trigger, wartet der spätere Lauf bis zum
vollständigen Publish und wird anschließend zum No-op; Assets und Publish-Job
können dadurch nie parallel auf denselben Draft zugreifen.

Ein signierter Testkandidat kann ausschließlich manuell mit `release_mode=draft_rc`
und einem exakt passenden Tag wie `v0.5.166-rc.1` gebaut werden. Er bleibt als
nicht öffentlich gelisteter GitHub-Draft mit gesetztem Prerelease-Merkmal bestehen
und wird niemals `Latest`. Dadurch kann er von einem Maintainer geprüft und manuell
installiert werden, ohne reguläre Echo-Installationen zu aktualisieren.

Vor jedem Publish beziehungsweise RC-Abschluss verifiziert der Workflow die vier
Updater-Artefakte und ihre vier `.sig`-Dateien kryptografisch gegen den Public Key
der exakt gebauten Echo-Quelle. IDs und SHA-256-Werte aller acht Dateien werden bis
zur finalen Publish-Entscheidung gebunden. Ab Version 1.0 einschließlich Release
Candidates kommt die native Plattform-Trust-Prüfung für macOS und Windows hinzu.
Die öffentlichen Signer-Identitäten, Prüfungen und noch fehlenden externen
Voraussetzungen stehen in [`docs/PLATFORM-TRUST.md`](docs/PLATFORM-TRUST.md).

© Subunit. Alle Rechte vorbehalten.

### Held Stable acceptance build (same-byte promotion)

A manual `build.yml` run with `release_mode=draft_stable` takes an **exact private
source commit SHA** and an explicit suffixless `release_tag`, for example
`v0.6.11`. Do not create that tag in the private source repository beforehand.
All app versions must already match the label. The existing four-platform build,
updater signatures and version-dependent native trust gates still apply. The
result remains `draft=true`, `prerelease=false`; GitHub Latest stays unchanged.
For 0.x, this means verified updater signatures, **not** a claim of Apple
notarization or Windows Authenticode.

The fresh draft receives an `ECHO_HELD_STABLE_V1` marker before any build. Its
assets cannot be rebuilt through another build run; both the poller and normal
stable mode refuse to publish it. A failed held build stays held for review.
The private tag poller does not discover tags created in this public repository.

After all build jobs succeed, record the numeric draft ID, exact source SHA and
independently read back the SHA-256 of `held-stable-receipt.json`. This receipt
pins the build run/attempt, workflow revision, complete asset IDs/sizes/hashes,
source and updater public key. Native device acceptance must use those exact
held binaries. **Only after explicit release approval**, manually dispatch
`promote-stable.yml` with those pins and the same version label. It independently
checks the completed canonical four-platform build, all asset bytes, IDs,
manifest and four cryptographic signatures; v1+ also repeats the native trust
gates. It publishes the same files without rebuilding, replacing or uploading
any assets. Create the matching private source tag only after published-release
readback succeeds; the poller will then see the existing release and do nothing.
