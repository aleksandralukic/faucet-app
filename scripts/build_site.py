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


# Currency pages that are the SAME page as another one: same network, and in the
# MATIC/POL case the same faucet URL with two claim flows. Two near-identical
# pages split their ranking signals and compete with each other, so the alias
# renders as a canonical stub pointing at the target and its faucets are folded
# into the target page. The old URL is kept (both are indexed) — only the
# duplicate CONTENT goes away.
ALIASES = {
    "MATIC": "POL",
    "XRP/SOLO": "XRP",
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


_NETWORKS_JSON = None


def networks_meta():
    """Lazy-load data/networks.json (network id -> {name, family, lifecycle})."""
    global _NETWORKS_JSON
    if _NETWORKS_JSON is None:
        path = os.path.join(DATA, "networks.json")
        try:
            with open(path, encoding="utf-8") as fh:
                _NETWORKS_JSON = json.load(fh)
        except (OSError, ValueError):
            _NETWORKS_JSON = {}
    return _NETWORKS_JSON


def network_family(net):
    """Group networks that searchers treat as siblings.

    Every Ethereum L2 testnet is a '<brand> Sepolia', and someone who needs Base
    Sepolia ETH very often needs Arbitrum or OP Sepolia ETH too — that shared
    codename is a stronger relatedness signal than networks.json's `family`
    (which would file them all under a generic 'evm'). So Sepolia wins, and
    networks.json supplies the family for everything else."""
    if "sepolia" in net.lower():
        return "sepolia"
    meta = networks_meta().get(slug(net)) or {}
    return meta.get("family")


def related_links(currency, items, net_index, fam_index, all_currencies):
    """Lateral links to sibling currency pages: same network first, then same
    family. Capped at 6 — past that it reads as a link dump rather than a
    genuine 'you probably also need these' block."""
    picks, seen = [], {currency}

    def take(names, why):
        for n in sorted(names):
            if n in seen or n not in all_currencies or len(picks) >= 6:
                continue
            seen.add(n)
            picks.append((n, why))

    for f in items:
        take(net_index.get(f["network"], ()), f["network"])
    for f in items:
        fam = network_family(f["network"])
        if fam:
            take(fam_index.get(fam, ()), "same family")

    if not picks:
        return ""
    lis = "".join(
        f'<li><a href="../{slug(n)}-testnet-faucet/">{e(n)} testnet faucet</a> '
        f'<span class="muted">({e(why)})</span></li>'
        for n, why in picks
    )
    return (
        '<section class="related"><h2>Related testnet faucets</h2>'
        f"<ul>{lis}</ul></section>"
    )


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
    "HyperEVM Testnet": {"chainId": 998, "rpc": "https://rpc.hyperliquid-testnet.xyz/evm", "symbol": "HYPE", "explorer": "https://testnet.purrsec.com", "name": "HyperEVM Testnet"},
    "Sonic Testnet": {"chainId": 14601, "rpc": "https://rpc.testnet.soniclabs.com", "symbol": "S", "explorer": "https://testnet.sonicscan.org", "name": "Sonic Testnet"},
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


def freshness_date(generated_at):
    """Date only, no clock time. Used in <meta description>: the SERP snippet has
    ~155 usable characters and 'at 07:07 UTC' spends nine of them on noise, while
    the date alone carries the whole freshness signal."""
    if not generated_at:
        return "not yet checked"
    try:
        dt = datetime.fromisoformat(generated_at)
    except ValueError:
        return generated_at
    return dt.strftime("%d %B %Y")


def breadcrumb_ld(trail):
    """BreadcrumbList JSON-LD. `trail` is [(name, absolute_url), ...].

    The only schema the site emitted was FAQPage, which Google restricted to
    authoritative health/government sources in 2023 — it renders nothing for a
    site like this, which is why the Search Appearance report is empty.
    Breadcrumbs still render, and they replace the raw URL in the result."""
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": n, "item": u}
            for i, (n, u) in enumerate(trail, 1)
        ],
    })


