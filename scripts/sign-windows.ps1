param([Parameter(Mandatory = $true)][string] $ArtifactPath)
$ErrorActionPreference = 'Stop'
# Tauri invokes this for application binaries and the finished installer.
switch -CaseSensitive ($env:WINDOWS_SIGNING_BACKEND) {
  'artifact-signing' {
    & $env:SIGNTOOL_PATH sign /v /debug /fd SHA256 /tr http://timestamp.acs.microsoft.com /td SHA256 /dlib $env:ARTIFACT_SIGNING_DLIB_PATH /dmdf $env:WINDOWS_SIGNING_CONFIG $ArtifactPath
  }
  'azure-key-vault' {
    & $env:RELIC_PATH sign --file $ArtifactPath --key azure --config $env:WINDOWS_SIGNING_CONFIG --digest sha256
  }
  default { throw 'Unsupported signing backend' }
}
if ($LASTEXITCODE -ne 0) { throw 'Windows signing command failed' }
# Verify each file before Tauri can place it inside an installer or upload it.
$signature = Get-AuthenticodeSignature -LiteralPath $ArtifactPath
if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or
    $signature.SignerCertificate.Subject -cne $env:WINDOWS_PUBLISHER -or
    -not $signature.TimeStamperCertificate) {
  throw 'Signed file failed Authenticode, publisher or timestamp verification'
}
