#!/usr/bin/env python3
"""
Verify that the LIVE site is telling the truth.

This is the trust backstop. It fetches what testnetfaucets.dev is actually
serving and independently re-derives the claims, failing loudly if any of them
don't hold up:

  1. Freshness   — the data isn't stale while the UI implies it's current.
  2. On-chain    — each "verified on-chain" balance/payout claim still matches
                   what the chain says right now (re-queried live).
  3. Consistency — the summary/tier counts and per-faucet records are coherent.
  4. Status      — a sample of "working" faucets still respond (soft check).

Exit code 0 = all good; non-zero = a hard failure (CI turns this into an alert).
Stdlib only, plus the local onchain module.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import onchain

SITE = os.environ.get("VERIFY_SITE", "https://testnetfaucets.dev").rstrip("/")
MAX_AGE_HOURS = float(os.environ.get("VERIFY_MAX_AGE_HOURS", "36"))
STATUS_SAMPLE = 6  # how many "up" HTTP faucets to re-probe (soft)

HEADERS = {"User-Agent": "testnetfaucets.dev-verify/1.0"}
failures, warnings, notes = [], [], []


def fetch_json(path):
    req = urllib.request.Request(SITE + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def http_ok(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.getcode()
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def check_freshness(status):
    gen = status.get("generatedAt")
    if not gen:
        failures.append("status.json has no generatedAt")
        return
    dt = datetime.fromisoformat(gen.replace("Z", "+00:00"))
    age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    notes.append(f"data age: {age_h:.1f}h (limit {MAX_AGE_HOURS}h)")
    if age_h > MAX_AGE_HOURS:
        failures.append(
            f"STALE DATA: last check was {age_h:.1f}h ago — the daily job may have "
            f"stopped while the site still serves this data."
        )


def check_consistency(status):
    results = status.get("results", [])
    total = status.get("totalFaucets")
    if total != len(results):
        failures.append(f"totalFaucets {total} != {len(results)} results")
    if sum(status.get("summary", {}).values()) != len(results):
        failures.append("summary counts don't sum to the number of faucets")
    if sum(status.get("tiers", {}).values()) != len(results):
        failures.append("tier counts don't sum to the number of faucets")
    for r in results:
        for field in ("id", "status", "verificationTier"):
            if field not in r:
                failures.append(f"result {r.get('id', '?')} missing '{field}'")


def check_onchain(status, faucets):
    """Re-query each on-chain wallet live and confirm the served claim holds."""
    by_id = {r["id"]: r for r in status.get("results", [])}
    checked = 0
    for f in faucets:
        cfg = f.get("onchain")
        if not cfg:
            continue
        served = (by_id.get(f["id"]) or {}).get("onchain") or {}
        if not served.get("balanceStr"):
            continue
        live = onchain.check(cfg)
        if live.get("error"):
            warnings.append(f"{f['id']}: couldn't re-query on-chain ({live['error']})")
            continue
        checked += 1

        # Payout count only ever grows — a served value ABOVE live is impossible
        # (fabricated or from a since-reset chain). Small negative slack for races.
        sv, lv = served.get("payoutsSent"), live.get("payoutsSent")
        if sv is not None and lv is not None and sv > lv + 5:
            failures.append(
                f"{f['id']}: served payouts {sv:,} exceeds live {lv:,} — data can't be real"
            )

        # Balance drifts as the faucet dispenses, but a >10x gap either way means
        # the served number is stale or wrong, not just moved.
        sb, lb = served.get("balance"), live.get("balance")
        if sb and lb:
            ratio = sb / lb
            if not (0.1 <= ratio <= 10):
                failures.append(
                    f"{f['id']}: served balance {sb:g} vs live {lb:g} "
                    f"({ratio:.2g}x off) — likely stale"
                )

        # If we present it as verified/up but the wallet is now empty+idle, warn.
        if by_id[f["id"]].get("status") == "up" and not live.get("ok"):
            warnings.append(
                f"{f['id']}: shown 'up' on-chain but live check now says not-ok "
                f"({live.get('evidence')})"
            )
    notes.append(f"on-chain claims re-verified: {checked}")


def check_status_sample(status, faucets):
    """Soft: re-probe a few 'working' HTTP faucets. Faucets are flaky, so this
    only warns — it never fails the build on its own."""
    url_by_id = {f["id"]: f["url"] for f in faucets}
    up_http = [
        r for r in status.get("results", [])
        if r.get("status") == "up" and r.get("verificationTier") == "http"
    ][:STATUS_SAMPLE]
    bad = 0
    for r in up_http:
        code = http_ok(url_by_id.get(r["id"], ""))
        if code is None or code >= 500 or code == 404:
            warnings.append(f"{r['id']}: shown 'up' but re-probe returned {code}")
            bad += 1
    notes.append(f"status re-probed: {len(up_http)} sampled, {bad} suspect")


def main():
    try:
        status = fetch_json("/data/status.json")
        faucets = fetch_json("/data/faucets.json")
    except Exception as e:
        print(f"FATAL: couldn't fetch site data: {e}")
        return 2

    check_freshness(status)
    check_consistency(status)
    check_onchain(status, faucets)
    check_status_sample(status, faucets)

    print(f"\n=== verify {SITE} ===")
    for n in notes:
        print(f"  · {n}")
    for w in warnings:
        print(f"  ⚠ {w}")
    if failures:
        print("\nFAILURES:")
        for x in failures:
            print(f"  ✗ {x}")
        print(f"\n{len(failures)} hard failure(s), {len(warnings)} warning(s).")
        return 1
    print(f"\nOK — no hard failures ({len(warnings)} warning(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
