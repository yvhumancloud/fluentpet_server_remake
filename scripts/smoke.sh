#!/bin/bash
# Smoke test a deployed API (docs/AWS_SETUP.md §8). Keys come from an env file, default .env:
#     scripts/smoke.sh https://47-130-5-81.sslip.io              # DEVICE_API_KEY / JOB_API_KEY from .env
#     scripts/smoke.sh https://47-130-5-81.sslip.io .env.prod    # or from another file (.env* is gitignored)
set -u
URL=${1:?usage: scripts/smoke.sh https://host [envfile]}; URL=${URL%/}
ENVF=${2:-.env}
key() { sed -n "s/^$1=//p" "$ENVF" | tr -d "\"'" | tail -1; }
DEVICE_API_KEY=$(key DEVICE_API_KEY); JOB_API_KEY=$(key JOB_API_KEY)
[ -n "$DEVICE_API_KEY" ] && [ -n "$JOB_API_KEY" ] || { echo "DEVICE_API_KEY / JOB_API_KEY missing in $ENVF"; exit 2; }

fail=0
check() {  # check <name> <want-status> <want-body-substring> <curl args...>
  local name=$1 want=$2 body=$3 out code; shift 3
  out=$(curl -sS -m 15 -w $'\n%{http_code}' "$@" 2>&1); code=${out##*$'\n'}; out=${out%$'\n'*}
  if [ "$code" = "$want" ] && [[ "$out" == *"$body"* ]]; then echo "ok    $name"
  else echo "FAIL  $name  → [$code] $out"; fail=1; fi
}
check "healthz"             200 '"ok":true'     "$URL/healthz"
check "docs"                200 swagger         "$URL/docs"
check "me: no token"        401 unauthenticated "$URL/api/v1/me"
check "me: dev token dead"  401 unauthenticated -H "Authorization: Bearer ann" "$URL/api/v1/me"
check "device: wrong key"   401 unauthenticated -H "X-Device-Key: nope" "$URL/api/v1/device/desired?serial_number=FPB000000001"
check "device: desired"     200 '['             -H "X-Device-Key: $DEVICE_API_KEY" "$URL/api/v1/device/desired?serial_number=FPB000000001"
check "job: wrong key"      401 unauthenticated -X POST -H "X-Job-Key: nope" "$URL/api/v1/internal/base-offline"
check "job: base-offline"   200 '"pushed":'     -X POST -H "X-Job-Key: $JOB_API_KEY" "$URL/api/v1/internal/base-offline"
check "internal not in openapi" 200 '"/api/v1/me"' "$URL/openapi.json"
curl -sS -m 15 "$URL/openapi.json" | grep -q internal && { echo "FAIL  /internal leaked into openapi"; fail=1; }
exit $fail
