/* WillIFit — cookie consent engine.
 *
 * Loaded BLOCKING from <head> on every page, immediately before the
 * Cloudflare/Google tag block:
 *
 *     <script src="/js/consent.js"></script>
 *
 * "Blocking" (no async, no defer) is deliberate and load-bearing.  The
 * Google tag is <script async>, so the browser may fetch it in parallel but
 * cannot EXECUTE it until this synchronous script has finished.  That gives
 * us a guaranteed window in which to push the Consent Mode defaults into
 * window.dataLayer before gtag.js ever reads the queue.  Move this to defer
 * and the whole policy silently stops applying — GA would initialise under
 * its own "everything granted" assumption and set _ga before we ever spoke.
 *
 * Two independent mechanisms are at work here, and confusing them is the
 * usual way these things get built wrong:
 *
 *   1. WHAT GOOGLE IS ALLOWED TO DO  -> Google Consent Mode v2, region-scoped
 *      by Google itself.  Google resolves the visitor's region SERVER-SIDE
 *      from the request IP.  That is authoritative; no client-side guess can
 *      beat it, and we do not try.  See setDefaults() below.
 *
 *   2. WHETHER WE SHOW A BANNER      -> a purely cosmetic client-side
 *      timezone heuristic.  It decides UI only.  If it is wrong in the
 *      "didn't show a banner" direction, an EEA visitor still gets full
 *      denial from mechanism (1) — they just have no way to opt IN until
 *      they use the footer "Cookie preferences" control.  Nothing leaks.
 *
 * Public API (frozen — other pages and agents wire against these names):
 *   window.wfConsent.open()        opens the preferences dialog, any region
 *   window.wfConsent.get()         -> 'granted' | 'denied' | null (undecided)
 *   window.wfConsent.set(state)    persists + fires a gtag consent update
 *   window.wfConsent.isEEA()       -> bool, the banner region heuristic
 *
 * Markup hook (any page, anywhere):
 *   <button type="button" data-wf-consent-open class="wf-consent-btn">
 *     Cookie preferences
 *   </button>
 *
 * Storage:
 *   localStorage["willifit_cookie_consent"]
 *     = {"state":"granted"|"denied","ts":<epoch ms>,"v":1}
 *
 * ES5 only: var, function expressions, string concatenation.  No arrow
 * functions, no const/let, no template literals, no optional chaining, no
 * Array#includes, no String#startsWith.  This file is the one script on the
 * site that must never throw a SyntaxError, because a parse failure here
 * means no consent defaults at all — the exact opposite of what it is for.
 */
