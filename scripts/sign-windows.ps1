param([Parameter(Mandatory = $true)][string] $ArtifactPath)
$ErrorActionPreference = 'Stop'
# Tauri invokes this for application binaries and the finished installer.
# One fixed-format line per file goes to a public facts file, so a failure inside
# the confidential build step still names its cause without private diagnostics.
function Write-SigningFact([string] $Exit) {
  if (-not $env:RUNNER_TEMP) { return }
  # Diagnostics only: a failure here must never change the signing outcome.
  try {
    $name = [IO.Path]::GetFileName($ArtifactPath) -replace '[^A-Za-z0-9._-]', '_'
    $found = Get-AuthenticodeSignature -LiteralPath $ArtifactPath
    $subjectMatches = [bool]($found.SignerCertificate -and $found.SignerCertificate.Subject -ceq $env:WINDOWS_PUBLISHER)
    "SIGNFACT name=$name exit=$Exit status=$($found.Status) subjectMatches=$subjectMatches timestamped=$([bool]$found.TimeStamperCertificate)" |
      Add-Content -Encoding utf8NoBOM (Join-Path $env:RUNNER_TEMP 'windows-signing-facts.txt')
  } catch {
    Write-Warning 'Signing facts could not be recorded'
  }
}
switch -CaseSensitive ($env:WINDOWS_SIGNING_BACKEND) {
  'artifact-signing' {
    & $env:SIGNTOOL_PATH sign /v /debug /fd SHA256 /tr http://timestamp.acs.microsoft.com /td SHA256 /dlib $env:ARTIFACT_SIGNING_DLIB_PATH /dmdf $env:WINDOWS_SIGNING_CONFIG $ArtifactPath
  }
  'azure-key-vault' {
    & $env:RELIC_PATH sign --file $ArtifactPath --key azure --config $env:WINDOWS_SIGNING_CONFIG --digest sha256
  }
  default { throw 'Unsupported signing backend' }
}
$exitCode = $LASTEXITCODE
Write-SigningFact "$exitCode"
if ($exitCode -ne 0) { throw 'Windows signing command failed' }
# Verify each file before Tauri can place it inside an installer or upload it.
$signature = Get-AuthenticodeSignature -LiteralPath $ArtifactPath
if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or
    $signature.SignerCertificate.Subject -cne $env:WINDOWS_PUBLISHER -or
    -not $signature.TimeStamperCertificate) {
  throw 'Signed file failed Authenticode, publisher or timestamp verification'
}
