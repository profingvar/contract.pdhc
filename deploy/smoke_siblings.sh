#!/usr/bin/env bash
# contract.pdhc -> sibling-service smoke (#708). Pre-deploy gate.
#
# Runs smoke_siblings.py INSIDE contract-api-1, which is the only place the
# caller's real configuration resolves: IPS_BASE_URL is
# http://host.docker.internal:9040, meaningful in the container and nowhere
# else. Running the checks from a laptop would exercise a different client
# than the one that serves production.
#
#   ./deploy/smoke_siblings.sh                        # read-only, local
#   ./deploy/smoke_siblings.sh --remote               # read-only, on miserver
#   ./deploy/smoke_siblings.sh --remote \
#       --patient-guid <guid>                         # + positive ips check
#   ./deploy/smoke_siblings.sh --remote --for-real \
#       --provider-org-guid <g> --contract-guid <g>   # also MINTS a PAT
#
# Read-only is the default and changes no state on any service. Everything
# that writes sits behind --for-real, and the script names what it created.
set -euo pipefail
set -o pipefail

CONTAINER=contract-api-1
REMOTE=0
PASS_ARGS=()
for a in "$@"; do
  if [ "$a" = "--remote" ]; then REMOTE=1; else PASS_ARGS+=("$a"); fi
done

OUT="$(mktemp -t contract_smoke)"
trap 'rm -f "$OUT"' EXIT

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SCRIPT="$HERE/smoke_siblings.py"
[ -f "$SCRIPT" ] || { echo "missing $SCRIPT" >&2; exit 2; }

# Non-interactive ssh gets a minimal PATH on the macmini: docker, colima and
# friends live in /opt/homebrew/bin and are otherwise "command not found".
REMOTE_PATH='export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"'

if [ "$REMOTE" = "1" ]; then
  echo "--- running against PRODUCTION via miserver:$CONTAINER"
  # Two stdin traps on one line, both of which fail SILENTLY with exit 0:
  #   * `docker exec` without -i does not forward stdin, so the script body
  #     never reaches python;
  #   * `ssh -n` points stdin at /dev/null, which does the same thing one
  #     level further out. (-n is the right habit when ssh runs inside a
  #     `while read` loop; here it is exactly wrong.)
  # Both produce no output and a zero exit, so the guard below is what turns
  # "nothing ran" into a failure instead of a pass.
  ssh miserver "$REMOTE_PATH; docker context use colima >/dev/null 2>&1; \
    docker exec -i -w /srv $CONTAINER python - $(printf '%q ' "${PASS_ARGS[@]+"${PASS_ARGS[@]}"}")" \
    < "$SCRIPT" | tee "$OUT"
else
  echo "--- running against LOCAL $CONTAINER"
  command -v docker >/dev/null 2>&1 || {
    echo "docker not found; use --remote to run against the macmini" >&2
    exit 2; }
  docker exec -i -w /srv "$CONTAINER" python - \
    ${PASS_ARGS[@]+"${PASS_ARGS[@]}"} < "$SCRIPT" | tee "$OUT"
fi

# A smoke that printed nothing must never look like a smoke that passed.
if ! grep -q "edge(s) checked" "$OUT"; then
  echo
  echo "SMOKE DID NOT RUN — no summary line in the output." >&2
  echo "The checks never executed; this is a FAILURE, not a pass." >&2
  rm -f "$OUT"
  exit 3
fi
rm -f "$OUT"
