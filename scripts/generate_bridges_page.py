#!/usr/bin/env python3
"""Generate /lowest-bridges-in-america.html — a data story computed from the
live corpus (national top-25 lowest posted bridge clearances + the lowest in
every covered state).  This is the site's linkable asset: journalists, RV
bloggers, and forum posters cite "lowest bridge" lists, and ours is backed by
inspectable data.  Re-run after imports so the numbers track the corpus.
"""
import html
import json
from datetime import date
from pathlib import Path

from wf_common import STATE_NAMES

REPO = Path(__file__).resolve().parent.parent
SITE = "https://willifit.ai"
OUT = REPO / "lowest-bridges-in-america.html"

esc = lambda s: html.escape(str(s or ""), quote=True)


def label(inches):
    ft, rem = int(inches) // 12, int(inches) % 12
    return f"{ft}'{rem}\""


def safe_jsonld(obj) -> str:
    """Serialize to compact JSON, then neutralize characters that could
    break out of the <script type="application/ld+json"> block (e.g. a
    structure name pulled from OSM containing '</script>').  The escapes are
    valid inside a JSON string and inert in HTML, so this still parses back
    to an identical object -- asserted below on every call."""
    raw = json.dumps(obj, separators=(",", ":"))
    escaped = raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    assert "<" not in escaped and ">" not in escaped, \
        "JSON-LD escaping failed to remove angle brackets"
    assert json.loads(escaped) == obj, \
        "JSON-LD escaping altered the payload"
    return escaped