def ld(*objs):
    """Wrap JSON-LD payloads as <script> tags, skipping empties."""
    return "".join(
        f'<script type="application/ld+json">{o}</script>' for o in objs if o
    )


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
    # On-chain verification supersedes HTTP uptime — showing "10% uptime" next to
    # "dispensed today" reads as a contradiction (the weaker signal winning).
    if st.get("uptimePct") is not None and st.get("verificationTier") != "onchain":
        bits.append(f"{st['uptimePct']}% HTTP uptime")

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
    # Aliased currencies are resolved to their canonical page, so the schema and
    # the internal links it carries never point at a canonicalised stub.
    seen_cur, unique_currencies = set(), []
    for f in ordered:
        cur = ALIASES.get(f["currency"], f["currency"])
        if cur not in seen_cur:
            seen_cur.add(cur)
            unique_currencies.append(cur)

    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebSite",
                "@id": f"{SITE_URL}/#website",
                "url": f"{SITE_URL}/",
                "name": SITE_NAME,
                "description": (
                    "Testnet faucet status for 35+ blockchain networks, "
                    "health-checked daily and verified on-chain."
                ),
                "publisher": {"@id": f"{SITE_URL}/#org"},
            },
            {
                "@type": "Organization",
                "@id": f"{SITE_URL}/#org",
                "name": SITE_NAME,
                "url": f"{SITE_URL}/",
                "logo": f"{SITE_URL}/apple-touch-icon.png",
                "sameAs": ["https://github.com/aleksandralukic/faucet-app"],
            },
            {
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
            },
        ],
    }
    shell = re.sub(
        r'(<script type="application/ld\+json" id="ld-home">).*?(</script>)',
        lambda m: m.group(1) + json.dumps(graph) + m.group(2),
        shell,
        flags=re.S,
    )

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(shell)
    return True


# ------------------------------------------------------- per-currency pages

