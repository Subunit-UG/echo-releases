#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "::error::usage: release_policy.sh EVENT REF TAG MODE" >&2
  exit 2
fi

event="$1"
ref="$2"
tag="$3"
mode="$4"

stable_re='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
rc_re='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc\.[1-9][0-9]*$'

case "$mode" in
  stable)
    if [[ ! "$ref" =~ $stable_re ]]; then
      echo "::error::stable source_ref must be suffixless vX.Y.Z: $ref" >&2
      exit 1
    fi
    if [[ ! "$tag" =~ $stable_re ]]; then
      echo "::error::stable release_tag must be suffixless vX.Y.Z: $tag" >&2
      exit 1
    fi
    if [ "$tag" != "$ref" ]; then
      echo "::error::stable release_tag must exactly equal source_ref" >&2
      exit 1
    fi
    prerelease=false
    ;;
  draft_stable)
    [ "$event" = workflow_dispatch ] || { echo "::error::draft_stable is manual only" >&2; exit 1; }
    [[ "$ref" =~ ^[0-9a-f]{40}$ ]] || { echo "::error::draft_stable requires exact source SHA" >&2; exit 1; }
    [[ "$tag" =~ $stable_re ]] || { echo "::error::draft_stable requires explicit stable tag" >&2; exit 1; }
    prerelease=false
    ;;
  draft_rc)
    if [ "$event" != "workflow_dispatch" ]; then
      echo "::error::draft_rc is allowed only via manual workflow_dispatch" >&2
      exit 1
    fi
    if [[ ! "$ref" =~ $rc_re ]]; then
      echo "::error::draft_rc source_ref must match vX.Y.Z-rc.N: $ref" >&2
      exit 1
    fi
    if [ "$tag" != "$ref" ]; then
      echo "::error::draft_rc release_tag must exactly equal source_ref" >&2
      exit 1
    fi
    prerelease=true
    ;;
  *)
    echo "::error::unknown release_mode: $mode" >&2
    exit 1
    ;;
esac

source_version="${tag#v}"
source_major="${source_version%%.*}"
requires_platform_trust=false
if [ "$source_major" != "0" ]; then
  requires_platform_trust=true
fi

# Every macOS release, including 0.x, requires a Developer ID signature with a
# TeamIdentifier and a stapled notarization ticket.
requires_macos_trust=true

printf 'release_mode=%s\nprerelease=%s\nrequires_platform_trust=%s\nrequires_macos_trust=%s\n' \
  "$mode" "$prerelease" "$requires_platform_trust" "$requires_macos_trust"
