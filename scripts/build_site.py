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
SITE_URL = os.environ.get("SITE_URL", "https://testnetfaucets.dev").rstrip("/")
SITE_NAME = "Faucet App"

# Cache-buster for CSS/JS. GitHub Pages serves assets with a 4h cache, so without
# this a UI change wouldn't reach visitors until their cache expired. Set from the
# check's timestamp in main(), so every deploy ships a fresh asset URL.
BUILD_VERSION = "0"

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
<meta property="og:image" content="{SITE_URL}/og-image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="{e(description)}">
<meta name="twitter:image" content="{SITE_URL}/og-image.png">
<link rel="stylesheet" href="{up}assets/style.css?v={BUILD_VERSION}">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/favicon-32.png" type="image/png" sizes="32x32">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
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


def _dispensed_str(days):
    if days is None:
        return None
    if days < 1:
        return "today"
    return f"{days:g} day{'s' if days >= 2 else ''} ago"


def onchain_panel(oc):
    """A labeled table of the on-chain evidence (wallet balance is the anchor),
    shown on cards and faucet sections instead of buried as fine print."""
    if not (oc and oc.get("balanceStr")):
        return ""
    rows = [("Wallet balance", f'<span class="oc-bal">{e(oc["balanceStr"])}</span>',
             "Test tokens the faucet's dispensing wallet holds right now")]
    if oc.get("payoutsSent") is not None:
        rows.append((oc.get("countLabel", "payouts").capitalize(), f'{oc["payoutsSent"]:,}',
                     "Total transactions this wallet has sent — a high count means a real, active faucet"))
    disp = _dispensed_str(oc.get("lastDispenseDays"))
    if disp:
        rows.append(("Last dispensed", disp,
                     "Time since the wallet's most recent outbound payment"))
    body = "".join(
        f'<tr><th title="{e(t)}">{e(k)}</th><td>{v}</td></tr>' for k, v, t in rows
    )
    return (
        '<div class="onchain-panel">'
        '<div class="oc-tag">⛓ Verified on-chain</div>'
        f'<table class="oc-table">{body}</table>'
        '</div>'
    )


# EVM testnet parameters for "Add to your wallet", keyed by the faucet's
# `network` string. Non-EVM networks are omitted (no wallet_addEthereumChain).
NETWORKS = {
    "Ethereum Sepolia": {"chainId": 11155111, "rpc": "https://ethereum-sepolia-rpc.publicnode.com", "symbol": "ETH", "explorer": "https://sepolia.etherscan.io", "name": "Ethereum Sepolia"},
    "Polygon Amoy": {"chainId": 80002, "rpc": "https://polygon-amoy-bor-rpc.publicnode.com", "symbol": "POL", "explorer": "https://amoy.polygonscan.com", "name": "Polygon Amoy"},
    "Avalanche Fuji C-Chain": {"chainId": 43113, "rpc": "https://api.avax-test.network/ext/bc/C/rpc", "symbol": "AVAX", "explorer": "https://testnet.snowtrace.io", "name": "Avalanche Fuji C-Chain"},
    "BNB Smart Chain Testnet": {"chainId": 97, "rpc": "https://bsc-testnet-rpc.publicnode.com", "symbol": "tBNB", "explorer": "https://testnet.bscscan.com", "name": "BNB Smart Chain Testnet"},
    "Flare Coston2": {"chainId": 114, "rpc": "https://coston2-api.flare.network/ext/C/rpc", "symbol": "C2FLR", "explorer": "https://coston2-explorer.flare.network", "name": "Flare Coston2"},
    "Core test2": {"chainId": 1114, "rpc": "https://rpc.test2.btcs.network", "symbol": "tCORE2", "explorer": "https://scan.test2.btcs.network", "name": "Core Blockchain Testnet2"},
    "Sei Atlantic-2": {"chainId": 1328, "rpc": "https://evm-rpc-testnet.sei-apis.com", "symbol": "SEI", "explorer": "https://seitrace.com", "name": "Sei Atlantic-2"},
    "Filecoin Calibration": {"chainId": 314159, "rpc": "https://api.calibration.node.glif.io/rpc/v1", "symbol": "tFIL", "explorer": "https://calibration.filscan.io", "name": "Filecoin Calibration"},
    "Base Sepolia": {"chainId": 84532, "rpc": "https://sepolia.base.org", "symbol": "ETH", "explorer": "https://sepolia.basescan.org", "name": "Base Sepolia"},
    "Arbitrum Sepolia": {"chainId": 421614, "rpc": "https://sepolia-rollup.arbitrum.io/rpc", "symbol": "ETH", "explorer": "https://sepolia.arbiscan.io", "name": "Arbitrum Sepolia"},
    "Optimism Sepolia": {"chainId": 11155420, "rpc": "https://sepolia.optimism.io", "symbol": "ETH", "explorer": "https://sepolia-optimism.etherscan.io", "name": "OP Sepolia"},
    "Scroll Sepolia": {"chainId": 534351, "rpc": "https://sepolia-rpc.scroll.io", "symbol": "ETH", "explorer": "https://sepolia.scrollscan.com", "name": "Scroll Sepolia"},
    "Linea Sepolia": {"chainId": 59141, "rpc": "https://rpc.sepolia.linea.build", "symbol": "ETH", "explorer": "https://sepolia.lineascan.build", "name": "Linea Sepolia"},
    "Celo Alfajores": {"chainId": 44787, "rpc": "https://alfajores-forno.celo-testnet.org", "symbol": "CELO", "explorer": "https://alfajores.celoscan.io", "name": "Celo Alfajores"},
}

