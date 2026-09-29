#!/usr/bin/env bash
# Rotate NETLIFY_FORMS_TOKEN for willifit.ai from the terminal.
#
#   scripts/rotate-netlify-token.sh 2027-06-01
#
# Before running: create the new Personal Access Token in the Netlify UI
# (avatar -> User settings -> Applications -> Personal access tokens) and
# copy it.  The script takes it from the clipboard, or prompts for a paste
# if the clipboard holds something else (copying this command overwrites
# it), so the token never lands in shell history.  The argument is the
# expiry date Netlify shows for it.
#
# What it does, in order (runbook: docs/netlify-token-rotation.md):
#   1. proves the token can read this site's forms -- the exact call
#      netlify/functions/reports.js makes -- BEFORE storing anything
#   2. stores it in NETLIFY_FORMS_TOKEN (plain text, all contexts and
#      scopes, exactly how the variable was originally configured) and
#      reads the variable back to confirm it is there.  It is deliberately
#      NOT marked secret: on this account a secret create reported success
#      and the variable was then simply gone, which shipped a deploy with
#      no token (2026-09-27), and Netlify's forum reports secret values not
#      being injected into Functions at all.
#   3. records the expiry in NETLIFY_FORMS_TOKEN_EXPIRES for the admin banner
#   4. triggers a clear-cache deploy and waits for it (functions read env
#      vars at deploy time)
#   5. optionally calls the reports function with the admin password
#
# Afterwards revoke the previous token in the Netlify UI.  Netlify has no
# API for creating or revoking a PAT; everything else is scripted here.
#
# Needs: node/npx (runs npx netlify-cli, logged in as the site owner),
# jq, curl.  macOS clipboard via pbpaste; prompts for the token otherwise.

set -euo pipefail

SITE=e25ea5f5-a4ed-4ad6-8b57-63669fef6653   # willifit.ai
ACCOUNT=willifit                             # Netlify team slug
FUNCTION_URL=https://willifit.ai/.netlify/functions/reports
EXPIRES="${1:-}"

ncli() { npx --yes netlify-cli "$@"; }

[[ "$EXPIRES" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] \
  || { echo "usage: $0 YYYY-MM-DD   (the expiry date Netlify shows for the new token)" >&2; exit 1; }

TOKEN_RE='^[A-Za-z0-9_-]{30,}$'
TOKEN=""
if command -v pbpaste >/dev/null 2>&1; then
  TOKEN="$(pbpaste | tr -d '[:space:]')"
fi
if [[ ! "$TOKEN" =~ $TOKEN_RE ]]; then
  # Copying this command into the terminal overwrites the clipboard, so the
  # token is usually gone by the time the script runs.  Ask for it instead;
  # the paste is not echoed and never touches shell history.
  printf 'Clipboard does not hold a token. Paste the new PAT and press Enter: '
  read -rs TOKEN; echo
  TOKEN="$(printf '%s' "$TOKEN" | tr -d '[:space:]')"
fi
[[ "$TOKEN" =~ $TOKEN_RE ]] \
  || { echo "that is not a Netlify token (expected 30+ letters, digits, _ or -)" >&2; exit 1; }

# 1. Prove the token before storing it.
CODE="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE/forms")"
[[ "$CODE" == 200 ]] \
  || { echo "Netlify refused the token (HTTP $CODE). Nothing was changed." >&2; exit 1; }
echo "token ${TOKEN:0:4}... (${#TOKEN} chars) accepted by Netlify; expires $EXPIRES"

# 2. Store it, then read the variable's metadata back (never its value).
#    Plain text on purpose -- see the header.  Refuse to deploy if the
#    variable is not there, so the live site keeps whatever it has.
stored() {   # prints how NETLIFY_FORMS_TOKEN is stored, or "absent"
  local out
  out="$(ncli api getEnvVar --data "{\"account_id\":\"$ACCOUNT\",\"site_id\":\"$SITE\",\"key\":\"NETLIFY_FORMS_TOKEN\"}" 2>/dev/null || true)"
  [[ -n "$out" ]] || { echo absent; return; }
  printf '%s' "$out" | jq -r '"secret=\(.is_secret) scopes=\(.scopes|join(",")) contexts=\([.values[].context]|join(","))"'
}
# The CLI echoes KEY=value after a plain set; mask the value on the way out.
ncli env:set NETLIFY_FORMS_TOKEN "$TOKEN" --site "$SITE" --force | sed "s/$TOKEN/<token>/g"
NOW="$(stored)"
if [[ "$NOW" == absent ]]; then
  echo "NETLIFY_FORMS_TOKEN is NOT on the site after env:set. Not deploying." >&2
  echo "Set it by hand: https://app.netlify.com/projects/willifit/configuration/env" >&2
  exit 1
fi
echo "stored: $NOW"

# 3. Expiry date for the admin banner (advisory text, not the credential).
ncli env:set NETLIFY_FORMS_TOKEN_EXPIRES "$EXPIRES" --site "$SITE" --force

# 4. Redeploy and wait.
DEPLOY="$(ncli api createSiteBuild --data "{\"site_id\":\"$SITE\",\"body\":{\"clear_cache\":true}}" | jq -r .deploy_id)"
echo "deploy $DEPLOY building"
while :; do
  STATE="$(ncli api getDeploy --data "{\"deploy_id\":\"$DEPLOY\"}" | jq -r .state)"
  echo "  $STATE"
  case "$STATE" in
    ready) break ;;
    error|rejected) echo "DEPLOY FAILED" >&2; exit 1 ;;
  esac
  sleep 15
done

echo "stored after deploy: $(stored)"

# 5. End to end through the function itself, gated by the admin password.
printf 'admin password for the end-to-end check (Enter to skip): '
read -rs PW || PW=""; echo
if [[ -n "$PW" ]]; then
  RESP="$(curl -s -H "x-willifit-admin: $PW" "$FUNCTION_URL")"
  echo "$RESP" | jq -r 'if .code=="netlify_token_rejected" then "TOKEN REFUSED: "+.error
                       elif .total!=null then "OK: queue loads (\(.total) items)"
                       else "unexpected: "+tostring end' || echo "$RESP"
fi
echo "Now revoke the previous token: https://app.netlify.com/user/applications#personal-access-tokens"
