#!/usr/bin/env bash
set -euo pipefail

# Verify the exact macOS updater archive and DMG after download from the release
# draft. This script consumes no signing credentials and never modifies either
# artifact.
#
# Apple requirements and command-line notarization workflow:
# https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution
# https://developer.apple.com/documentation/security/customizing-the-notarization-workflow

usage() {
  echo "usage: $0 APP_TAR_GZ DMG EXPECTED_BUNDLE_ID EXPECTED_VERSION EXPECTED_TEAM_ID EXPECTED_AUTHORITY" >&2
  exit 2
}

[ "$#" -eq 6 ] || usage

app_archive="$1"
dmg="$2"
expected_bundle_id="$3"
expected_version="$4"
expected_team_id="$5"
expected_authority="$6"

[ -f "$app_archive" ] || { echo "error: app archive not found: $app_archive" >&2; exit 1; }
[ -f "$dmg" ] || { echo "error: DMG not found: $dmg" >&2; exit 1; }
[ -n "$expected_bundle_id" ] || { echo "error: expected bundle identifier is empty" >&2; exit 1; }
[ -n "$expected_version" ] || { echo "error: expected app version is empty" >&2; exit 1; }
[ -n "$expected_team_id" ] || { echo "error: expected TeamIdentifier is empty" >&2; exit 1; }
[ -n "$expected_authority" ] || { echo "error: expected signing authority is empty" >&2; exit 1; }
[ "$(uname -s)" = Darwin ] || { echo "error: macOS verifier requires a Darwin runner" >&2; exit 1; }

for command in codesign hdiutil mktemp plutil python3 spctl xcrun; do
  command -v "$command" >/dev/null 2>&1 \
    || { echo "error: required command not found: $command" >&2; exit 1; }
done

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/echo-macos-trust.XXXXXX")"
mount_dir="$work_dir/dmg"
archive_dir="$work_dir/archive"
mounted=false
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cleanup() {
  if [ "$mounted" = true ]; then
    hdiutil detach "$mount_dir" -quiet >/dev/null 2>&1 || hdiutil detach "$mount_dir" -force -quiet >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

mkdir -p "$mount_dir"
echo "evidence directory: $work_dir"
python3 "$script_dir/validate-macos-app-archive.py" "$app_archive" "$archive_dir"

find_single_app() {
  local root="$1"
  local label="$2"
  local apps=()
  while IFS= read -r -d '' path; do
    apps+=("$path")
  done < <(find "$root" -type d -name '*.app' -prune -print0)
  if [ "${#apps[@]}" -ne 1 ]; then
    echo "error: expected exactly one .app in $label, found ${#apps[@]}" >&2
    exit 1
  fi
  printf '%s\n' "${apps[0]}"
}

verify_signer_metadata() {
  local artifact="$1"
  local label="$2"
  local details

  details="$(codesign --display --verbose=4 "$artifact" 2>&1)"
  printf '%s\n' "$details" | grep -Fqx "TeamIdentifier=$expected_team_id" \
    || { echo "error: $label has unexpected TeamIdentifier" >&2; exit 1; }
  printf '%s\n' "$details" | grep -Fqx "Authority=$expected_authority" \
    || { echo "error: $label has unexpected signing authority" >&2; exit 1; }
  printf '%s\n' "$details" | grep -Fq 'Timestamp=' \
    || { echo "error: $label has no secure signing timestamp" >&2; exit 1; }
  if printf '%s\n' "$details" | grep -Fq 'Signature=adhoc'; then
    echo "error: $label has an ad-hoc signature" >&2
    exit 1
  fi
}

# Gatekeeper must attribute the artifact to a notarized Developer ID, and the
# leaf certificate must chain to Apple and belong to the expected team.
verify_gatekeeper_source() {
  local label="$1"
  shift
  local assessment
  assessment="$(spctl --assess "$@" --verbose=4 2>&1)" \
    || { echo "error: $label was rejected by Gatekeeper" >&2; exit 1; }
  printf '%s\n' "$assessment" | grep -Fqx 'source=Notarized Developer ID' \
    || { echo "error: $label is not assessed as Notarized Developer ID" >&2; exit 1; }
}

verify_apple_anchor() {
  local artifact="$1"
  local label="$2"
  codesign --verify --strict \
    -R="anchor apple generic and certificate leaf[subject.OU] = \"$expected_team_id\"" "$artifact" \
    || { echo "error: $label does not satisfy the Apple Developer ID requirement" >&2; exit 1; }
}

verify_app() {
  local app="$1"
  local label="$2"
  local details

  codesign --verify --deep --strict --verbose=2 "$app"
  verify_signer_metadata "$app" "$label"
  details="$(codesign --display --verbose=4 "$app" 2>&1)"
  printf '%s\n' "$details" | grep -Fqx "Identifier=$expected_bundle_id" \
    || { echo "error: $label has unexpected bundle identifier" >&2; exit 1; }
  local info_plist="$app/Contents/Info.plist"
  local short_version bundle_version
  [ -f "$info_plist" ] || { echo "error: $label has no Info.plist" >&2; exit 1; }
  short_version="$(plutil -extract CFBundleShortVersionString raw -o - "$info_plist")"
  bundle_version="$(plutil -extract CFBundleVersion raw -o - "$info_plist")"
  [ "$short_version" = "$expected_version" ] \
    || { echo "error: $label has CFBundleShortVersionString=$short_version, expected $expected_version" >&2; exit 1; }
  [ "$bundle_version" = "$expected_version" ] \
    || { echo "error: $label has CFBundleVersion=$bundle_version, expected $expected_version" >&2; exit 1; }
  xcrun stapler validate "$app"
  verify_apple_anchor "$app" "$label"
  verify_gatekeeper_source "$label" --type execute "$app"
}

archive_app="$(find_single_app "$archive_dir" "updater archive")"
verify_app "$archive_app" "updater archive app"

codesign --verify --strict --verbose=2 "$dmg"
verify_signer_metadata "$dmg" "DMG"
xcrun stapler validate "$dmg"
verify_apple_anchor "$dmg" "DMG"
verify_gatekeeper_source "DMG" --type open --context context:primary-signature "$dmg"

hdiutil attach "$dmg" -readonly -nobrowse -mountpoint "$mount_dir" -quiet
mounted=true
dmg_app="$(find_single_app "$mount_dir" "mounted DMG")"
verify_app "$dmg_app" "DMG app"
hdiutil detach "$mount_dir" -quiet
mounted=false

printf 'macOS platform trust verified: bundle=%s version=%s team=%s\n' \
  "$expected_bundle_id" "$expected_version" "$expected_team_id"
printf 'evidence retained: %s\n' "$work_dir"