WALLET_SCRIPT = """<script>
document.querySelectorAll('.addchain').forEach(function(b){
  b.addEventListener('click', async function(){
    if(!window.ethereum){ alert('No EVM wallet detected — install MetaMask, then reload.'); return; }
    try { await window.ethereum.request({method:'wallet_addEthereumChain', params:[JSON.parse(b.dataset.chain)]}); }
    catch(err){ console.error(err); }
  });
});
</script>"""


def wallet_config_section(networks):
    """'Add <network> to your wallet' — the step a user needs right after tokens.
    EVM only; also good SEO for '<network> rpc / chainid' queries."""
    blocks = []
    for net in networks:
        c = NETWORKS.get(net)
        if not c:
            continue
        params = json.dumps({
            "chainId": hex(c["chainId"]),
            "chainName": c["name"],
            "rpcUrls": [c["rpc"]],
            "nativeCurrency": {"name": c["symbol"], "symbol": c["symbol"], "decimals": 18},
            "blockExplorerUrls": [c["explorer"]],
        })
        blocks.append(f"""<section class="wallet-config">
  <h2>Add {e(c["name"])} to your wallet</h2>
  <p class="muted">Configure MetaMask (Networks → Add network → Add manually), or one-click below.</p>
  <div class="table-scroll"><table class="kv">
    <tr><th>Network name</th><td>{e(c["name"])}</td></tr>
    <tr><th>New RPC URL</th><td><code>{e(c["rpc"])}</code></td></tr>
    <tr><th>Chain ID</th><td>{c["chainId"]}</td></tr>
    <tr><th>Currency symbol</th><td>{e(c["symbol"])}</td></tr>
    <tr><th>Block explorer</th><td><a href="{e(c["explorer"])}" target="_blank" rel="noopener">{e(c["explorer"])}</a></td></tr>
  </table></div>
  <button class="addchain" data-chain='{e(params)}'>Add to MetaMask</button>
</section>""")
    return ("".join(blocks) + WALLET_SCRIPT) if blocks else ""


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
    if f.get("requiresLogin"):
        bits.append(f"🔑 {f['requiresLogin']} login")
    if f.get("requiresMainnetBalance"):
        bits.append("💰 needs mainnet balance")
    if f.get("requiresCaptcha"):
        bits.append("captcha")
    if f.get("requiresWallet"):
        bits.append("wallet connect")
    if st.get("uptimePct") is not None:
        bits.append(f"{st['uptimePct']}% uptime")

    cur_slug = slug(f["currency"])
    badge = ""
    oc = st.get("onchain") or {}
    if st.get("verificationTier") == "onchain":
        badge = '<span class="tier onchain" title="Verified by on-chain wallet activity">⛓ on-chain</span>'
    panel = onchain_panel(oc)
    fail = ""
    if st.get("failureLabel"):
        fail = f'<p class="reason">{e(st["failureLabel"])} — {e(st.get("failureAdvice", ""))}</p>'

    return f"""<article class="card {e(s)}">
  <div class="card-top">
    <span class="ticker">{e(f["currency"])}</span>
    <a href="{e(f["url"])}" target="_blank" rel="noopener">{e(f["name"])}</a>
    <span class="network">{e(f["network"])}</span>
    <span class="status-line"><span class="dot {e(s)}"></span>{e(STATUS_LABEL.get(s, s))}{badge}</span>
  </div>
  {panel}
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

    # Cache-bust the homepage's own CSS/JS (idempotent — replaces any prior ?v=).
    shell = re.sub(
        r'(assets/(?:style\.css|app\.js))(\?v=[^"\']*)?',
        rf"\1?v={BUILD_VERSION}", shell,
    )

    # Order by usefulness, not alphabet: best status → verified on-chain →
    # fewest barriers (no captcha/wallet) → currency. Mirrors app.js byRank.
    order = ["up", "degraded", "down", "manual", "unknown"]

    def rank(f):
        st = status_by_id.get(f["id"], {})
        return (
            order.index(status_of(f["id"], status_by_id)),
            0 if st.get("verificationTier") == "onchain" else 1,
            (1 if f.get("requiresCaptcha") else 0) + (1 if f.get("requiresWallet") else 0),
            f["currency"],
        )

    ordered = sorted(faucets, key=rank)
    cards = "\n".join(render_card(f, status_by_id.get(f["id"], {})) for f in ordered)

    start, end = "<!-- FAUCET_LIST:START -->", "<!-- FAUCET_LIST:END -->"
    if start not in shell or end not in shell:
        print("ERROR: markers missing from index.html", file=sys.stderr)
        return False

    pre, rest = shell.split(start, 1)
    _, post = rest.split(end, 1)
    shell = f"{pre}{start}\n{cards}\n{end}{post}"

    # One ListItem per currency, pointing at our own currency pages (internal
    # linking + keeps the schema promoting us, not the external faucets).
    seen_cur, unique_currencies = set(), []
    for f in ordered:
        if f["currency"] not in seen_cur:
            seen_cur.add(f["currency"])
            unique_currencies.append(f["currency"])

    itemlist = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "Blockchain testnet faucets by network",
        "numberOfItems": len(unique_currencies),
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": f"{cur} testnet faucet",
                "url": f"{SITE_URL}/{slug(cur)}-testnet-faucet/",
            }
            for i, cur in enumerate(unique_currencies)
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

        # Explicit lead override — for L2s ("Base Sepolia") where the auto-logic
        # would wrongly pick the shared "Sepolia" suffix over the network brand.
        explicit_lead = next((f["lead"] for f in items if f.get("lead")), None)
        if explicit_lead:
            lead = explicit_lead
            also = "" if currency.lower() in lead.lower() else f" ({currency})"

        headline = (
            f"{working} of {len(items)} working"
            if len(items) > 1
            else STATUS_LABEL.get(statuses[0], statuses[0])
        )
        # Titles/descriptions are framed around GETTING tokens (searcher intent
        # is "get test X now"), with "checked daily" as the trust signal. The
        # earlier "Is It Down?" framing ranked on page 1 but got ~0% CTR because
        # it read as a monitoring tool rather than a place to get tokens.
        n = len(items)
        faucet_phrase = f"{n} {currency} faucets" if n > 1 else f"the {currency} faucet"
        title = f"{lead}{also} Testnet Faucet — Get Test {currency}, Checked Daily | {SITE_NAME}"
        desc = (
            f"Where to get free test {currency} on {', '.join(networks)} — {faucet_phrase}, "
            f"health-checked daily with amounts, cooldowns, and requirements so you know "
            f"which one works right now. Last checked {freshness(generated_at)}."
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
            oc = st.get("onchain") or {}
            oc_panel = onchain_panel(oc)
            if oc_panel:
                detail.append(
                    '<p class="muted oc-explain">Read directly from the faucet\'s '
                    "dispensing wallet, so it holds even when the site is behind bot "
                    "protection.</p>"
                )
            amt_bits = []
            if f.get("amount"):
                amt_bits.append(f"Dispenses {e(f['amount'])}")
            if f.get("cooldown"):
                amt_bits.append(f"cooldown {e(f['cooldown'])}")
            if amt_bits:
                detail.append(f"<p><strong>Amount:</strong> {', '.join(amt_bits)}.</p>")
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
            if f.get("requiresLogin"):
                reqs.append(f"signing in with {e(f['requiresLogin'])}")
            if f.get("requiresMainnetBalance"):
                reqs.append("a pre-existing mainnet balance (empty wallets can't claim)")
            if f.get("requiresCaptcha"):
                reqs.append("solving a captcha")
            if f.get("requiresWallet"):
                reqs.append("connecting a wallet")
            if reqs:
                detail.append(f"<p><strong>Requires:</strong> {', '.join(reqs)}.</p>")

            sections.append(f"""<section class="card {e(s)}">
  <h2>Is the {e(f["name"])} down right now?</h2>
  {oc_panel}
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

        # Comparison table — scannable "best faucet" view, the content that
        # matches "best <X> faucet" intent and beats single-faucet pages.
        def reqs_cell(f):
            r = []
            if f.get("requiresLogin"):
                r.append(f"{e(f['requiresLogin'])} login")
            if f.get("requiresMainnetBalance"):
                r.append("mainnet balance")
            if f.get("requiresWallet"):
                r.append("wallet")
            if f.get("requiresCaptcha"):
                r.append("captcha")
            return ", ".join(r) if r else "none"

        trows = ""
        for f in items:
            st = status_by_id.get(f["id"], {})
            s = st.get("status", "unknown")
            up = st.get("uptimePct")
            oc = st.get("onchain") or {}
            oc_cell = (
                f' <span class="tier onchain">⛓ {e(oc["balanceStr"])} in wallet</span>'
                if oc.get("balanceStr") else ""
            )
            trows += (
                f'<tr><td><a href="{e(f["url"])}" target="_blank" rel="noopener">{e(f["name"])}</a></td>'
                f'<td>{e(f.get("amount") or "—")}</td>'
                f'<td>{e(f.get("cooldown") or "—")}</td>'
                f'<td>{e(reqs_cell(f))}</td>'
                f'<td><span class="dot {e(s)}"></span> {e(STATUS_LABEL.get(s, s))}'
                f'{f" · {up}%" if up is not None else ""}{oc_cell}</td></tr>'
            )
        compare = (
            f'<div class="table-scroll"><table><thead><tr><th>Faucet</th>'
            f'<th>Dispenses</th><th>Cooldown</th><th>Requires</th><th>Status</th></tr></thead>'
            f'<tbody>{trows}</tbody></table></div>'
        )

        # Data-driven intro — unique per page (counts + best pick), so pages
        # aren't near-duplicate boilerplate that Google collapses.
        nc = sum(1 for f in items if f.get("requiresCaptcha"))
        nw = sum(1 for f in items if f.get("requiresWallet"))
        req_bits = []
        if nc:
            req_bits.append(f"{nc} need{'s' if nc == 1 else ''} a captcha")
        if nw:
            req_bits.append(f"{nw} require{'s' if nw == 1 else ''} a wallet connection")
        req_sentence = (
            "Of these, " + " and ".join(req_bits) + "."
            if req_bits else "None of them require a wallet or captcha."
        )
        working = sorted(
            ((f, status_by_id.get(f["id"], {})) for f in items
             if status_of(f["id"], status_by_id) == "up"),
            key=lambda pr: -(pr[1].get("uptimePct") or 0),
        )
        if working:
            bf, bst = working[0]
            up = bst.get("uptimePct")
            best_sentence = (
                f"The most reliable right now is <strong>{e(bf['name'])}</strong>"
                + (f" ({up}% uptime over the last {len(bst.get('history', []))} daily checks)." if up is not None else ".")
            )
        else:
            best_sentence = (
                "Every listed faucet is failing our automated check right now — check the "
                "notes above and try again later, or report a working one on GitHub."
            )
        verb = "is" if len(items) == 1 else "are"
        plural = "" if len(items) == 1 else "s"
        intro = (
            f"There {verb} {len(items)} {e(currency)} testnet faucet{plural} in this list, "
            f"on {e(', '.join(networks))}. {req_sentence} {best_sentence}"
        )

        faq_ld = json.dumps({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": faqs,
        })

        h1 = f"{lead}{also} Testnet Faucet — Get Test {currency}"
        wallet_cfg = wallet_config_section(networks)
        body = f"""<header class="masthead"><div class="wrap">
  <p class="crumb"><a href="../">← All testnet faucets</a></p>
  <h1>{e(h1)}</h1>
  <p class="tagline">Where to get free test {e(currency)} on {e(", ".join(networks))} — {e(faucet_phrase)}, health-checked every day so you know which one works right now.</p>
  <p class="generated">Last checked {e(freshness(generated_at))}.</p>
</div></header>
<main class="wrap">
  {alternatives}
  <p class="intro">{intro}</p>
  {compare}
  {"".join(sections)}
  {wallet_cfg}
  <section>
    <h2>Getting test {e(currency)} on {e(lead)}</h2>
    <p>Testnet faucets are run on a best-effort basis and break constantly: domains
    lapse, TLS certificates expire, rate limits tighten, and faucet wallets run dry.
    We re-check every {e(currency)} faucet daily and record the cause of failure, so
    you can tell a dead faucet from a temporary blip before you waste time on it.</p>
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

    global BUILD_VERSION
    BUILD_VERSION = (re.sub(r"\D", "", generated_at or "")[:14]) or "0"

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