(function () {
  'use strict';

  /* ==========================================================
     0 · Constants
     ========================================================== */

  var STORAGE_KEY = 'willifit_cookie_consent';
  var STORAGE_VERSION = 1;
  var GA_MEASUREMENT_ID = 'G-SH191K5NGS';

  /* The EEA + UK + Switzerland, as ISO 3166-1 alpha-2 codes.  Google's
     `region` parameter takes ISO 3166-2, of which bare country codes are the
     valid two-letter form ('ES'), with subregions written 'US-AK'.  We only
     ever need country granularity.

     27 EU member states, then the three non-EU EEA states (IS, LI, NO), then
     the UK (post-Brexit UK GDPR is materially the same obligation) and
     Switzerland (revFADP — not GDPR, but the consent expectations line up
     closely enough that treating it identically is the cheap, safe call). */
  var EEA_UK_CH = [
    /* EU-27 */
    'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR',
    'DE', 'GR', 'HU', 'IE', 'IT', 'LV', 'LT', 'LU', 'MT', 'NL',
    'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE',
    /* EEA, non-EU */
    'IS', 'LI', 'NO',
    /* UK GDPR + Swiss revFADP */
    'GB', 'CH'
  ];

  /* Timezones that are in scope for the BANNER but do not start with
     "Europe/".  Two groups:

     (a) Cyprus.  This one is a genuine trap.  Cyprus is an EU member state,
         but its IANA identifier is Asia/Nicosia (Europe/Nicosia exists only
         as a backward-compatibility link, and Chrome reports the Asia/ form).
         A naive "Europe/*" check misses an entire member state.
         Asia/Famagusta covers the northern part of the island.

     (b) EEA Atlantic/Arctic islands and the French overseas departments.
         The DOMs (Réunion, Mayotte, Martinique, Guadeloupe, French Guiana,
         Saint-Martin) are outermost regions of the EU — GDPR applies there
         exactly as it does in metropolitan France.  Saint-Barthélemy is an
         OCT rather than an OMR, but it is one line and over-inclusion is
         free, so it stays.

     Over-inclusion is always the correct error here.  A Serbian or Turkish
     visitor sees a banner they did not strictly need; that costs nothing.
     A German visitor who never sees one is the actual liability. */
  var EXTRA_EEA_ZONES = [
    'Asia/Nicosia', 'Asia/Famagusta',
    'Atlantic/Canary', 'Atlantic/Madeira', 'Atlantic/Azores',
    'Atlantic/Reykjavik', 'Atlantic/Faroe', 'Atlantic/Jan_Mayen',
    'Arctic/Longyearbyen',
    'Indian/Reunion', 'Indian/Mayotte',
    'America/Martinique', 'America/Guadeloupe', 'America/Cayenne',
    'America/Marigot', 'America/St_Barthelemy'
  ];

  /* Cookie name prefixes we actively delete when analytics is refused.
     Consent Mode stops Google WRITING new cookies; it does not remove ones
     already on the device from a previous "accept".  Without this sweep,
     a user who accepts and later rejects keeps their _ga client id forever,
     which makes the reject a lie. */
  var ANALYTICS_COOKIE_PREFIXES = ['_ga', '_gid', '_gat', '_gcl'];

  var STYLE_ID = 'wf-consent-style';
  var BANNER_ID = 'wf-consent-banner';
  var DIALOG_ID = 'wf-consent-dialog';

  /* ==========================================================
     1 · Google Consent Mode v2 defaults
     ==========================================================
     Runs synchronously, right now, before anything else in this file. */

  window.dataLayer = window.dataLayer || [];

  /* Deliberately a LOCAL gtag, not a global one.  Every page also declares
     its own global `function gtag(){dataLayer.push(arguments)}` a few lines
     further down the <head>; that declaration would overwrite anything we
     put on window, and the contract says leave that snippet untouched.  Both
     functions push identical `arguments` objects onto the same
     window.dataLayer array, so the queue is the same either way and there is
     nothing to coordinate. */
  function gtag() {
    window.dataLayer.push(arguments);
  }

  function setDefaults() {
    /* ORDER MATTERS AND IS NOT INTERCHANGEABLE.
       Per Google's Consent Mode guide, the region-scoped default is written
       FIRST and the general default SECOND — that is the exact shape of
       Google's own published example:

           gtag('consent', 'default', { 'analytics_storage': 'denied',
                                        'region': ['ES', 'US-AK'] });
           gtag('consent', 'default', { 'ad_storage': 'denied' });

       Resolution is by specificity, not by source order: "If two default
       consent commands occur on the same page with values for a region and
       subregion, the one with a more specific region will take effect", and
       a default with no `region` "sets the default for all visitors not
       covered by another region-specific command."  We follow the published
       order anyway, because Google is explicit that "if your consent code is
       called out of order, consent defaults won't work" and this is not a
       thing worth being clever about — getting it backwards would silently
       invert the entire policy and nothing visible would break. */

    /* (a) EEA / UK / CH — everything denied until the visitor says otherwise.
           wait_for_update holds measurement for 500ms so that a stored
           decision (or a fast banner click) can land before the tag gives up
           and falls through to the denied default. */
    gtag('consent', 'default', {
      'ad_storage': 'denied',
      'ad_user_data': 'denied',
      'ad_personalization': 'denied',
      'analytics_storage': 'denied',
      'wait_for_update': 500,
      'region': EEA_UK_CH
    });

    /* (b) Everywhere else (overwhelmingly US traffic) — analytics on,
           advertising off.  WillIFit runs no ad tech of any kind, and the
           three ad signals are pinned to 'denied' for every visitor on
           earth, in every code path in this file, including after an
           explicit "Accept".  If the site ever does take ad money, granting
           these has to be a deliberate, reviewed change rather than
           something that switched itself on quietly. */
    gtag('consent', 'default', {
      'ad_storage': 'denied',
      'ad_user_data': 'denied',
      'ad_personalization': 'denied',
      'analytics_storage': 'granted'
    });
  }

  /* Push a consent UPDATE reflecting an explicit decision.
     Called synchronously at head time when a stored decision exists, and
     again on every click in the banner or dialog. */
  function pushUpdate(state) {
    gtag('consent', 'update', {
      /* Never granted. See note (b) above. */
      'ad_storage': 'denied',
      'ad_user_data': 'denied',
      'ad_personalization': 'denied',
      'analytics_storage': state === 'granted' ? 'granted' : 'denied'
    });
  }

  /* ==========================================================
     2 · Stored decision
     ========================================================== */

  /* -> 'granted' | 'denied' | null.  Never throws: localStorage can be
     disabled outright (Safari private mode historically, enterprise policy,
     file:// origins) and the stored blob can be corrupt if a user or an
     extension has been poking at it.  Both cases mean "undecided", which is
     the safe reading. */
  function readStored() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return null;
      if (parsed.state === 'granted' || parsed.state === 'denied') {
        return parsed.state;
      }
      return null;
    } catch (e) {
      return null;
    }
  }

  function writeStored(state) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
        state: state,
        ts: Date.now(),
        v: STORAGE_VERSION
      }));
    } catch (e) {
      /* Storage unavailable or full.  The consent update below still applies
         for this pageview; the visitor will simply be asked again next time.
         Asking twice is annoying; assuming consent we could not record would
         be worse. */
    }
  }

  /* ==========================================================
     3 · Global Privacy Control
     ==========================================================
     GPC is a browser-level "do not sell/share my personal information"
     signal.  Honoring it is legally REQUIRED in California (CCPA/CPRA),
     Colorado and Connecticut, and it costs one boolean check, so we honor it
     everywhere on earth rather than trying to work out who is in scope.

     A GPC visitor has already answered the question, so they are never shown
     a banner.  If they later open the preferences dialog themselves and
     explicitly tick "allow analytics", that later explicit act wins — it is
     stored, and stored decisions are read before GPC is consulted. */
  function hasGPC() {
    try {
      return window.navigator.globalPrivacyControl === true;
    } catch (e) {
      return false;
    }
  }

  /* ==========================================================
     4 · Region heuristic (banner UI only — see file header)
     ==========================================================
     No network call, no extra request, no IP lookup, no third-party geo
     service.  Intl.DateTimeFormat().resolvedOptions().timeZone is already
     resident in the browser and costs nothing. */
  function isEEA() {
    try {
      var tz = null;
      if (window.Intl && window.Intl.DateTimeFormat) {
        var opts = new window.Intl.DateTimeFormat().resolvedOptions();
        tz = opts && opts.timeZone;
      }
      /* No Intl, or an Intl that does not report timeZone (IE11 does exactly
         this).  We cannot tell where they are, so we over-include and show
         the banner.  That population is vanishingly small, and the failure
         mode is "a US visitor on a museum-piece browser sees one extra bar",
         not "an EEA visitor is tracked without being asked". */
      if (!tz) return true;

      if (tz.indexOf('Europe/') === 0) return true;
      if (EXTRA_EEA_ZONES.indexOf(tz) !== -1) return true;
      return false;
    } catch (e) {
      /* Same reasoning as above: fail toward showing the banner. */
      return true;
    }
  }

  /* ==========================================================
     5 · Cookie cleanup on refusal
     ==========================================================
     GA4 writes _ga on the registrable domain (".willifit.ai") with a leading
     dot, but a cookie can only be deleted by matching its domain and path,
     and JS cannot read a cookie's domain attribute.  So we brute-force it:
     expire every analytics-looking cookie against the bare host, the dotted
     host, and every parent domain up the chain.  Deleting a cookie that does
     not exist is a no-op, so the shotgun is harmless. */
  function clearAnalyticsCookies() {
    try {
      var jar = document.cookie ? document.cookie.split(';') : [];
      if (!jar.length) return;

      var host = window.location.hostname;
      var domains = [null, host, '.' + host];
      var parts = host.split('.');
      var i;
      for (i = 1; i < parts.length - 1; i++) {
        var parent = parts.slice(i).join('.');
        domains.push(parent);
        domains.push('.' + parent);
      }

      for (i = 0; i < jar.length; i++) {
        var name = jar[i].split('=')[0].replace(/^\s+|\s+$/g, '');
        if (!name) continue;

        var matches = false;
        for (var p = 0; p < ANALYTICS_COOKIE_PREFIXES.length; p++) {
          if (name.indexOf(ANALYTICS_COOKIE_PREFIXES[p]) === 0) {
            matches = true;
            break;
          }
        }
        if (!matches) continue;

        for (var d = 0; d < domains.length; d++) {
          var base = name + '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
          document.cookie = domains[d] ? base + '; domain=' + domains[d] : base;
        }
      }
    } catch (e) {
      /* document.cookie can throw in sandboxed frames. Nothing to do. */
    }
  }

  /* ==========================================================
     6 · State transitions
     ========================================================== */

  function setState(state) {
    /* Anything that is not an explicit 'granted' is treated as 'denied'.
       Defaulting an unrecognised value to the permissive side would be the
       classic way this kind of code springs a leak. */
    var next = state === 'granted' ? 'granted' : 'denied';
    writeStored(next);
    pushUpdate(next);
    if (next === 'denied') clearAnalyticsCookies();
    hideBanner();
    syncDialogToState();
    return next;
  }

  /* What is actually in force right now, as opposed to what was explicitly
     chosen.  Used to seed the dialog's checkbox so it tells the truth to a
     US visitor who has never been asked anything (analytics IS on for them,
     via the general default) as well as to an EEA visitor (it is not). */
  function effectiveGranted() {
    var stored = readStored();
    if (stored) return stored === 'granted';
    if (hasGPC()) return false;
    if (isEEA()) return false;
    return true;
  }

  /* ==========================================================
     7 · Injected CSS
     ==========================================================
     Injected from JS as a <style> element rather than shipped in each page's
     stylesheet, so this file stays the single source of truth and no HTML
     page has to be edited to restyle the banner.  The site's CSP already
     allows 'unsafe-inline' for style-src, and a <style> element does not
     depend on it in any case.

     COLORS: the entire site is dark (--bg #0e1116 on all 240+ pages), so
     these are the house palette hard-coded rather than pulled from CSS
     custom properties.  Hard-coding matters here — this bar can render on
     any page, including a hypothetical future one that has not defined the
     tokens, and a consent notice that renders invisibly is worse than no
     consent notice.  Every pairing below was measured:

       #e6ebf2 on #171b23  14.4:1   body text
       #b6c0d0 on #171b23   9.4:1   secondary text
       #3ecf8e on #171b23   8.6:1   links + focus ring
       #0e1116 on #e6ebf2  15.8:1   button labels
       #b6c0d0 on #1f242e   8.5:1   cookie table
       #6b7690 on #171b23   3.8:1   borders (non-text, needs 3:1)

     .wf-consent-btn is the exception: it uses `color: inherit` / `font:
     inherit` on purpose so the footer control adopts whatever the
     surrounding footer already uses, which is by definition already
     audited. */
  var CSS = [
    /* ---- the footer "Cookie preferences" control -------------------- */
    /* A <button> is the correct element (it performs an action, it does not
       navigate), but it has to READ as one of the footer links beside it.
       Everything that makes a button look like a button is unset, and font
       and color are inherited so it works on the dark index.html footer and
       on the static-page footers without knowing which is which. */
    '.wf-consent-btn{',
    '  background:transparent;border:0;margin:0;padding:0;',
    '  font:inherit;color:inherit;line-height:inherit;letter-spacing:inherit;',
    '  text-decoration:none;cursor:pointer;',
    '  -webkit-appearance:none;-moz-appearance:none;appearance:none;',
    '}',
    '.wf-consent-btn:hover{text-decoration:underline;color:var(--text,#e6ebf2);}',
    '.wf-consent-btn:focus-visible{outline:2px solid var(--ok,#3ecf8e);outline-offset:2px;border-radius:2px;}',

    /* ---- shared tokens, scoped so nothing leaks into the page ------- */
    '#' + BANNER_ID + ',#' + DIALOG_ID + '{',
    '  --wf-c-panel:#171b23;--wf-c-panel2:#1f242e;--wf-c-text:#e6ebf2;',
    '  --wf-c-muted:#b6c0d0;--wf-c-line:#6b7690;--wf-c-accent:#3ecf8e;',
    '  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Inter Tight",sans-serif;',
    '  box-sizing:border-box;',
    '}',
    '#' + BANNER_ID + ' *,#' + DIALOG_ID + ' *{box-sizing:border-box;}',
    /* One focus-ring rule for every interactive descendant, so the inline
       policy links get the same 2px accent ring as the buttons instead of
       falling back to the user-agent default (which varies by browser and is
       not guaranteed to contrast against this dark panel).  Matches the
       `outline: 2px solid var(--ok); outline-offset: 2px` house style already
       used across index.html. */
    '#' + BANNER_ID + ' a:focus-visible,#' + DIALOG_ID + ' a:focus-visible{',
    '  outline:2px solid var(--wf-c-accent);outline-offset:2px;border-radius:2px;',
    '}',

    /* ---- the banner: a bottom bar, never a full-screen interstitial -- */
    /* It does not cover the page, does not block scrolling, and does not
       steal focus.  The visitor can read the site and decide in their own
       time; a wall that must be dismissed before the content appears is the
       pattern regulators keep taking issue with. */
    '#' + BANNER_ID + '{',
    '  position:fixed;left:0;right:0;bottom:0;z-index:2147483000;',
    '  background:var(--wf-c-panel);color:var(--wf-c-text);',
    '  border-top:1px solid var(--wf-c-line);',
    '  box-shadow:0 -8px 30px rgba(0,0,0,0.45);',
    '  padding:14px 20px;',
    '  padding-bottom:calc(14px + env(safe-area-inset-bottom,0px));',
    '  padding-left:calc(20px + env(safe-area-inset-left,0px));',
    '  padding-right:calc(20px + env(safe-area-inset-right,0px));',
    '  font-size:13px;line-height:1.5;',
    '}',
    '@media (prefers-reduced-motion:no-preference){',
    '  #' + BANNER_ID + '{animation:wf-c-rise .22s cubic-bezier(.2,.7,.3,1);}',
    '}',
    '@keyframes wf-c-rise{from{transform:translateY(100%);}to{transform:none;}}',
    '#' + BANNER_ID + ' .wf-c-inner{',
    '  max-width:1100px;margin:0 auto;',
    '  display:flex;gap:16px;align-items:center;flex-wrap:wrap;',
    '  justify-content:space-between;',
    '}',
    '#' + BANNER_ID + ' .wf-c-copy{flex:1 1 420px;min-width:260px;margin:0;}',
    '#' + BANNER_ID + ' .wf-c-copy a{color:var(--wf-c-accent);text-decoration:underline;}',
    '#' + BANNER_ID + ' .wf-c-actions{',
    '  display:flex;gap:10px;align-items:center;flex-wrap:wrap;flex:0 0 auto;',
    '}',

    /* ---- Accept / Reject ------------------------------------------- */
    /* These two rules are the whole point.  Accept and Reject share ONE
       class and ONE declaration block: identical padding, font-size,
       font-weight, border, radius, min-width and colour.  They differ only
       in their text.  A greyed-out or link-styled "Reject" beside a solid
       "Accept" is precisely the dark pattern EU DPAs have been issuing fines
       over, and it is trivially avoided by not having a second style at all.
       If you are about to add `.wf-c-btn--secondary`, don't. */
    '#' + BANNER_ID + ' .wf-c-btn,#' + DIALOG_ID + ' .wf-c-btn{',
    '  font:inherit;font-size:13px;font-weight:700;letter-spacing:.01em;',
    '  padding:10px 22px;min-width:104px;min-height:40px;',
    '  background:#e6ebf2;color:#0e1116;',
    '  border:1px solid #e6ebf2;border-radius:4px;',
    '  cursor:pointer;text-align:center;',
    '  -webkit-appearance:none;-moz-appearance:none;appearance:none;',
    '}',
    '#' + BANNER_ID + ' .wf-c-btn:hover,#' + DIALOG_ID + ' .wf-c-btn:hover{background:#fff;border-color:#fff;}',
    '#' + BANNER_ID + ' .wf-c-btn:focus-visible,#' + DIALOG_ID + ' .wf-c-btn:focus-visible{',
    '  outline:2px solid var(--wf-c-accent);outline-offset:2px;',
    '}',
    /* "Preferences" is a third, different action (it opens a dialog rather
       than recording a choice), so styling it as a link is not a prominence
       game between Accept and Reject — those two remain identical. */
    '#' + BANNER_ID + ' .wf-c-link{',
    '  font:inherit;font-size:13px;background:transparent;border:0;',
    '  color:var(--wf-c-accent);text-decoration:underline;cursor:pointer;',
    '  padding:10px 4px;min-height:40px;',
    '}',
    '#' + BANNER_ID + ' .wf-c-link:focus-visible{outline:2px solid var(--wf-c-accent);outline-offset:2px;}',

    /* ---- the preferences dialog ------------------------------------ */
    '#' + DIALOG_ID + '{',
    '  position:fixed;inset:0;top:0;right:0;bottom:0;left:0;',
    '  z-index:2147483001;',
    '  background:rgba(0,0,0,.75);',
    '  display:flex;align-items:center;justify-content:center;',
    '  padding:24px 16px;',
    '}',
    '#' + DIALOG_ID + '[hidden]{display:none;}',
    '@media (prefers-reduced-motion:no-preference){',
    '  #' + DIALOG_ID + '{animation:wf-c-fade .18s ease-out;}',
    '}',
    '@keyframes wf-c-fade{from{opacity:0;}to{opacity:1;}}',
    '#' + DIALOG_ID + ' .wf-c-modal{',
    '  background:var(--wf-c-panel);color:var(--wf-c-text);',
    '  border:1px solid var(--wf-c-line);border-radius:8px;',
    '  box-shadow:0 30px 60px rgba(0,0,0,.6);',
    '  width:100%;max-width:640px;max-height:88vh;',
    '  display:flex;flex-direction:column;overflow:hidden;',
    '  font-size:13px;line-height:1.6;',
    '}',
    '#' + DIALOG_ID + ' .wf-c-head{',
    '  display:flex;align-items:center;justify-content:space-between;gap:12px;',
    '  padding:16px 20px;border-bottom:1px solid var(--wf-c-line);flex:0 0 auto;',
    '}',
    '#' + DIALOG_ID + ' .wf-c-title{font-size:17px;font-weight:700;letter-spacing:-.01em;margin:0;}',
    '#' + DIALOG_ID + ' .wf-c-x{',
    '  background:transparent;border:1px solid var(--wf-c-line);color:var(--wf-c-text);',
    '  width:40px;height:40px;border-radius:4px;cursor:pointer;font-size:18px;line-height:1;',
    '  flex:0 0 auto;',
    '}',
    '#' + DIALOG_ID + ' .wf-c-x:hover{background:#e5484d;border-color:#e5484d;color:#fff;}',
    '#' + DIALOG_ID + ' .wf-c-x:focus-visible{outline:2px solid var(--wf-c-accent);outline-offset:2px;}',
    '#' + DIALOG_ID + ' .wf-c-body{padding:18px 20px;overflow:auto;flex:1 1 auto;}',
    '#' + DIALOG_ID + ' .wf-c-body p{margin:0 0 12px;}',
    '#' + DIALOG_ID + ' .wf-c-body a{color:var(--wf-c-accent);}',
    '#' + DIALOG_ID + ' h3{',
    '  font-size:13px;font-weight:700;margin:20px 0 6px;',
    '  text-transform:uppercase;letter-spacing:.09em;color:var(--wf-c-text);',
    '}',
    '#' + DIALOG_ID + ' .wf-c-muted{color:var(--wf-c-muted);}',
    '#' + DIALOG_ID + ' .wf-c-status{',
    '  margin:0 0 4px;padding:10px 12px;border-radius:4px;',
    '  background:var(--wf-c-panel2);border:1px solid var(--wf-c-line);',
    '  color:var(--wf-c-text);font-weight:700;',
    '}',
    '#' + DIALOG_ID + ' .wf-c-choice{',
    '  display:flex;gap:10px;align-items:flex-start;',
    '  padding:12px;margin:10px 0 4px;border-radius:4px;',
    '  background:var(--wf-c-panel2);border:1px solid var(--wf-c-line);',
    '}',
    '#' + DIALOG_ID + ' .wf-c-choice input{',
    '  width:20px;height:20px;margin:2px 0 0;flex:0 0 auto;accent-color:#3ecf8e;cursor:pointer;',
    '}',
    '#' + DIALOG_ID + ' .wf-c-choice input:focus-visible{outline:2px solid var(--wf-c-accent);outline-offset:2px;}',
    '#' + DIALOG_ID + ' .wf-c-choice label{cursor:pointer;font-weight:700;}',
    '#' + DIALOG_ID + ' table{',
    '  width:100%;border-collapse:collapse;margin:8px 0 4px;font-size:12px;',
    '}',
    '#' + DIALOG_ID + ' caption{',
    '  text-align:left;color:var(--wf-c-muted);font-size:12px;padding-bottom:6px;',
    '}',
    '#' + DIALOG_ID + ' th,#' + DIALOG_ID + ' td{',
    '  text-align:left;padding:7px 9px;border:1px solid var(--wf-c-line);vertical-align:top;',
    '}',
    '#' + DIALOG_ID + ' th{background:var(--wf-c-panel2);font-weight:700;}',
    '#' + DIALOG_ID + ' td{color:var(--wf-c-muted);}',
    '#' + DIALOG_ID + ' code{',
    '  font-family:Menlo,Consolas,monospace;font-size:12px;color:var(--wf-c-text);',
    '  background:var(--wf-c-panel2);padding:1px 5px;border-radius:3px;',
    '}',
    '#' + DIALOG_ID + ' ul{margin:6px 0 12px;padding-left:20px;color:var(--wf-c-muted);}',
    '#' + DIALOG_ID + ' li{margin-bottom:4px;}',
    '#' + DIALOG_ID + ' .wf-c-foot{',
    '  display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end;',
    '  padding:14px 20px;border-top:1px solid var(--wf-c-line);flex:0 0 auto;',
    '}',
    '@media (max-width:560px){',
    '  #' + BANNER_ID + ' .wf-c-actions{width:100%;}',
    '  #' + BANNER_ID + ' .wf-c-btn{flex:1 1 0;}',
    '  #' + DIALOG_ID + ' .wf-c-foot .wf-c-btn{flex:1 1 0;}',
    '}',
    /* Windows High Contrast / forced-colors: give up our palette entirely
       and let the OS draw, otherwise the bar can vanish. */
    '@media (forced-colors:active){',
    '  #' + BANNER_ID + ',#' + DIALOG_ID + ' .wf-c-modal{border:1px solid CanvasText;}',
    '  #' + BANNER_ID + ' .wf-c-btn,#' + DIALOG_ID + ' .wf-c-btn{border:1px solid ButtonText;}',
    '}'
  ].join('\n');

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var head = document.head || document.getElementsByTagName('head')[0];
    if (!head) return;
    var s = document.createElement('style');
    s.id = STYLE_ID;
    s.appendChild(document.createTextNode(CSS));
    head.appendChild(s);
  }

  /* ==========================================================
     8 · Banner
     ==========================================================
     Shown only to EEA/UK/CH visitors with no stored decision and no GPC
     signal.  Everyone else gets the footer control instead. */

  var bannerEl = null;

  function showBanner() {
    if (bannerEl || !document.body) return;

    bannerEl = document.createElement('div');
    bannerEl.id = BANNER_ID;
    /* role="region" + aria-label, NOT role="dialog".  This is a non-modal
       bar: it does not trap focus and it does not take focus on load
       (yanking focus on page load is disorienting and is its own audit
       finding).  A labelled landmark means screen-reader users can jump
       straight to it with landmark navigation, and because the element is
       appended last in the body its DOM order matches its visual position at
       the bottom of the page — so tab order stays sane. */
    bannerEl.setAttribute('role', 'region');
    bannerEl.setAttribute('aria-label', 'Cookie consent');

    bannerEl.innerHTML =
      '<div class="wf-c-inner">' +
        '<p class="wf-c-copy">' +
          '<strong>Cookies on WillIFit.</strong> ' +
          'We would like to use Google Analytics cookies to count visits and see ' +
          'which pages actually help people. That is all they are for &mdash; no ' +
          'advertising cookies, no cross-site tracking, and we never sell your data. ' +
          'Say no and the site works exactly the same. ' +
          '<a href="/cookies.html">Cookie Policy</a> &middot; ' +
          '<a href="/privacy.html">Privacy Policy</a>' +
        '</p>' +
        '<div class="wf-c-actions">' +
          /* Accept and Reject: same class, same box, same weight. */
          '<button type="button" class="wf-c-btn" data-wf-c="accept">Accept</button>' +
          '<button type="button" class="wf-c-btn" data-wf-c="reject">Reject</button>' +
          '<button type="button" class="wf-c-link" data-wf-c="prefs">Preferences</button>' +
        '</div>' +
      '</div>';

    document.body.appendChild(bannerEl);

    /* W2 fix: this banner is position:fixed;bottom:0, and the SPA's own
       footer strip (index.html .site-footer, bottom of the .app grid) sits
       in that same spot -- the banner was rendering on top of it, so the
       footer's own "Cookie preferences" button couldn't be clicked while
       the banner was showing. Publish the banner's real height as a CSS
       var and flag <html> with a class so index.html can pad the footer
       (or shift the app grid) clear of it. Read after appendChild so
       offsetHeight reflects the actual laid-out height (copy can wrap to
       2-3 lines depending on viewport width), not a guess. */
    try {
      document.documentElement.style.setProperty('--wf-banner-h', bannerEl.offsetHeight + 'px');
      document.documentElement.classList.add('wf-banner-open');
    } catch (e) { /* non-fatal: worst case the footer button hides behind the banner */ }

    bannerEl.addEventListener('click', function (e) {
      var action = actionFor(e.target, bannerEl);
      if (!action) return;
      e.preventDefault();
      if (action === 'accept') setState('granted');
      else if (action === 'reject') setState('denied');
      else if (action === 'prefs') openDialog();
    }, false);
  }

  function hideBanner() {
    if (!bannerEl) return;
    if (bannerEl.parentNode) bannerEl.parentNode.removeChild(bannerEl);
    bannerEl = null;
    try {
      document.documentElement.classList.remove('wf-banner-open');
      document.documentElement.style.removeProperty('--wf-banner-h');
    } catch (e) { /* non-fatal */ }
  }

  /* Walk up from the click target looking for a data-wf-c action, stopping at
     the container.  Hand-rolled instead of Element.closest() to keep the ES5
     promise, and because clicks can land on a nested <strong> or text node. */
  function actionFor(node, container) {
    while (node && node !== container) {
      if (node.nodeType === 1 && node.getAttribute) {
        var a = node.getAttribute('data-wf-c');
        if (a) return a;
      }
      node = node.parentNode;
    }
    return null;
  }

  /* ==========================================================
     9 · Preferences dialog
     ==========================================================
     Reachable by ANY visitor in ANY region, via window.wfConsent.open() or a
     click on any [data-wf-consent-open] element. */

  var dialogEl = null;
  var dialogOpener = null;
  var savedRootOverflow = null;

  var FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]), ' +
    'select:not([disabled]), textarea:not([disabled]), ' +
    '[tabindex]:not([tabindex="-1"])';

  /* Visible focusable descendants.  getClientRects() is empty for
     display:none elements, so hidden controls are excluded automatically.
     This mirrors _focusables() in index.html so both traps behave the same. */
  function focusables(container) {
    var nodes = container.querySelectorAll(FOCUSABLE);
    var out = [];
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].getClientRects().length > 0) out.push(nodes[i]);
    }
    return out;
  }

  function buildDialog() {
    if (dialogEl || !document.body) return;

    dialogEl = document.createElement('div');
    dialogEl.id = DIALOG_ID;
    dialogEl.setAttribute('role', 'dialog');
    dialogEl.setAttribute('aria-modal', 'true');
    dialogEl.setAttribute('aria-labelledby', 'wf-c-title');
    dialogEl.setAttribute('aria-describedby', 'wf-c-desc');
    dialogEl.hidden = true;

    dialogEl.innerHTML =
      '<div class="wf-c-modal">' +
        '<div class="wf-c-head">' +
          '<h2 class="wf-c-title" id="wf-c-title">Cookie preferences</h2>' +
          '<button type="button" class="wf-c-x" data-wf-c="close" aria-label="Close cookie preferences">&times;</button>' +
        '</div>' +

        '<div class="wf-c-body">' +
          '<p id="wf-c-desc">' +
            'WillIFit uses one optional cookie category: analytics. Everything ' +
            'else the site stores is what makes it work at all. Change your ' +
            'choice here any time &mdash; it takes effect immediately.' +
          '</p>' +

          /* role="status" so a screen reader hears the change the moment a
             choice is saved, without focus having to move. */
          '<p class="wf-c-status" id="wf-c-current" role="status">&nbsp;</p>' +

          '<h3>Strictly necessary &mdash; always on</h3>' +
          '<p class="wf-c-muted">' +
            'These are <strong>localStorage entries, not cookies</strong>. They stay ' +
            'on your device, are never attached to a request, and are never ' +
            'transmitted to our server. They are functional, not tracking: without ' +
            'them the site forgets your city and your vehicle height on every page ' +
            'load. The main ones (the <a href="/cookies.html">Cookie Policy</a> has ' +
            'the complete table):' +
          '</p>' +
          '<ul>' +
            '<li><code>willifit_last_city</code> and <code>willifit_last_center</code> &mdash; reopens the map where you left it</li>' +
            '<li><code>willifit_vehicle_height_in</code> &mdash; your vehicle height, so you do not retype it</li>' +
            '<li><code>willifit_saved_spots</code> &mdash; garages you saved</li>' +
            '<li><code>willifit_map_layer</code> &mdash; street or satellite view</li>' +
            '<li><code>willifit_report_queue</code>, <code>willifit_issue_queue</code>, <code>willifit_new_location_queue</code> &mdash; holds a report you submitted while offline until it can be sent</li>' +
            '<li><code>willifit_cookie_consent</code> &mdash; this choice. Clearing it makes the site ask again.</li>' +
          '</ul>' +

          '<h3>Analytics &mdash; your choice</h3>' +
          '<div class="wf-c-choice">' +
            '<input type="checkbox" id="wf-c-analytics">' +
            '<label for="wf-c-analytics">Allow analytics cookies</label>' +
          '</div>' +
          '<p class="wf-c-muted">' +
            'Google Analytics 4 tells us how many people visit and which pages are ' +
            'worth keeping. It is off by default in the EU, UK and Switzerland until ' +
            'you switch it on. Turning it off here also deletes the cookies below ' +
            'from this browser.' +
          '</p>' +
          '<table>' +
            '<caption>Cookies set only while analytics is allowed</caption>' +
            '<thead><tr>' +
              '<th scope="col">Cookie</th>' +
              '<th scope="col">Set by</th>' +
              '<th scope="col">Purpose</th>' +
              '<th scope="col">Expires</th>' +
            '</tr></thead>' +
            '<tbody>' +
              '<tr>' +
                '<td><code>_ga</code></td>' +
                '<td>Google Analytics 4</td>' +
                '<td>Assigns a random id so repeat visits are counted as one person rather than several</td>' +
                '<td>2 years</td>' +
              '</tr>' +
              '<tr>' +
                '<td><code>_ga_' + GA_MEASUREMENT_ID.replace(/^G-/, '') + '</code></td>' +
                '<td>Google Analytics 4</td>' +
                '<td>Keeps session state for the ' + GA_MEASUREMENT_ID + ' property (when your visit started, how many pages in)</td>' +
                '<td>2 years</td>' +
              '</tr>' +
            '</tbody>' +
          '</table>' +

          '<h3>Analytics that needs no consent</h3>' +
          '<p class="wf-c-muted">' +
            '<strong>Cloudflare Web Analytics</strong> counts page views without ' +
            'setting any cookie at all and without fingerprinting your device or ' +
            'browser. There is nothing stored on your machine and nothing to ' +
            'consent to, so it runs either way. It is not affected by the ' +
            'checkbox above.' +
          '</p>' +

          '<h3>Advertising</h3>' +
          '<p class="wf-c-muted">' +
            'No ad networks and no advertising cookies. Google&rsquo;s ' +
            '<code>ad_storage</code>, <code>ad_user_data</code> and ' +
            '<code>ad_personalization</code> signals are set to <em>denied</em> for ' +
            'every visitor in every country, and stay denied even if you allow ' +
            'analytics above.' +
          '</p>' +
          '<p class="wf-c-muted">' +
            'The site does carry sponsored cards, and we would rather be precise ' +
            'about them than say &ldquo;no ads&rdquo;. Which card you see is picked ' +
            '<strong>in your browser</strong>, out of a list downloaded to your ' +
            'device, using the city you are looking at, the vehicle height you ' +
            'entered, whether the oversized filter is on, and how close a sponsor is ' +
            'to the garage on screen. So it is targeted &mdash; but every one of ' +
            'those inputs stays on your device, and no ad network is involved at any ' +
            'point. Two counters, <code>willifit_ad_impression</code> and ' +
            '<code>willifit_ad_click</code>, record which cards this browser has ' +
            'already seen so the same one is not shown forever. They are localStorage, ' +
            'they are capped at the most recent 500, they are never uploaded, and ' +
            'they are not used to build a profile of you.' +
          '</p>' +

          '<p class="wf-c-muted">' +
            'Full detail: <a href="/cookies.html">Cookie Policy</a> &middot; ' +
            '<a href="/privacy.html">Privacy Policy</a>. Questions: ' +
            '<a href="mailto:privacy@willifit.ai">privacy@willifit.ai</a>' +
          '</p>' +
        '</div>' +

        '<div class="wf-c-foot">' +
          '<button type="button" class="wf-c-btn" data-wf-c="cancel">Cancel</button>' +
          '<button type="button" class="wf-c-btn" data-wf-c="save">Save preferences</button>' +
        '</div>' +
      '</div>';

    document.body.appendChild(dialogEl);

    dialogEl.addEventListener('click', function (e) {
      /* Click on the dim backdrop itself = dismiss without saving. */
      if (e.target === dialogEl) {
        closeDialog();
        return;
      }
      var action = actionFor(e.target, dialogEl);
      if (!action) return;
      if (action === 'close' || action === 'cancel') {
        e.preventDefault();
        closeDialog();
      } else if (action === 'save') {
        e.preventDefault();
        var box = document.getElementById('wf-c-analytics');
        setState(box && box.checked ? 'granted' : 'denied');
        closeDialog();
      }
    }, false);
  }

  /* Reflect the live state into the dialog: the checkbox and the status line.
     Safe to call when the dialog has never been built. */
  function syncDialogToState() {
    if (!dialogEl) return;
    var box = document.getElementById('wf-c-analytics');
    var status = document.getElementById('wf-c-current');
    var granted = effectiveGranted();
    if (box) box.checked = granted;
    if (status) {
      var stored = readStored();
      var text = granted
        ? 'Analytics cookies are currently allowed.'
        : 'Analytics cookies are currently blocked.';
      /* Explain WHY it is set the way it is, so the line is never just an
         unexplained state.  Order matters: a GPC visitor gets their decision
         recorded on first load, so `stored` is already set by the time they
         can open this dialog — checking !stored first would make the GPC
         sentence unreachable, which is exactly the bug this replaced. */
      if (!stored) {
        text += ' You have not made a choice yet, so this is the default for your region.';
      } else if (hasGPC() && !granted) {
        text += ' Your browser sends a Global Privacy Control signal, which we ' +
                'honour as a "no". Ticking the box below overrides it.';
      }
      status.innerHTML = '';
      status.appendChild(document.createTextNode(text));
    }
  }

  function openDialog() {
    if (!document.body) return;
    buildDialog();
    if (!dialogEl || !dialogEl.hidden) return;

    dialogOpener = document.activeElement;
    syncDialogToState();
    dialogEl.hidden = false;

    /* Stop the page scrolling behind the modal.  index.html already pins
       overflow:hidden on <html>, so there this is a no-op; the static pages
       are ordinary scrolling documents and would otherwise scroll under the
       dialog when the visitor uses the wheel or arrow keys. */
    try {
      savedRootOverflow = document.documentElement.style.overflow;
      document.documentElement.style.overflow = 'hidden';
    } catch (e) {}

    /* Move focus into the dialog. The checkbox is the thing the visitor came
       here to change, so it is the sensible landing point; if it is somehow
       not focusable we fall back to whatever is first. */
    var target = document.getElementById('wf-c-analytics') || focusables(dialogEl)[0];
    if (target) {
      try { target.focus(); } catch (e) {}
    }
  }

  function closeDialog() {
    if (!dialogEl || dialogEl.hidden) return;
    dialogEl.hidden = true;
    if (savedRootOverflow !== null) {
      try { document.documentElement.style.overflow = savedRootOverflow; } catch (e) {}
      savedRootOverflow = null;
    }
    /* Restore focus to whatever opened us — the footer button, the banner's
       "Preferences" link, or nothing if opened from the console. */
    if (dialogOpener && dialogOpener !== document.body &&
        typeof dialogOpener.focus === 'function') {
      try { dialogOpener.focus(); } catch (e) {}
    }
    dialogOpener = null;
  }

  function isDialogOpen() {
    return !!dialogEl && !dialogEl.hidden;
  }

  /* Escape closes; Tab / Shift+Tab wrap inside the dialog.  Attached once, to
     document, and inert whenever the dialog is closed — index.html has its
     own global keydown handler for its own modals and the two must not fight.
     Ours only calls preventDefault while our dialog is actually open. */
  function onKeydown(e) {
    if (!isDialogOpen()) return;

    if (e.key === 'Escape' || e.keyCode === 27) {
      e.preventDefault();
      e.stopPropagation();
      closeDialog();
      return;
    }

    if (e.key === 'Tab' || e.keyCode === 9) {
      var f = focusables(dialogEl);
      if (!f.length) { e.preventDefault(); return; }
      var first = f[0];
      var last = f[f.length - 1];
      var active = document.activeElement;

      if (!dialogEl.contains(active)) {
        /* Focus escaped (or never entered) — pull it back in. */
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      } else if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  /* ==========================================================
     10 · [data-wf-consent-open] delegation
     ==========================================================
     Delegated from document rather than bound per element, so the footer
     button works no matter when it appears in the DOM — including on
     index.html, where the mobile layout moves the footer links into the city
     picker after this script has run. */
  function onDocumentClick(e) {
    var node = e.target;
    while (node && node !== document) {
      if (node.nodeType === 1 && node.getAttribute &&
          node.getAttribute('data-wf-consent-open') !== null) {
        e.preventDefault();
        openDialog();
        return;
      }
      node = node.parentNode;
    }
  }

  /* ==========================================================
     11 · Boot
     ========================================================== */

  /* --- synchronous, head time: the part that must beat gtag.js --- */
  setDefaults();

  var stored = readStored();
  var gpc = hasGPC();

  if (stored) {
    /* A recorded decision, replayed on every page load.
       This update is queued into dataLayer BEFORE the page's own
       gtag('js') / gtag('config') calls, which sit further down the same
       <head>.  gtag.js replays the queue in order, so by the time 'config'
       runs the consent state is already correct and GA never gets a window
       in which it thinks it may write _ga.  Deferring this update until
       after load would leave exactly that window open for a visitor outside
       the EEA who had previously chosen "reject" — the general default
       grants analytics_storage, config would fire, and the cookie would be
       written a beat before we denied it. */
    pushUpdate(stored);
    if (stored === 'denied') {
      /* Sweep up anything left over from a previous "accept". */
      onReady(clearAnalyticsCookies);
    }
  } else if (gpc) {
    /* GPC is an unambiguous, browser-level "no". Record it and honour it
       silently — asking someone who has already answered is the thing the
       signal exists to prevent. */
    writeStored('denied');
    pushUpdate('denied');
    onReady(clearAnalyticsCookies);
  }

  /* --- deferred to DOM-ready: everything that needs document.body --- */
  function onReady(fn) {
    if (document.readyState === 'complete' ||
        document.readyState === 'interactive') {
      /* Already parsed past <head> (e.g. injected late) — run on the next
         tick so callers can finish their own setup first. */
      window.setTimeout(fn, 0);
    } else {
      document.addEventListener('DOMContentLoaded', fn, false);
    }
  }

  injectStyle();

  onReady(function () {
    injectStyle();  /* no-op if head existed at parse time; belt and braces */
    document.addEventListener('click', onDocumentClick, false);
    /* CAPTURE phase, deliberately.  index.html registers its own
       document-level keydown handler for its city-picker / report modals
       while the body is parsing, which is BEFORE our DOMContentLoaded
       callback runs.  A bubble-phase listener of ours would therefore fire
       second, and stopPropagation() at that point is already too late to
       stop index.html closing its modal as well.  Capturing on document runs
       before any bubble listener on document, so when our dialog is the
       top-most layer we can stop the event dead and be the only handler.
       When our dialog is closed, onKeydown returns immediately and the event
       is untouched. */
    document.addEventListener('keydown', onKeydown, true);

    /* Banner rules, in order:
         a stored decision  -> never (they answered)
         a GPC signal       -> never (they answered, via the browser)
         EEA/UK/CH          -> yes
         everywhere else    -> no; the footer control is their route in */
    if (!readStored() && !gpc && isEEA()) {
      showBanner();
    }
  });

  /* ==========================================================
     12 · Public API
     ========================================================== */
  window.wfConsent = {
    open: function () { openDialog(); },
    get: function () { return readStored(); },
    set: function (state) { return setState(state); },
    isEEA: function () { return isEEA(); },

    /* Below the line: QA affordances, not part of the contract above.
       They exist so the EEA banner path can be exercised from a console on a
       non-EEA machine without editing this file's logic. Do not build on
       them; they may be removed. */
    _showBanner: function () { injectStyle(); showBanner(); },
    _hideBanner: function () { hideBanner(); },
    _clearStored: function () {
      try { window.localStorage.removeItem(STORAGE_KEY); } catch (e) {}
    },
    _clearCookies: function () { clearAnalyticsCookies(); }
  };
})();
