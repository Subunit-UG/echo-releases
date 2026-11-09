#!/usr/bin/env bash
# Deploy-key probe: can the read-only deploy key in ECHO_TAURI_DEPLOY_KEY still read the private source repository?
# A deploy key can disappear from the source repository without any change here; the release build and the PR check
# would then fail only at their next run. This probe reads the ref list only (git ls-remote) and prints PASS/FAIL,
# never refs, transport output or key material.
#   probe-deploy-key.sh <secret-name> <repository>     The key arrives in SOURCE_DEPLOY_KEY (environment).
# Exit: 0 readable · 1 not readable · 64 usage/allowlist · 65 key missing · 68 GitHub host key mismatch.

set -euo pipefail
set +x
umask 077

if [ "$#" -ne 2 ]; then
  echo "usage: probe-deploy-key.sh <secret-name> <repository>" >&2
  exit 64
fi
secret=$1
repository=$2

# Fixed secret → repository pairing (same source as build.yml and pr-check.yml); no input becomes a URL.
case "$secret:$repository" in
  ECHO_TAURI_DEPLOY_KEY:Subunit-UG/echo-app) ;;
  *)
    echo "secret/repository is not allowlisted" >&2
    exit 64
    ;;
esac

if [ -z "${SOURCE_DEPLOY_KEY:-}" ]; then
  echo "::error title=Deploy key missing::$secret is empty or not set."
  exit 65
fi

temp_root=${RUNNER_TEMP:?RUNNER_TEMP is required}
github_ed25519_fingerprint="SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"
credential_dir=''
# shellcheck disable=SC2329 # invoked by trap
cleanup() {
  if [ -n "$credential_dir" ]; then
    rm -f "$credential_dir/key" "$credential_dir/known_hosts"
    rmdir "$credential_dir"
  fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

credential_dir=$(mktemp -d "$temp_root/echo-key-probe.XXXXXX")
key_file="$credential_dir/key"
known_hosts="$credential_dir/known_hosts"
printf '%s\n' "$SOURCE_DEPLOY_KEY" > "$key_file"
chmod 600 "$key_file"
unset SOURCE_DEPLOY_KEY
{ ssh-keyscan -t ed25519 github.com 2>/dev/null || true; } | { grep -v '^#' || true; } > "$known_hosts"
chmod 600 "$known_hosts"
host_key_count=$(wc -l < "$known_hosts" | tr -d '[:space:]')
actual_fingerprint=$(ssh-keygen -lf "$known_hosts" -E sha256 | awk 'NR == 1 { print $2 }')
if [ "$host_key_count" != "1" ] || [ "$actual_fingerprint" != "$github_ed25519_fingerprint" ]; then
  echo "::error title=GitHub host verification failed::The pinned GitHub ED25519 host fingerprint did not match."
  exit 68
fi

printf -v ssh_command '%q -i %q -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=%q -o BatchMode=yes -o ConnectTimeout=20' \
  ssh "$key_file" "$known_hosts"
if GIT_SSH_COMMAND="$ssh_command" GIT_TERMINAL_PROMPT=0 git ls-remote --heads "git@github.com:$repository.git" >/dev/null 2>&1; then
  echo "PASS deploy-key $secret → $repository readable"
  exit 0
fi
echo "::error title=Deploy key invalid::$secret cannot read $repository (key missing in the repository or removed)."
exit 1
