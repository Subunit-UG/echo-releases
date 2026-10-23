#!/usr/bin/env bash
# Public runners must never replay diagnostics from the private source tree.
# Keep stdin, environment, working directory and the command's exit status intact.
set +x
exec 2>/dev/null
set -u
umask 077

fail() { printf '%s\n' '::error::confidential command could not be isolated'; exit 1; }
[ "$#" -gt 0 ] || fail
[ -n "${RUNNER_TEMP:-}" ] && [ -d "$RUNNER_TEMP" ] || fail
capture_dir=$(mktemp -d "$RUNNER_TEMP/echo-confidential.XXXXXXXX") || fail
cleanup() {
  status=$?
  trap - EXIT
  if ! rm -rf -- "$capture_dir"; then
    printf '%s\n' '::error::confidential command cleanup failed'
    [ "$status" -ne 0 ] || status=1
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
chmod 700 "$capture_dir" || fail

if "$@" >"$capture_dir/output" 2>&1; then
  printf '%s\n' 'Confidential command succeeded.'
else
  status=$?
  printf '::error::Confidential command failed (exit %d); diagnostics withheld.\n' "$status"
  exit "$status"
fi