def main():
    idx = json.loads((REPO / "data/index.json").read_text())
    live = {c["slug"]: c for c in idx if c.get("status") == "live"}

    rows = []
    for slug, meta in live.items():
        p = REPO / "data/cities" / f"{slug}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        for b in (d.get("bridges") or []) + (d.get("tunnels") or []):
            h = b.get("height_in")
            # Plausibility floor: posted under-clearances below 6' are almost
            # always pedestrian/culvert artifacts, not drivable roads.
            if isinstance(h, (int, float)) and 72 <= h <= 168:
                rows.append({
                    "h": int(h), "name": (b.get("name") or "Unnamed underpass")[:90],
                    "slug": slug, "city": meta["name"], "state": meta["state"],
                })

    rows.sort(key=lambda r: r["h"])
    # National top 25 — one entry per (city, height, name) to avoid dupes
    seen, top = set(), []
    for r in rows:
        k = (r["slug"], r["h"], r["name"].lower())
        if k in seen:
            continue
        seen.add(k)
        top.append(r)
        if len(top) >= 25:
            break

    # Lowest per state
    by_state = {}
    for r in rows:
        st = r["state"]
        if st not in by_state or r["h"] < by_state[st]["h"]:
            by_state[st] = r

    today = date.today().isoformat()
    n_bridges = len(rows)

    def row_html(r, rank=None, state_col=False):
        rk = f"<td>{rank}</td>" if rank is not None else ""
        st_col = (f'<td><a href="/state/{r["state"].lower()}">'
                  f'{esc(STATE_NAMES.get(r["state"], r["state"]))}</a></td>'
                  if state_col else "")
        return (f"<tr>{rk}{st_col}<td><b>{label(r['h'])}</b></td>"
                f"<td>{esc(r['name'])}</td>"
                f"<td><a href=\"/city/{esc(r['slug'])}\">{esc(r['city'])}, {esc(r['state'])}</a></td></tr>")

    top_rows = "\n".join(row_html(r, i + 1) for i, r in enumerate(top))
    state_rows = "\n".join(
        row_html(by_state[st], state_col=True)
        for st in sorted(by_state, key=lambda s: by_state[s]["h"]))

    FAQS = [
        {
            "q": "What is the lowest bridge in America?",
            "a": (
                f"Among the {n_bridges:,} low-clearance bridges, underpasses, and tunnels tracked "
                f"by WillIFit.ai, the lowest posted clearance is {label(top[0]['h']) if top else '?'} "
                f"at {top[0]['name'] if top else '?'} in {top[0]['city'] if top else '?'}, "
                f"{top[0]['state'] if top else '?'}. Posted clearances nationwide vary widely by "
                "region and structure age; always trust the sign in front of you over any database."
            ),
        },
        {
            "q": "How many low-clearance bridges does WillIFit.ai track?",
            "a": (
                f"WillIFit.ai tracks {n_bridges:,} bridges, underpasses, and tunnels posted between "
                f"6' and 14' across {len(by_state)} US states and territories, sourced from the FHWA "
                "National Bridge Inventory, OpenStreetMap, and AI-verified readings of posted Street "
                "View signage."
            ),
        },
        {
            "q": "What vehicles are at risk from low bridges?",
            "a": (
                "Any vehicle taller than about 11'6\" is at meaningful risk nationwide: standard box "
                "trucks run 12'6\" to 13'6\", moving trucks and RVs commonly reach 13'6\", and even "
                "high-roof cargo vans (roughly 9'6\") can strike older, lower urban underpasses. "
                "Bridge strikes are almost always preventable by checking posted clearance before "
                "routing a tall vehicle."
            ),
        },
    ]

    faq_section = "".join(f"<h3>{esc(f['q'])}</h3><p>{esc(f['a'])}</p>" for f in FAQS)

    jsonld = safe_jsonld([
        {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": "The Lowest Bridges in America: Posted Clearances Under 14 Feet",
            "description": f"The lowest posted bridge and underpass clearances across 226 US cities, computed from a corpus of {n_bridges:,} tracked low-clearance structures. National top 25 plus the lowest in every covered state.",
            "image": f"{SITE}/og-image.png",
            "inLanguage": "en-US",
            "datePublished": "2026-07-06",
            "dateModified": today,
            "author": {"@type": "Organization", "name": "WillIFit.ai", "url": f"{SITE}/"},
            "publisher": {"@type": "Organization", "name": "WillIFit.ai", "url": f"{SITE}/",
                          "logo": {"@type": "ImageObject", "url": f"{SITE}/og-image.png"}},
            "mainEntityOfPage": {"@type": "WebPage", "@id": f"{SITE}/lowest-bridges-in-america.html"},
            "speakable": {"@type": "SpeakableSpecification", "cssSelector": ["#lowest-answer"]},
        },
        {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{SITE}/"},
                {"@type": "ListItem", "position": 2, "name": "Lowest Bridges in America",
                 "item": f"{SITE}/lowest-bridges-in-america.html"},
            ],
        },
        {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": f["q"],
                    "acceptedAnswer": {"@type": "Answer", "text": f["a"]},
                }
                for f in FAQS
            ],
        },
    ])

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="index,follow">
<title>Lowest Bridges in America: Posted Clearances | WillIFit.ai</title>
<meta name="description" content="{esc(f"The 25 lowest posted bridge clearances in the US plus the lowest bridge in every covered state, computed from {n_bridges:,} tracked low-clearance structures.")}">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="canonical" href="{SITE}/lowest-bridges-in-america.html">
<meta property="og:title" content="The Lowest Bridges in America — Posted Clearances">
<meta property="og:description" content="The 25 lowest posted bridge clearances in the US, plus the lowest in every covered state. From a corpus of {n_bridges:,} tracked structures.">
<meta property="og:type" content="article">
<meta property="og:image" content="{SITE}/og-image.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:url" content="{SITE}/lowest-bridges-in-america.html">
<meta property="og:site_name" content="WillIFit.ai">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="The Lowest Bridges in America — Posted Clearances">
<meta name="twitter:description" content="The 25 lowest posted bridge clearances in the US, plus the lowest per state.">
<meta name="twitter:image" content="{SITE}/og-image.png">
<script type="application/ld+json">{jsonld}</script>
<style>
  :root {{
    --bg: #0e1116; --panel: #171b23; --text: #e6eaf0; --muted: #8a95a6;
    --border: #2a3140; --accent: #0ea5e9; --warn: #f5a623; --bad: #e5484d;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
         font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         line-height: 1.65; }}
  /* Skip-link for keyboard users -- same visually-hidden-until-focused
     pattern as the main SPA (index.html .skip-link). */
  .skip-link {{
    position: absolute; top: -40px; left: 0;
    background: var(--ok); color: #0a0f18;
    padding: 8px 12px; z-index: 9999;
    font-family: 'SF Mono', monospace; font-size: 12px;
    text-decoration: none; font-weight: 700;
  }}
  .skip-link:focus {{ top: 0; }}
  .page {{ max-width: 820px; margin: 0 auto; padding: 40px 24px 80px; }}
  header {{ border-bottom: 1px solid var(--border); padding-bottom: 20px; margin-bottom: 26px; }}
  h1 {{ margin: 0 0 10px; font-size: 31px; letter-spacing: -0.02em; }}
  h2 {{ margin-top: 42px; font-size: 21px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }}
  a {{ color: var(--accent); text-decoration: none; }}
  main a {{ text-decoration: underline; text-underline-offset: 2px; text-decoration-color: rgba(14,165,233,0.4); }}
  a:hover {{ text-decoration: underline; }}
  /* Tap target: WCAG 2.2 min 24x24, aim 44px on mobile (matches the
     header a.brand / a.crumb pattern in generate_city_pages.py). */
  .back {{
    display: inline-flex; align-items: center;
    min-height: 24px; padding: 6px 4px; margin: -6px -4px 10px;
    font-size: 13px; color: var(--muted);
  }}
  @media (max-width: 600px) {{
    .back {{ min-height: 44px; padding: 12px 4px; margin: -12px -4px 10px; }}
  }}
  .lede {{ font-size: 17px; color: var(--muted); margin: 0; }}
  .answer {{
    background: linear-gradient(135deg, rgba(245,166,35,0.14), rgba(245,166,35,0.04));
    border: 1px solid rgba(245,166,35,0.4); border-left: 4px solid var(--warn);
    border-radius: 8px; padding: 16px 20px; margin: 22px 0; font-size: 15.5px;
  }}
  table {{ width: 100%; border-collapse: collapse; margin: 18px 0; font-size: 14px; }}
  th, td {{ text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--muted); font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.04em; }}
  tr:last-child td {{ border-bottom: none; }}
  td b {{ color: var(--bad); font-variant-numeric: tabular-nums; }}
  .src {{ font-size: 12.5px; color: var(--muted); }}
  .table-wrap {{ overflow-x: auto; }}
  table caption {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
                   overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }}
  footer {{ margin-top: 60px; padding-top: 20px; border-top: 1px solid var(--border);
           font-size: 12px; color: var(--muted); display: flex; justify-content: space-between;
           flex-wrap: wrap; gap: 12px; }}
  footer a {{ color: var(--muted); }}
