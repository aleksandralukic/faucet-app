#!/usr/bin/env python3
"""
Generate static HTML from faucets.json + status.json.

Why this exists: the site was fully client-side rendered, so the HTML a crawler
receives contained an empty <div> and nothing else. Search engines can execute
JS, but unreliably, and most other crawlers do not. This bakes the real content
into the markup at build time. The JS still layers filtering on top for humans.

Generates:
  index.html                       homepage list injected between markers
  <currency>-testnet-faucet/       one page per currency (long-tail queries)
  down/                            currently-down faucets ("testnet faucet down")
  sitemap.xml, robots.txt

Stdlib only. Run after check_faucets.py.
"""

import html
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# Change this one value when moving to a custom domain (no trailing slash).
SITE_URL = os.environ.get("SITE_URL", "https://aleksandralukic.github.io/faucet-app").rstrip("/")
SITE_NAME = "Faucet App"

STATUS_LABEL = {
    "up": "Working",
    "degraded": "Degraded",
    "down": "Down",
    "manual": "Manual check",
    "unknown": "Unknown",
}


def e(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def slug(s):
    s = re.sub(r"[^a-z0-9]+", "-", str(s).lower())
    return s.strip("-")


def page(title, description, canonical, body, depth=0, extra_head=""):
    """Shared HTML skeleton. `depth` sets how far assets are from this page."""
    up = "../" * depth
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
<link rel="canonical" href="{e(canonical)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(description)}">
<meta property="og:url" content="{e(canonical)}">
<meta property="og:site_name" content="{e(SITE_NAME)}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="{e(description)}">
<link rel="stylesheet" href="{up}assets/style.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🚰</text></svg>">
{extra_head}
</head>
<body>
{body}
</body>
</html>
"""


# Generic words that carry no search intent — stripped when deriving the
# distinctive keyword from a network name ("Ethereum Sepolia" -> "Sepolia").
NETWORK_STOPWORDS = {
    "testnet", "devnet", "mainnet", "chain", "network", "smart", "provider",
    "test", "test2", "the", "c-chain", "calibration", "preprod",
}


def network_keywords(networks):
    """Distinctive search terms from network names, e.g. 'Sepolia', 'Amoy', 'Fuji'.

    These are the words people actually type ('sepolia faucet down'), which are
    usually NOT the token ticker. Surfacing them in the title and description is
    what lets a page rank for a network-name query.
    """
    seen, out = set(), []
    for net in networks:
        for word in re.split(r"[\s/]+", net):
            w = word.strip()
            if not w or w.lower() in NETWORK_STOPWORDS:
                continue
            key = w.lower()
            if key not in seen:
                seen.add(key)
                out.append(w)
    return out


def status_of(fid, status_by_id):
    return status_by_id.get(fid, {}).get("status", "unknown")


def freshness(generated_at):
    if not generated_at:
        return "not yet checked"
    try:
        dt = datetime.fromisoformat(generated_at)
    except ValueError:
        return generated_at
    return dt.strftime("%d %B %Y at %H:%M UTC")


# ---------------------------------------------------------------- homepage

def render_card(f, st):
    """Server-rendered equivalent of the card the JS builds."""
    s = st.get("status", "unknown")
    bits = []
    if f.get("requiresCaptcha"):
        bits.append("captcha")
    if f.get("requiresWallet"):
        bits.append("wallet connect")
    if st.get("uptimePct") is not None:
        bits.append(f"{st['uptimePct']}% uptime")

    cur_slug = slug(f["currency"])
    fail = ""
    if st.get("failureLabel"):
        fail = f'<p class="reason">{e(st["failureLabel"])} — {e(st.get("failureAdvice", ""))}</p>'

    return f"""<article class="card {e(s)}">
  <div class="card-top">
    <span class="ticker">{e(f["currency"])}</span>
    <a href="{e(f["url"])}" target="_blank" rel="noopener">{e(f["name"])}</a>
    <span class="network">{e(f["network"])}</span>
    <span class="status-line"><span class="dot {e(s)}"></span>{e(STATUS_LABEL.get(s, s))}</span>
  </div>
  {f'<p class="notes">{e(f["notes"])}</p>' if f.get("notes") else ""}
  <div class="meta">{"".join(f'<span class="tag">{e(b)}</span>' for b in bits)}
    <a class="tag" href="{cur_slug}-testnet-faucet/">{e(f["currency"])} faucet status →</a>
  </div>
  {fail}
</article>"""


def build_home(faucets, status_by_id, generated_at, summary):
    path = os.path.join(ROOT, "index.html")
    with open(path, encoding="utf-8") as fh:
        shell = fh.read()

    order = ["up", "degraded", "down", "manual", "unknown"]
    ordered = sorted(
        faucets,
        key=lambda f: (order.index(status_of(f["id"], status_by_id)), f["currency"]),
    )
    cards = "\n".join(render_card(f, status_by_id.get(f["id"], {})) for f in ordered)

    start, end = "<!-- FAUCET_LIST:START -->", "<!-- FAUCET_LIST:END -->"
    if start not in shell or end not in shell:
        print("ERROR: markers missing from index.html", file=sys.stderr)
        return False

    pre, rest = shell.split(start, 1)
    _, post = rest.split(end, 1)
    shell = f"{pre}{start}\n{cards}\n{end}{post}"

    # Keep the crawler-visible summary sentence in sync with the real numbers.
    line = (
        f"{summary.get('up', 0)} of {len(faucets)} testnet faucets working, "
        f"{summary.get('down', 0)} down. Last checked {freshness(generated_at)}."
    )
    shell = re.sub(
        r'(<p id="seo-summary"[^>]*>).*?(</p>)',
        lambda m: m.group(1) + html.escape(line) + m.group(2),
        shell,
        flags=re.S,
    )

    itemlist = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "Blockchain testnet faucets",
        "numberOfItems": len(ordered),
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": f"{f['currency']} — {f['name']}",
                "url": f["url"],
            }
            for i, f in enumerate(ordered)
        ],
    }
    shell = re.sub(
        r'(<script type="application/ld\+json" id="ld-home">).*?(</script>)',
        lambda m: m.group(1) + json.dumps(itemlist) + m.group(2),
        shell,
        flags=re.S,
    )

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(shell)
    return True


# ------------------------------------------------------- per-currency pages

def build_currency_pages(faucets, status_by_id, generated_at):
    """One page per currency, targeting '<X> testnet faucet' style queries."""
    groups = {}
    for f in faucets:
        groups.setdefault(f["currency"], []).append(f)

    written = []
    for currency, items in sorted(groups.items()):
        cslug = slug(currency)
        dirname = f"{cslug}-testnet-faucet"
        outdir = os.path.join(ROOT, dirname)
        os.makedirs(outdir, exist_ok=True)

        networks = sorted({f["network"] for f in items})
        statuses = [status_of(f["id"], status_by_id) for f in items]
        any_up = "up" in statuses
        working = statuses.count("up")

        # Lead the title/description with the network keyword people actually
        # search ("Sepolia", "Amoy", "Fuji"). The distinctive testnet codename
        # is nearly always the LAST clean word of an EVM network name, so prefer
        # that over the chain family ("Ethereum"). Fall back to the ticker when
        # there's no distinct, human-readable codename (e.g. Sei Atlantic-2).
        keywords = network_keywords(networks)
        clean = [
            k for k in keywords
            if k.lower() != currency.lower() and k.isalpha() and len(k) >= 4
        ]
        lead = clean[-1] if clean else currency
        kw = [k for k in keywords if k.lower() != lead.lower()][:2]
        also = f" ({currency})" if lead.lower() != currency.lower() else ""

        headline = (
            f"{working} of {len(items)} working"
            if len(items) > 1
            else STATUS_LABEL.get(statuses[0], statuses[0])
        )
        title = f"{lead}{also} Testnet Faucet — Is It Down? Live Status | {SITE_NAME}"
        # Pack the distinct phrasings a searcher uses into the meta description.
        phrases = ", ".join(dict.fromkeys([lead] + kw))
        desc = (
            f"Is the {lead} testnet faucet down? Live status for "
            f"{len(items)} {currency} faucet{'s' if len(items) > 1 else ''} on "
            f"{', '.join(networks)}, checked daily — {phrases} testnet faucet "
            f"working or not. Last checked {freshness(generated_at)}."
        )

        sections = []
        faqs = []
        for f in items:
            st = status_by_id.get(f["id"], {})
            s = st.get("status", "unknown")
            hist = st.get("history") or []

            detail = [
                f'<p><strong>Status:</strong> <span class="dot {e(s)}"></span> {e(STATUS_LABEL.get(s, s))}'
                f' — <span class="muted">{e(st.get("reason", "not yet checked"))}</span></p>'
            ]
            if st.get("uptimePct") is not None:
                detail.append(
                    f'<p><strong>Uptime:</strong> {st["uptimePct"]}% across the last '
                    f'{len(hist)} daily check{"s" if len(hist) != 1 else ""}.</p>'
                )
            if st.get("failureLabel"):
                detail.append(
                    f'<p><strong>Why it is failing:</strong> {e(st["failureLabel"])}. '
                    f'{e(st.get("failureAdvice", ""))}</p>'
                )
            if f.get("notes"):
                detail.append(f'<p><strong>How to use it:</strong> {e(f["notes"])}</p>')

            reqs = []
            if f.get("requiresCaptcha"):
                reqs.append("solving a captcha")
            if f.get("requiresWallet"):
                reqs.append("connecting a wallet")
            if reqs:
                detail.append(f"<p><strong>Requires:</strong> {e(' and '.join(reqs))}.</p>")

            sections.append(f"""<section class="card {e(s)}">
  <h2>Is the {e(f["name"])} down right now?</h2>
  {"".join(detail)}
  <p><a href="{e(f["url"])}" target="_blank" rel="noopener">Open {e(f["name"])} ↗</a>
     <span class="muted">({e(f["network"])})</span></p>
</section>""")

            answer = (
                f"No. As of {freshness(generated_at)} the {f['name']} responded normally."
                if s == "up" else
                f"Yes. As of {freshness(generated_at)} the {f['name']} is not responding normally: "
                f"{st.get('failureLabel') or st.get('reason', 'unknown reason')}. "
                f"{st.get('failureAdvice', '')}"
                if s == "down" else
                f"Partly. As of {freshness(generated_at)} the {f['name']} returned a degraded "
                f"response: {st.get('reason', 'unknown')}. It may still work in a browser."
            )
            faqs.append({
                "@type": "Question",
                "name": f"Is the {f['name']} down?",
                "acceptedAnswer": {"@type": "Answer", "text": answer},
            })

        alternatives = ""
        if not any_up and len(items):
            alternatives = (
                '<p class="callout">Every listed ' + e(currency) + ' faucet is currently failing. '
                'Check the <a href="../">full faucet list</a> for other networks, or '
                '<a href="https://github.com/aleksandralukic/faucet-app/issues">report a working one</a>.</p>'
            )

        faq_ld = json.dumps({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": faqs,
        })

        h1 = f"{lead}{also} Testnet Faucet Status" if also else f"{currency} Testnet Faucet Status"
        body = f"""<header class="masthead"><div class="wrap">
  <p class="crumb"><a href="../">← All testnet faucets</a></p>
  <h1>{e(h1)}</h1>
  <p class="tagline">Is the {e(lead)} testnet faucet down? Live status for {len(items)} {e(currency)} faucet{"s" if len(items) > 1 else ""} on {e(", ".join(networks))}, re-checked every day.</p>
  <p class="generated">Last checked {e(freshness(generated_at))}.</p>
</div></header>
<main class="wrap">
  {alternatives}
  {"".join(sections)}
  <section>
    <h2>Why {e(currency)} testnet faucets stop working</h2>
    <p>Testnet faucets are run on a best-effort basis and break constantly: domains
    lapse, TLS certificates expire, rate limits tighten, and faucet wallets run dry.
    This page re-checks every {e(currency)} faucet daily and records the cause of
    failure, so you can tell a dead domain from a temporary outage before you waste
    time on it.</p>
    <p class="muted">A "working" result means the faucet's page responded normally.
    It cannot prove the faucet still holds funds — only a real claim does that.</p>
  </section>
</main>
<footer class="wrap footer"><p><a href="../">{e(SITE_NAME)}</a> — testnet faucet status, checked daily.
<a href="https://github.com/aleksandralukic/faucet-app">Source on GitHub</a>.</p></footer>"""

        out = page(
            title, desc, f"{SITE_URL}/{dirname}/", body, depth=1,
            extra_head=f'<script type="application/ld+json">{faq_ld}</script>',
        )
        with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as fh:
            fh.write(out)
        written.append(dirname)

    return written


# ------------------------------------------------------------- /down/ page

def build_down_page(faucets, status_by_id, generated_at):
    """Targets 'testnet faucet down' — unique content nobody else has."""
    outdir = os.path.join(ROOT, "down")
    os.makedirs(outdir, exist_ok=True)

    broken = [
        (f, status_by_id.get(f["id"], {}))
        for f in faucets
        if status_of(f["id"], status_by_id) in ("down", "degraded")
    ]
    broken.sort(key=lambda p: (p[1].get("status", ""), p[0]["currency"]))

    rows = "".join(
        f"""<tr>
  <td><span class="dot {e(st.get("status"))}"></span> {e(STATUS_LABEL.get(st.get("status"), "?"))}</td>
  <td><strong>{e(f["currency"])}</strong></td>
  <td><a href="../{slug(f["currency"])}-testnet-faucet/">{e(f["name"])}</a></td>
  <td>{e(f["network"])}</td>
  <td>{e(st.get("failureLabel") or st.get("reason", ""))}</td>
</tr>"""
        for f, st in broken
    )

    permanent = [f for f, st in broken if st.get("failurePermanent")]
    perm_note = ""
    if permanent:
        names = ", ".join(f"{f['currency']} ({f['name']})" for f in permanent)
        perm_note = (
            f'<p class="callout"><strong>Permanently gone:</strong> {e(names)}. '
            "These domains no longer resolve at all — they are not coming back, "
            "so look for a replacement rather than waiting.</p>"
        )

    body = f"""<header class="masthead"><div class="wrap">
  <p class="crumb"><a href="../">← All testnet faucets</a></p>
  <h1>Testnet Faucets Currently Down</h1>
  <p class="tagline">{len(broken)} of {len(faucets)} tracked faucets are failing right now.</p>
  <p class="generated">Last checked {e(freshness(generated_at))}.</p>
</div></header>
<main class="wrap">
  {perm_note}
  <div class="table-scroll"><table>
    <thead><tr><th>Status</th><th>Token</th><th>Faucet</th><th>Network</th><th>Cause</th></tr></thead>
    <tbody>{rows or '<tr><td colspan="5">Everything is working right now.</td></tr>'}</tbody>
  </table></div>
  <section>
    <h2>Testnet faucet not working — what to do</h2>
    <p>Work through these in order:</p>
    <ol>
      <li><strong>Check the cause above.</strong> A dead domain needs a replacement;
          a timeout or server error is often worth retrying in an hour.</li>
      <li><strong>Try it in a real browser.</strong> Faucets marked <em>Blocked (403)</em>
          are refusing automated requests but usually work for humans.</li>
      <li><strong>Check for a second faucet on the same token.</strong> Several
          networks have more than one — the per-token pages list them all.</li>
      <li><strong>Check the rate limit.</strong> Most faucets allow one claim per
          address per 24 hours, and a silent failure often just means you already claimed.</li>
      <li><strong>Check your address format.</strong> Several Bitcoin testnet faucets
          reject SegWit addresses outright.</li>
    </ol>
  </section>
</main>
<footer class="wrap footer"><p><a href="../">{e(SITE_NAME)}</a> — testnet faucet status, checked daily.
<a href="https://github.com/aleksandralukic/faucet-app">Source on GitHub</a>.</p></footer>"""

    out = page(
        "Testnet Faucets Down Right Now — Live Status | " + SITE_NAME,
        f"Which testnet faucets are down right now: {len(broken)} of {len(faucets)} "
        f"tracked faucets are failing, with the cause of each failure. Checked daily.",
        f"{SITE_URL}/down/", body, depth=1,
    )
    with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(out)


# --------------------------------------------------------- sitemap + robots

def build_sitemap(dirs, generated_at):
    today = (generated_at or datetime.now(timezone.utc).isoformat())[:10]
    urls = [f"{SITE_URL}/", f"{SITE_URL}/down/"] + [f"{SITE_URL}/{d}/" for d in dirs]
    entries = "".join(
        f"<url><loc>{e(u)}</loc><lastmod>{e(today)}</lastmod>"
        f"<changefreq>daily</changefreq></url>\n"
        for u in urls
    )
    with open(os.path.join(ROOT, "sitemap.xml"), "w", encoding="utf-8") as fh:
        fh.write(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{entries}</urlset>\n"
        )

    with open(os.path.join(ROOT, "robots.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n")

    return len(urls)


def clean_stale(current_dirs):
    """Remove pages for currencies that no longer exist in faucets.json."""
    keep = set(current_dirs) | {"down"}
    removed = []
    for name in os.listdir(ROOT):
        full = os.path.join(ROOT, name)
        if not os.path.isdir(full) or name.startswith("."):
            continue
        if name.endswith("-testnet-faucet") and name not in keep:
            shutil.rmtree(full)
            removed.append(name)
    return removed


def main():
    with open(os.path.join(DATA, "faucets.json"), encoding="utf-8") as fh:
        faucets = json.load(fh)

    status_path = os.path.join(DATA, "status.json")
    if os.path.exists(status_path):
        with open(status_path, encoding="utf-8") as fh:
            status = json.load(fh)
    else:
        status = {"results": [], "generatedAt": None, "summary": {}}

    status_by_id = {r["id"]: r for r in status.get("results", [])}
    generated_at = status.get("generatedAt")
    summary = status.get("summary", {})

    if not build_home(faucets, status_by_id, generated_at, summary):
        return 1

    dirs = build_currency_pages(faucets, status_by_id, generated_at)
    build_down_page(faucets, status_by_id, generated_at)
    removed = clean_stale(dirs)
    n = build_sitemap(dirs, generated_at)

    print(f"Homepage rendered with {len(faucets)} faucets")
    print(f"Currency pages: {len(dirs)}")
    print(f"Sitemap URLs:   {n}")
    if removed:
        print(f"Removed stale:  {', '.join(removed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
