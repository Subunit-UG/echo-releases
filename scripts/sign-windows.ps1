param([Parameter(Mandatory = $true)][string] $ArtifactPath)
$ErrorActionPreference = 'Stop'
# Tauri invokes this for application binaries and the finished installer.
# One fixed-format line per attempt goes to a public facts file, so a failure inside
# the confidential build step still names its cause without private diagnostics.
function Write-SigningFact([string] $Exit, [int] $Attempt, [string] $Detail) {
  if (-not $env:RUNNER_TEMP) { return }
  # Diagnostics only: a failure here must never change the signing outcome.
  try {
    $name = [IO.Path]::GetFileName($ArtifactPath) -replace '[^A-Za-z0-9._-]', '_'
    $found = Get-AuthenticodeSignature -LiteralPath $ArtifactPath
    $subjectMatches = [bool]($found.SignerCertificate -and $found.SignerCertificate.Subject -ceq $env:WINDOWS_PUBLISHER)
    $line = "SIGNFACT name=$name exit=$Exit status=$($found.Status) subjectMatches=$subjectMatches timestamped=$([bool]$found.TimeStamperCertificate)"
    if ($Exit -ne '0') {
      $reason = ($Detail -replace '[^A-Za-z0-9._ -]', '_').Trim()
      if ($reason.Length -gt 160) { $reason = $reason.Substring(0, 160) }
      $line = "SIGNFAIL name=$name attempt=$Attempt exit=$Exit error=$reason"
    }
    $line | Add-Content -Encoding utf8NoBOM (Join-Path $env:RUNNER_TEMP 'windows-signing-facts.txt')
  } catch {
    Write-Warning 'Signing facts could not be recorded'
  }
}

# The signing service and the RFC 3161 timestamp server can fail transiently;
# Microsoft recommends retrying. Every attempt starts from the unsigned input
# because signtool only writes the file after a complete, timestamped signature.
$maxAttempts = 3
for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
  $PSNativeCommandUseErrorActionPreference = $false
  switch -CaseSensitive ($env:WINDOWS_SIGNING_BACKEND) {
    'artifact-signing' {
      $output = & $env:SIGNTOOL_PATH sign /v /debug /fd SHA256 /tr http://timestamp.acs.microsoft.com /td SHA256 /dlib $env:ARTIFACT_SIGNING_DLIB_PATH /dmdf $env:WINDOWS_SIGNING_CONFIG $ArtifactPath 2>&1
    }
    'azure-key-vault' {
      $output = & $env:RELIC_PATH sign --file $ArtifactPath --key azure --config $env:WINDOWS_SIGNING_CONFIG --digest sha256 2>&1
    }
    default { throw 'Unsupported signing backend' }
  }
  $exitCode = $LASTEXITCODE
  $output | ForEach-Object { Write-Host $_ }
  $detail = ($output | Where-Object { "$_" -match 'SignTool Error|error' } | Select-Object -First 1) -as [string]
  if ($exitCode -eq 0) {
    Write-SigningFact '0' $attempt ''
    break
  }
  Write-SigningFact "$exitCode" $attempt $detail
  if ($attempt -lt $maxAttempts) { Start-Sleep -Seconds (15 * $attempt) }
}
if ($exitCode -ne 0) { throw 'Windows signing command failed' }
# Verify each file before Tauri can place it inside an installer or upload it.
$signature = Get-AuthenticodeSignature -LiteralPath $ArtifactPath
if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or
    $signature.SignerCertificate.Subject -cne $env:WINDOWS_PUBLISHER -or
    -not $signature.TimeStamperCertificate) {
  throw 'Signed file failed Authenticode, publisher or timestamp verification'
}