</style>
<script src="/js/consent.js"></script>
<!-- Cloudflare Web Analytics -->
<script defer src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon='{{"token": "162b93f801fa42499a0b840c50d3f772"}}'></script>
<!-- End Cloudflare Web Analytics -->
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-SH191K5NGS"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', 'G-SH191K5NGS');
</script>
<!-- End Google tag -->
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
<div class="page">
  <nav aria-label="Breadcrumb"><a href="/" class="back">← Back to WillIFit.ai</a></nav>
  <header>
    <h1>The Lowest Bridges in America</h1>
    <p class="lede">The lowest posted vehicle clearances we track across 226 US cities —
       computed from {n_bridges:,} low-clearance bridges, underpasses, and tunnels, updated {today}.</p>
  </header>

  <main id="main">
  <div class="answer" id="lowest-answer">
    <b>The lowest posted drivable clearance in our database is {label(top[0]['h']) if top else '?'}</b> —
    {esc(top[0]['name']) if top else ''} in {esc(top[0]['city']) if top else ''}, {esc(top[0]['state']) if top else ''}.
    For context: a standard box truck is 12'6"–13'6" tall, a Class C RV about 10'–11'6", and a
    high-roof Sprinter about 9'6". Every bridge on this list can take the roof off something.
    This ranks the lowest posted clearance for road vehicles under each structure, not how tall or short the bridge itself is.
  </div>

  <h2>The 25 lowest posted clearances</h2>
  <div class="table-wrap" tabindex="0" role="region" aria-label="The 25 lowest posted clearances, scrollable table">
  <table>
    <caption>The 25 lowest posted vertical clearances in the WillIFit database</caption>
    <thead><tr><th scope="col">#</th><th scope="col">Posted</th><th scope="col">Structure</th><th scope="col">City</th></tr></thead>
    <tbody>
{top_rows}
    </tbody>
  </table>
  </div>
  <p class="src">Posted clearance as recorded from OpenStreetMap, the FHWA National Bridge Inventory,
     or AI-verified Street View signage. Posted numbers can change with re-paving and re-signage —
     always trust the sign in front of you over any database, including this one.</p>

  <h2>The lowest bridge in every covered state</h2>
  <div class="table-wrap" tabindex="0" role="region" aria-label="The lowest bridge in every covered state, scrollable table">
  <table>
    <caption>The lowest posted bridge clearance in each covered state</caption>
    <thead><tr><th scope="col">State</th><th scope="col">Posted</th><th scope="col">Structure</th><th scope="col">City</th></tr></thead>
    <tbody>
{state_rows}
    </tbody>
  </table>
  </div>

  <h2 id="faq">Frequently asked questions</h2>
  {faq_section}

  <h2>Methodology</h2>
  <p>WillIFit.ai tracks {n_bridges:,} low-clearance bridges, underpasses, and tunnels (posted between
     6' and 14') across 226 US cities, sourced from the
     <a href="https://www.fhwa.dot.gov/bridge/nbi.cfm" target="_blank" rel="noopener">FHWA National Bridge Inventory</a>,
     <a href="https://www.openstreetmap.org" target="_blank" rel="noopener">OpenStreetMap</a>, and our own
     <a href="/how-ai-verification-works.html">AI verification of posted signage</a> in Google Street View.
     This page is regenerated from the live database; figures reflect posted signs as recorded, which are
     often set below true clearance as a safety margin. Structures posted under 6' are excluded as likely
     pedestrian or culvert records. See a bridge we're missing?
     <a href="/">Open the map</a> and report it.</p>

  <p>Planning a route in a tall vehicle? Check your height against every garage, tunnel, and bridge in
     <a href="/cities.html">226 cities</a>, or read the
     <a href="/parking-garage-clearance-heights.html">parking garage clearance guide</a>.</p>
  </main>

  <footer>
    <div>© <span id="y"></span> WillIFit.ai — clearance data for oversized vehicles.</div>
    <div>
      <a href="/about.html">About</a> ·
      <a href="/accessibility.html">Accessibility</a> ·
      <a href="/how-ai-verification-works.html">How AI verification works</a> ·
      <a href="/parking-garage-clearance-heights.html">Clearance guide</a> ·
      <a href="/vehicle-heights.html">Vehicle heights</a> ·
      <a href="/lowest-bridges-in-america.html">Lowest bridges</a> ·
      <a href="/advertise.html">Advertise</a> ·
      <a href="/disclaimer.html">Disclaimer</a> ·
      <a href="/terms.html">Terms</a> ·
      <a href="/privacy.html">Privacy</a> ·
      <a href="/dmca.html">DMCA</a> ·
      <a href="/cookies.html">Cookies</a> ·
      <button type="button" data-wf-consent-open class="wf-consent-btn">Cookie preferences</button> ·
      <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">© OpenStreetMap</a> ·
      <a href="https://www.fhwa.dot.gov/bridge/nbi/" target="_blank" rel="noopener">FHWA NBI</a>
    </div>
  </footer>
</div>
<script>document.getElementById('y').textContent = new Date().getFullYear();</script>
</body>
</html>
"""
    OUT.write_text(page)
    print(f"Wrote {OUT.name}: top25 floor {label(top[0]['h']) if top else '?'}, "
          f"{len(by_state)} states, {n_bridges:,} qualifying structures")


if __name__ == "__main__":
    main()