def build_currency_pages(faucets, status_by_id, generated_at):
    """One page per currency, targeting '<X> testnet faucet' style queries."""
    # Fold aliased currencies into their target so the target page carries every
    # faucet for that network and the alias renders as a stub (see ALIASES).
    groups = {}
    for f in faucets:
        groups.setdefault(ALIASES.get(f["currency"], f["currency"]), []).append(f)

    # Network -> the currency pages that cover it, for lateral "related" links.
    # Before this, a currency page's only internal outlink was the homepage, so
    # 46 pages sat in a flat hub-and-spoke with no topical clustering at all.
    net_index = {}
    fam_index = {}
    for currency, items in groups.items():
        for f in items:
            net_index.setdefault(f["network"], set()).add(currency)
            fam = network_family(f["network"])
            if fam:
                fam_index.setdefault(fam, set()).add(currency)

    written = []
    alias_dirs = []
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
        # No "| Faucet App" suffix: the brand has no search equity, and it spent
        # ~13 of the ~60 characters Google renders before truncating.
        title = f"{lead}{also} Testnet Faucet — Get Test {currency}, Checked Daily"
        desc = (
            f"Where to get free test {currency} on {', '.join(networks)} — {faucet_phrase}, "
            f"health-checked daily with amounts, cooldowns, and requirements so you know "
            f"which one works right now. Last checked {freshness_date(generated_at)}."
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
            if st.get("uptimePct") is not None and st.get("verificationTier") != "onchain":
                detail.append(
                    f'<p><strong>HTTP uptime:</strong> {st["uptimePct"]}% across the last '
                    f'{len(hist)} daily check{"s" if len(hist) != 1 else ""} '
                    f'<span class="muted">(page reachability, not funds)</span>.</p>'
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
                f'{f" · {up}% HTTP" if up is not None and not oc.get("balanceStr") else ""}{oc_cell}</td></tr>'
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
                + (f" ({up}% uptime over the last {len(bst.get('history', []))} "
                   f"daily check{'s' if len(bst.get('history', [])) != 1 else ''})."
                   if up is not None else ".")
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
        # The combined USDC/EURC listing is the landing page for every
        # chain-qualified stablecoin query, so it carries the cross-chain table.
        stable = (
            stablecoin_table(faucets, status_by_id)
            if currency.upper() in ("USDC/EURC", "USDT") else ""
        )
        related = related_links(currency, items, net_index, fam_index, set(groups))
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
  {stable}
  {related}
  <section>
    <h2>Getting test {e(currency)}{"" if lead.lower() == currency.lower() else f" on {e(lead)}"}</h2>
    <p>Testnet faucets are run on a best-effort basis and break constantly: domains
    lapse, TLS certificates expire, rate limits tighten, and faucet wallets run dry.
    We re-check every {e(currency)} faucet daily and record the cause of failure, so
    you can tell a dead faucet from a temporary blip before you waste time on it.</p>
    <p class="muted">Faucets we can verify on-chain show the dispensing wallet's live
    balance and last payout — proof they're funded, not just reachable. For the rest,
    a "working" result means the faucet's page responded; it can't prove the tap has funds.</p>
  </section>
</main>
<footer class="wrap footer"><p><a href="../">{e(SITE_NAME)}</a> — testnet faucet status, checked daily.
<a href="../api/">Developer API</a> · <a href="https://github.com/aleksandralukic/faucet-app">Source on GitHub</a>.</p></footer>"""

        crumbs = breadcrumb_ld([
            ("Testnet faucets", f"{SITE_URL}/"),
            (f"{currency} testnet faucet", f"{SITE_URL}/{dirname}/"),
        ])
        out = page(
            title, desc, f"{SITE_URL}/{dirname}/", body, depth=1,
            extra_head=ld(faq_ld, crumbs),
        )
        with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as fh:
            fh.write(out)
        written.append(dirname)

    # Alias stubs. GitHub Pages can't serve a 301, so the consolidation is done
    # with rel=canonical (tells Google which page owns the ranking) plus a meta
    # refresh and a real link (moves humans who land on the old URL). The stub
    # stays out of the sitemap — a canonicalised page has no business being
    # advertised for indexing.
    for src, dst in sorted(ALIASES.items()):
        if dst not in groups:
            continue
        src_dir = f"{slug(src)}-testnet-faucet"
        dst_dir = f"{slug(dst)}-testnet-faucet"
        target = f"{SITE_URL}/{dst_dir}/"
        outdir = os.path.join(ROOT, src_dir)
        os.makedirs(outdir, exist_ok=True)
        body = (
            '<main class="wrap">\n'
            f"  <h1>{e(src)} testnet faucet</h1>\n"
            f"  <p>{e(src)} and {e(dst)} are the same testnet. This page has moved to the\n"
            f'  <a href="../{dst_dir}/">{e(dst)} testnet faucet page</a>, which lists every\n'
            "  faucet for it with live status.</p>\n"
            "</main>"
        )
        out = page(
            f"{src} Testnet Faucet — see the {dst} faucet page",
            f"{src} and {dst} are the same testnet. See the {dst} testnet faucet "
            f"page for every faucet, with daily health checks.",
            target, body, depth=1,
            extra_head=f'<meta http-equiv="refresh" content="0; url=../{dst_dir}/">',
        )
        with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as fh:
            fh.write(out)
        alias_dirs.append(src_dir)

    return written, alias_dirs


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
<a href="../api/">Developer API</a> · <a href="https://github.com/aleksandralukic/faucet-app">Source on GitHub</a>.</p></footer>"""

    out = page(
        "Testnet Faucets Down Right Now — Live Status",
        f"Which testnet faucets are down right now: {len(broken)} of {len(faucets)} "
        f"tracked faucets are failing, with the cause of each failure. Checked daily.",
        f"{SITE_URL}/down/", body, depth=1,
    )
    with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(out)


# Tokens that are stablecoins for the purposes of the cross-chain table.
STABLE_TOKENS = {"USDC", "EURC", "USDT", "CUSD", "CEUR", "CREAL"}


def stablecoin_table(faucets, status_by_id):
    """Cross-chain 'which testnet gives me test USDC' table.

    Search Console shows the stablecoin demand is chain-qualified — 'usdc faucet
    sei', 'fuji usdc', 'linea testnet usdc faucet', 'usdc faucet base' — but all
    of it landed on one page with no per-chain breakdown. This lists every
    faucet we track that dispenses a stablecoin, by network, with an anchor per
    network so a chain-qualified query has something specific to match.
    """
    rows = []
    for f in faucets:
        toks = [f["currency"]] if f["currency"].upper() in STABLE_TOKENS else []
        toks += [t for t in (f.get("dripsAlso") or [])
                 if t.upper() in STABLE_TOKENS]
        # "USDC/EURC" is a combined listing, not a ticker.
        if "/" in f["currency"] and not toks:
            parts = [p for p in f["currency"].split("/") if p.upper() in STABLE_TOKENS]
            toks = parts + toks
        if not toks:
            continue
        seen, uniq = set(), []
        for t in toks:
            if t.upper() not in seen:
                seen.add(t.upper())
                uniq.append(t)
        rows.append((f, uniq))

    if not rows:
        return ""

    trs = ""
    for f, toks in sorted(rows, key=lambda r: r[0]["network"]):
        st = status_by_id.get(f["id"], {})
        s = st.get("status", "unknown")
        cur = ALIASES.get(f["currency"], f["currency"])
        net_link = (
            f'<a href="../networks/{slug(f["network"])}/">{e(f["network"])}</a>'
            if not f["network"].lower().startswith("multi-chain")
            else e(f["network"])
        )
        trs += (
            f'<tr id="{slug(f["network"])}">'
            f"<td>{net_link}</td>"
            f'<td>{", ".join(f"<strong>{e(t)}</strong>" for t in toks)}</td>'
            f'<td><a href="{e(f["url"])}" target="_blank" rel="noopener">{e(f["name"])}</a></td>'
            f'<td>{e(f.get("amount") or "—")}</td>'
            f'<td><span class="dot {e(s)}"></span> {e(STATUS_LABEL.get(s, s))}</td>'
            f'<td><a href="../{slug(cur)}-testnet-faucet/">{e(cur)} page →</a></td>'
            "</tr>"
        )

    return f"""<section>
  <h2>Test stablecoins by network</h2>
  <p>Which testnet gives you which stablecoin, and from where. Every row is
  health-checked daily along with the rest of the site.</p>
  <div class="table-scroll"><table>
    <thead><tr><th>Network</th><th>Tokens</th><th>Faucet</th><th>Amount</th>
    <th>Status</th><th>Details</th></tr></thead>
    <tbody>{trs}</tbody>
  </table></div>
  <p class="muted">Chasing a specific chain? Its network page lists the chain ID,
  RPC URL and explorer you'll need alongside the faucet.</p>
</section>"""


# ------------------------------------------------- /faucet-errors/ pages

def build_error_pages(faucets, status_by_id, generated_at):
    """Pages for the error messages faucets themselves show.

    Search Console shows people pasting these verbatim ("cannot claim drip
    because user does not exist on mainnet", "faucet is not available") and
    finding nothing that explains them. It is the one query cluster on this site
    that already ranks well without any links, because effectively nobody
    competes for it. Content comes from data/faucet_errors.json.
    """
    path = os.path.join(DATA, "faucet_errors.json")
    try:
        with open(path, encoding="utf-8") as fh:
            errors = json.load(fh)
    except (OSError, ValueError):
        return []

    by_currency = {}
    for f in faucets:
        by_currency.setdefault(ALIASES.get(f["currency"], f["currency"]), []).append(f)

    def token_links(codes, depth_prefix):
        live = [c for c in codes if c in by_currency]
        if not live:
            return ""
        return ", ".join(
            f'<a href="{depth_prefix}{slug(c)}-testnet-faucet/">{e(c)}</a>' for c in live
        )

    os.makedirs(os.path.join(ROOT, "faucet-errors"), exist_ok=True)
    hub_rows = []
    written = []
    for eslug, spec in errors.items():
        outdir = os.path.join(ROOT, "faucet-errors", eslug)
        os.makedirs(outdir, exist_ok=True)

        quotes = "".join(
            f'<li><q>{e(m)}</q></li>' for m in spec.get("messages", [])
        )
        fixes = "".join(f"<li>{fx}</li>" for fx in spec.get("fixes", []))

        affected = token_links(spec.get("affected", []), "../../")
        see_also = token_links(spec.get("seeAlso", []), "../../")
        rel = ""
        if affected:
            rel += (
                f'<p><strong>Faucets we track that behave this way:</strong> {affected}.</p>'
            )
        if see_also:
            rel += f'<p><strong>Related tokens:</strong> {see_also}.</p>'

        others = "".join(
            f'<li><a href="../{s2}/">{e(sp2["shortName"])}</a></li>'
            for s2, sp2 in errors.items() if s2 != eslug
        )

        body = f"""<header class="masthead"><div class="wrap">
  <p class="crumb"><a href="../../">← All testnet faucets</a> ·
     <a href="../">Faucet errors</a></p>
  <h1>{e(spec["h1"])}</h1>
  <p class="tagline">What this error means, why the faucet returns it, and the
  fastest way to get your test tokens anyway.</p>
  <p class="generated">Reviewed {e(freshness_date(generated_at))}.</p>
</div></header>
<main class="wrap">
  <section>
    <h2>The error</h2>
    <p>Faucets word this differently, but these are the same problem:</p>
    <ul class="quotes">{quotes}</ul>
  </section>
  <section>
    <h2>Why it happens</h2>
    <p>{spec["cause"]}</p>
  </section>
  <section>
    <h2>How to fix it</h2>
    <ol>{fixes}</ol>
    {rel}
  </section>
  <section class="related">
    <h2>Other faucet errors</h2>
    <ul>{others}</ul>
  </section>
</main>
<footer class="wrap footer"><p><a href="../../">{e(SITE_NAME)}</a> — testnet faucet status, checked daily.
<a href="../../api/">Developer API</a> · <a href="https://github.com/aleksandralukic/faucet-app">Source on GitHub</a>.</p></footer>"""

        faq_ld = json.dumps({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [{
                "@type": "Question",
                "name": spec["messages"][0] if spec.get("messages") else spec["shortName"],
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": re.sub(r"<[^>]+>", "", spec["cause"]),
                },
            }],
        })
        crumbs = breadcrumb_ld([
            ("Testnet faucets", f"{SITE_URL}/"),
            ("Faucet errors", f"{SITE_URL}/faucet-errors/"),
            (spec["shortName"], f"{SITE_URL}/faucet-errors/{eslug}/"),
        ])
        out = page(
            spec["title"],
            re.sub(r"<[^>]+>", "", spec["cause"])[:150].rsplit(" ", 1)[0] + "…",
            f"{SITE_URL}/faucet-errors/{eslug}/", body, depth=2,
            extra_head=ld(faq_ld, crumbs),
        )
        with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as fh:
            fh.write(out)
        written.append(eslug)
        hub_rows.append(
            f'<li><a href="{eslug}/"><strong>{e(spec["shortName"])}</strong></a>'
            f' — <q>{e(spec["messages"][0])}</q></li>'
            if spec.get("messages") else
            f'<li><a href="{eslug}/"><strong>{e(spec["shortName"])}</strong></a></li>'
        )

    hub_body = f"""<header class="masthead"><div class="wrap">
  <p class="crumb"><a href="../">← All testnet faucets</a></p>
  <h1>Testnet Faucet Errors — What They Mean</h1>
  <p class="tagline">The error messages testnet faucets actually return, what
  causes each one, and how to get your tokens anyway.</p>
  <p class="generated">Reviewed {e(freshness_date(generated_at))}.</p>
</div></header>
<main class="wrap">
  <p class="intro">Most testnet faucet failures are not bugs on your side. They are
  anti-abuse gates, cooldowns, or a dispensing wallet that has run dry — each with
  a different fix. These are the ones we see most often.</p>
  <ul class="errorlist">{"".join(hub_rows)}</ul>
  <section>
    <h2>Is the faucet itself down?</h2>
    <p>If the page will not load at all, that is a different problem — see
    <a href="../down/">testnet faucets currently down</a> for live status and the
    recorded cause of each failure.</p>
  </section>
</main>
<footer class="wrap footer"><p><a href="../">{e(SITE_NAME)}</a> — testnet faucet status, checked daily.
<a href="../api/">Developer API</a> · <a href="https://github.com/aleksandralukic/faucet-app">Source on GitHub</a>.</p></footer>"""

    hub_crumbs = breadcrumb_ld([
        ("Testnet faucets", f"{SITE_URL}/"),
        ("Faucet errors", f"{SITE_URL}/faucet-errors/"),
    ])
    with open(os.path.join(ROOT, "faucet-errors", "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page(
            "Testnet Faucet Errors — What Each Message Means",
            "Testnet faucet not working? What each faucet error message means — "
            "mainnet balance required, already claimed, captcha failed, faucet "
            "empty — and how to get your test tokens anyway.",
            f"{SITE_URL}/faucet-errors/", hub_body, depth=1,
            extra_head=ld(hub_crumbs),
        ))

    return written


# ----------------------------------------------------- /networks/ pages

def build_network_pages(faucets, status_by_id, generated_at):
    """One page per testnet with its connection parameters.

    Search Console shows network-configuration intent the faucet pages don't
    serve — 'polygon rpc url', 'amoy testnet scan', 'fuji c-chain', 'cardano
    network status'. That is a far less contested query set than '<token>
    faucet', and the data (chain id, RPC, explorer, lifecycle) is already here.
    """
    meta = networks_meta()
    by_net = {}
    for f in faucets:
        # Circle's faucet is listed against a pseudo-network ("Multi-chain …"),
        # which has no chain id or RPC of its own — nothing to document.
        if f["network"].lower().startswith("multi-chain"):
            continue
        by_net.setdefault(f["network"], []).append(f)

    os.makedirs(os.path.join(ROOT, "networks"), exist_ok=True)
    written, hub_rows = [], []
    for net, items in sorted(by_net.items()):
        nslug = slug(net)
        outdir = os.path.join(ROOT, "networks", nslug)
        os.makedirs(outdir, exist_ok=True)

        evm = NETWORKS.get(net) or {}
        nmeta = meta.get(nslug) or {}
        life = nmeta.get("lifecycle") or {}

        rows = []
        if evm.get("chainId"):
            rows.append(("Chain ID", f'<code>{evm["chainId"]}</code>'))
            rows.append(("Currency symbol", e(evm.get("symbol", ""))))
            rows.append(("RPC URL", f'<code>{e(evm["rpc"])}</code>'))
        if evm.get("explorer"):
            rows.append((
                "Block explorer",
                f'<a href="{e(evm["explorer"])}" target="_blank" rel="noopener">'
                f'{e(evm["explorer"].replace("https://", ""))}</a>',
            ))
        if nmeta.get("family"):
            rows.append(("Family", e(nmeta["family"])))
        if life.get("status"):
            rows.append(("Lifecycle", e(life["status"])))
        if life.get("sunset_date"):
            rows.append(("Sunset date", e(life["sunset_date"])))
        params = (
            '<div class="table-scroll"><table><tbody>'
            + "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
            + "</tbody></table></div>"
        ) if rows else ""

        frows = "".join(
            f'<tr><td><strong>{e(f["currency"])}</strong></td>'
            f'<td><a href="{e(f["url"])}" target="_blank" rel="noopener">{e(f["name"])}</a></td>'
            f'<td><span class="dot {e(status_of(f["id"], status_by_id))}"></span> '
            f'{e(STATUS_LABEL.get(status_of(f["id"], status_by_id), "?"))}</td>'
            f'<td><a href="../../{slug(ALIASES.get(f["currency"], f["currency"]))}-testnet-faucet/">'
            f'{e(ALIASES.get(f["currency"], f["currency"]))} faucet page →</a></td></tr>'
            for f in items
        )
        faucet_table = (
            '<div class="table-scroll"><table><thead><tr><th>Token</th><th>Faucet</th>'
            f'<th>Status</th><th>Details</th></tr></thead><tbody>{frows}</tbody></table></div>'
        )

        sunset_note = ""
        if life.get("status") and life["status"] != "active":
            succ = life.get("successor_network_id")
            succ_txt = ""
            if succ and succ in meta:
                succ_txt = (
                    f' Its replacement is <a href="../{e(succ)}/">'
                    f'{e(meta[succ].get("name", succ))}</a>.'
                )
            sunset_note = (
                f'<p class="callout"><strong>Heads up:</strong> this testnet is marked '
                f'<em>{e(life["status"])}</em>'
                + (f" (sunset {e(life['sunset_date'])})" if life.get("sunset_date") else "")
                + f".{succ_txt}</p>"
            )

        wallet_cfg = wallet_config_section([net])
        title = f"{net} — Chain ID, RPC URL and Faucets"
        desc = (
            f"{net} connection details: "
            + (f"chain ID {evm['chainId']}, RPC endpoint, block explorer, " if evm.get("chainId") else "")
            + f"and the {len(items)} working faucet{'s' if len(items) != 1 else ''} "
            f"we health-check daily. Last checked {freshness_date(generated_at)}."
        )

        body = f"""<header class="masthead"><div class="wrap">
  <p class="crumb"><a href="../../">← All testnet faucets</a> ·
     <a href="../">Networks</a></p>
  <h1>{e(net)}</h1>
  <p class="tagline">Connection parameters and working faucets for {e(net)},
  health-checked every day.</p>
  <p class="generated">Last checked {e(freshness_date(generated_at))}.</p>
</div></header>
<main class="wrap">
  {sunset_note}
  <section>
    <h2>Network parameters</h2>
    {params or "<p>No published RPC parameters for this network.</p>"}
  </section>
  {wallet_cfg}
  <section>
    <h2>Faucets on {e(net)}</h2>
    {faucet_table}
  </section>
  <section class="related">
    <h2>More</h2>
    <ul>
      <li><a href="../">All testnet networks</a></li>
      <li><a href="../../faucet-errors/">Common faucet errors and fixes</a></li>
      <li><a href="../../down/">Faucets currently down</a></li>
    </ul>
  </section>
</main>
<footer class="wrap footer"><p><a href="../../">{e(SITE_NAME)}</a> — testnet faucet status, checked daily.
<a href="../../api/">Developer API</a> · <a href="https://github.com/aleksandralukic/faucet-app">Source on GitHub</a>.</p></footer>"""

        crumbs = breadcrumb_ld([
            ("Testnet faucets", f"{SITE_URL}/"),
            ("Networks", f"{SITE_URL}/networks/"),
            (net, f"{SITE_URL}/networks/{nslug}/"),
        ])
        with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as fh:
            fh.write(page(title, desc, f"{SITE_URL}/networks/{nslug}/", body,
                          depth=2, extra_head=ld(crumbs)))
        written.append(nslug)
        hub_rows.append(
            f'<tr><td><a href="{nslug}/">{e(net)}</a></td>'
            f'<td>{e(str(evm.get("chainId", "—")))}</td>'
            f'<td>{e(nmeta.get("family", "—"))}</td>'
            f'<td>{len(items)}</td></tr>'
        )

    hub_body = f"""<header class="masthead"><div class="wrap">
  <p class="crumb"><a href="../">← All testnet faucets</a></p>
  <h1>Testnet Networks — Chain IDs, RPC URLs and Explorers</h1>
  <p class="tagline">Connection parameters for every testnet we track, alongside
  the faucets that fund them.</p>
  <p class="generated">Last checked {e(freshness_date(generated_at))}.</p>
</div></header>
<main class="wrap">
  <p class="intro">Getting test tokens is half the job — you also need the chain to
  be configured correctly. Each network page lists its chain ID, a public RPC
  endpoint, its block explorer, and whether the testnet is still active or has
  been superseded.</p>
  <div class="table-scroll"><table>
    <thead><tr><th>Network</th><th>Chain ID</th><th>Family</th><th>Faucets</th></tr></thead>
    <tbody>{"".join(hub_rows)}</tbody>
  </table></div>
</main>
<footer class="wrap footer"><p><a href="../">{e(SITE_NAME)}</a> — testnet faucet status, checked daily.
<a href="../api/">Developer API</a> · <a href="https://github.com/aleksandralukic/faucet-app">Source on GitHub</a>.</p></footer>"""

    hub_crumbs = breadcrumb_ld([
        ("Testnet faucets", f"{SITE_URL}/"),
        ("Networks", f"{SITE_URL}/networks/"),
    ])
    with open(os.path.join(ROOT, "networks", "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page(
            "Testnet Networks — Chain IDs, RPC URLs and Faucets",
            "Chain ID, public RPC URL, block explorer and working faucets for every "
            "testnet we track — Sepolia, Amoy, Fuji, Base Sepolia and more. "
            "Health-checked daily.",
            f"{SITE_URL}/networks/", hub_body, depth=1, extra_head=ld(hub_crumbs),
        ))

    return written


# ------------------------------------------------------------ /api/ page

def build_api_page(faucets, generated_at):
    """Human-readable docs at /api/.

    The endpoints already existed but /api/ itself returned 404, so the one
    genuinely linkable thing here — a free, keyless testnet faucet dataset with
    on-chain evidence — had no page anyone could link to or find. Docs pages
    attract links; raw JSON files do not.
    """
    outdir = os.path.join(ROOT, "api")
    os.makedirs(outdir, exist_ok=True)

    eps = [
        ("v1/all.json", "Everything — every network with its faucets."),
        ("v1/faucets.json", "The faucet array on its own."),
        ("v1/networks.json", "The network array on its own."),
        ("v1/networks/&lt;network-id&gt;.json",
         "A single network and its faucets, e.g. <code>ethereum-sepolia</code>."),
    ]
    rows = "".join(
        f'<tr><td><code>/api/{u}</code></td><td>{d}</td></tr>' for u, d in eps
    )

    body = f"""<header class="masthead"><div class="wrap">
  <p class="crumb"><a href="../">← All testnet faucets</a></p>
  <h1>Testnet Faucet API</h1>
  <p class="tagline">Free JSON API for testnet faucet status across 35+ networks —
  no key, no rate limit, no sign-up. Updated on every daily check.</p>
  <p class="generated">Last generated {e(freshness_date(generated_at))}.</p>
</div></header>
<main class="wrap">
  <p class="intro">Static JSON served straight from the CDN. It covers
  {len(faucets)} faucets, and for those we can verify on-chain it carries the
  dispensing wallet's balance and last payout — so you can tell a funded faucet
  from one that merely returns HTTP 200.</p>

  <section>
    <h2>Endpoints</h2>
    <div class="table-scroll"><table>
      <thead><tr><th>URL</th><th>Contents</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>
    <p class="muted">Query-string filtering does not work on static hosting —
    filter client-side (the payload is small) or fetch the per-network file.
    A <code>network-id</code> is the network name slugified.</p>
  </section>

  <section>
    <h2>Freshness contract</h2>
    <p>Every response is wrapped in an envelope carrying
    <code>schema_version</code>, <code>generated_at</code> and
    <code>next_update_expected_at</code>. If
    <code>next_update_expected_at</code> is in the past, our job has stopped and
    the data should be treated as unreliable — you can assert on that without
    knowing our schedule.</p>
  </section>

  <section>
    <h2>Example</h2>
    <pre><code>curl -s https://testnetfaucets.dev/api/v1/networks/ethereum-sepolia.json \\
  | jq '.faucets[] | select(.status=="working") | {{name, url, amount}}'</code></pre>
  </section>

  <section>
    <h2>Terms</h2>
    <p>Public domain data, no attribution required — though a link back is
    appreciated if it's useful to you. Full field reference is in
    <a href="https://github.com/aleksandralukic/faucet-app/blob/main/API.md">API.md</a>
    on GitHub.</p>
  </section>

  <section class="related">
    <h2>More</h2>
    <ul>
      <li><a href="../networks/">Testnet networks and chain IDs</a></li>
      <li><a href="../faucet-errors/">Common faucet errors</a></li>
      <li><a href="../down/">Faucets currently down</a></li>
    </ul>
  </section>
</main>
<footer class="wrap footer"><p><a href="../">{e(SITE_NAME)}</a> — testnet faucet status, checked daily.
<a href="https://github.com/aleksandralukic/faucet-app">Source on GitHub</a>.</p></footer>"""

    crumbs = breadcrumb_ld([
        ("Testnet faucets", f"{SITE_URL}/"),
        ("API", f"{SITE_URL}/api/"),
    ])
    api_ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "Testnet faucet status and on-chain evidence",
        "description": (
            "Daily health checks for public blockchain testnet faucets across 35+ "
            "networks, including dispensing-wallet balances verified on-chain."
        ),
        "url": f"{SITE_URL}/api/",
        "license": "https://creativecommons.org/publicdomain/zero/1.0/",
        "isAccessibleForFree": True,
        "creator": {"@id": f"{SITE_URL}/#org"},
        "distribution": [{
            "@type": "DataDownload",
            "encodingFormat": "application/json",
            "contentUrl": f"{SITE_URL}/api/v1/all.json",
        }],
    })
    with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page(
            "Testnet Faucet API — Free JSON, No Key Required",
            f"Free JSON API for testnet faucet status across 35+ networks. "
            f"{len(faucets)} faucets, health-checked daily, with on-chain wallet "
            f"balances. No key, no rate limit.",
            f"{SITE_URL}/api/", body, depth=1, extra_head=ld(api_ld, crumbs),
        ))


