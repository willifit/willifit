# Rotating NETLIFY_FORMS_TOKEN

**What this token is.** A Netlify Personal Access Token (PAT) held in the site's
environment as `NETLIFY_FORMS_TOKEN`. Its single consumer is
`netlify/functions/reports.js`, which uses it to read Netlify Forms submissions
for the queue on `/admin.html`. Nothing else in this repo uses it.

**Why it needs rotating.** Netlify PATs carry a hard expiry date. The current
one (`willifit AT July 6 2026`) expires **2026-10-05**.

## What breaks when it lapses — and what does not

| | status after expiry |
|---|---|
| `/admin.html` report queue | **broken** — Netlify 401s every API call |
| Form submissions still being collected | fine — collection is server-side, no token involved |
| Site, deploys, DNS, functions | fine — deploys run off the Git integration, not this PAT |
| Already-submitted reports | fine — nothing is lost, they are just unreadable until rotation |

So a lapse costs visibility, not data. Rotate at leisure; do not panic-deploy.

## Rotation

Steps 1–3 must be done by hand in the Netlify UI — a PAT is only ever shown
once, at creation, and cannot be created through the API with an existing token.

1. **Create the new token.** app.netlify.com → avatar → **User settings** →
   **Applications** → **Personal access tokens** → **New access token**.
   Name it `willifit AT <today's date>`. Pick the **longest expiry offered**
   (choose "no expiration" if that option exists) — a 90-day token means doing
   this four times a year. Copy the value; it is not shown again.

2. **Set it on the site.** app.netlify.com → the willifit site → **Site
   configuration** → **Environment variables**:
   - `NETLIFY_FORMS_TOKEN` → paste the new value
   - `NETLIFY_FORMS_TOKEN_EXPIRES` → the new expiry as `YYYY-MM-DD`
     (add it if missing; it drives the warning banner described below)

3. **Redeploy.** Deploys → **Trigger deploy** → *Clear cache and deploy site*.
   Functions read env vars at deploy time, so an unredeployed site keeps using
   the old value.

4. **Verify.** Open `/admin.html`, unlock, and confirm the queue loads. A
   working queue is the proof the new token took; see "If it is still broken"
   below if not.

5. **Revoke the old token.** Back in **Personal access tokens**, delete the
   previous entry. Skipping this leaves a live account-wide credential in
   circulation for no reason.

## How a failure shows up now

- **Before expiry:** within 21 days of the date in
  `NETLIFY_FORMS_TOKEN_EXPIRES`, `/admin.html` shows an amber banner above the
  queue naming the token and the date. The date is recorded by hand — Netlify
  has no API that reports a token's own expiry — so it is only as accurate as
  step 2 above.
- **After expiry:** the queue shows a red **"Netlify token refused"** box with
  the fix spelled out, instead of the bare `Error 502` it used to show. The
  same applies if the token is revoked or loses access to the site.

Both are locked by `tests/test_reports_token_expiry.mjs`.

## If it is still broken after rotating

- **Still "Netlify token refused":** the deploy in step 3 did not pick up the
  new value, or the token was created on an account that cannot see this site.
- **`NETLIFY_FORMS_TOKEN or NETLIFY_SITE_ID missing` (500):** an env var name
  is misspelled, or it was set in the wrong scope/context.
- **Banner says expired but the queue loads:** `NETLIFY_FORMS_TOKEN_EXPIRES` is
  stale — it is advisory text, not the credential. Update it.

## Worth knowing

**A Netlify PAT is account-wide.** There is no read-only or forms-only scope.
The value in `NETLIFY_FORMS_TOKEN` can do anything the account owner can do:
read and write every site's env vars, change DNS, trigger and delete deploys.
It is sitting in a function's environment to power a read-only report viewer.
That is the Forms API's constraint, not a choice made here, but it is the
reason step 5 (revoke the old one) is not optional and the reason a shorter
expiry is a real mitigation rather than just a chore.

**The way to stop rotating entirely** is to stop needing the token: point a
Netlify form-submission notification (an outgoing webhook) at a function that
writes each submission into Netlify Blobs, and have `/admin.html` read Blobs.
No credential, nothing to expire. The cost is that it only captures
submissions made after the switch — the existing queue would have to be
exported first — so it is a deliberate project, not a rotation-day shortcut.