# --------------------------------------------------------- sitemap + robots

def build_sitemap(dirs, error_dirs, network_dirs, generated_at):
    today = (generated_at or datetime.now(timezone.utc).isoformat())[:10]
    # Alias stubs are deliberately absent: they canonicalise elsewhere.
    urls = (
        [f"{SITE_URL}/", f"{SITE_URL}/down/", f"{SITE_URL}/faucet-errors/",
         f"{SITE_URL}/networks/", f"{SITE_URL}/api/"]
        + [f"{SITE_URL}/{d}/" for d in dirs]
        + [f"{SITE_URL}/faucet-errors/{d}/" for d in error_dirs]
        + [f"{SITE_URL}/networks/{d}/" for d in network_dirs]
    )
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


def clean_stale(current_dirs, error_dirs=(), network_dirs=()):
    """Remove pages whose source rows no longer exist in the data files."""
    keep = set(current_dirs) | {"down"}
    removed = []
    for parent, live in (("faucet-errors", error_dirs), ("networks", network_dirs)):
        base = os.path.join(ROOT, parent)
        if not os.path.isdir(base):
            continue
        for name in os.listdir(base):
            full = os.path.join(base, name)
            if os.path.isdir(full) and name not in set(live):
                shutil.rmtree(full)
                removed.append(f"{parent}/{name}")
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

    dirs, alias_dirs = build_currency_pages(faucets, status_by_id, generated_at)
    build_down_page(faucets, status_by_id, generated_at)
    error_dirs = build_error_pages(faucets, status_by_id, generated_at)
    network_dirs = build_network_pages(faucets, status_by_id, generated_at)
    build_api_page(faucets, generated_at)
    removed = clean_stale(dirs + alias_dirs, error_dirs, network_dirs)
    n = build_sitemap(dirs, error_dirs, network_dirs, generated_at)

    print(f"Homepage rendered with {len(faucets)} faucets")
    print(f"Currency pages: {len(dirs)}  (+{len(alias_dirs)} canonical stubs)")
    print(f"Error pages:    {len(error_dirs)}")
    print(f"Network pages:  {len(network_dirs)}")
    print(f"Sitemap URLs:   {n}")
    if removed:
        print(f"Removed stale:  {', '.join(removed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
