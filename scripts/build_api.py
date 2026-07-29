#!/usr/bin/env python3
"""
Generate the public JSON API from faucets.json + status.json.

Static files served from the CDN — no server, no rate limits. Query-param
filtering can't work on static hosting, so we ship the whole payload (tiny) plus
one file per network, and consumers filter client-side or fetch the network file.

  /api/v1/all.json                    everything
  /api/v1/faucets.json                the faucet array
  /api/v1/networks/<network-id>.json  one network's faucets

This is the DATA LAYER (pure serialisation of what we already measure). The
network `lifecycle`/`liveness` objects and the preflight CLI build on top of it.

Stdlib only. Run after check_faucets.py + build_site.py.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_DIR = os.path.join(ROOT, "api", "v1")
SCHEMA_VERSION = "1.0"
UPDATE_INTERVAL_HOURS = 24  # the daily check cadence

# status.json vocabulary -> API vocabulary (public-facing).
STATUS_MAP = {"up": "working", "degraded": "degraded", "down": "down",
              "manual": "manual", "unknown": "unknown"}


def network_id(network_name):
    return re.sub(r"[^a-z0-9]+", "-", network_name.lower()).strip("-")


def friction_of(f):
    fr = []
    if f.get("requiresLogin"):
        fr.append(f"login:{f['requiresLogin']}")
    if f.get("requiresMainnetBalance"):
        fr.append("mainnet-balance")
    if f.get("requiresCaptcha"):
        fr.append("captcha")
    if f.get("requiresWallet"):
        fr.append("wallet")
    return fr


def last_dispensed_at(oc, now_dt):
    days = oc.get("lastDispenseDays")
    if days is None:
        return None
    # Approximate from "N days ago" relative to this run — good to the day.
    return (now_dt - timedelta(days=days)).replace(microsecond=0).isoformat()


def faucet_object(f, st, now_dt):
    oc = st.get("onchain") or {}
    evidence = {
        "http_status": st.get("httpStatus"),
        "wallet_balance": str(oc["balance"]) if oc.get("balance") is not None else None,
        "wallet_balance_unit": oc.get("symbol"),
        "payouts_sent": oc.get("payoutsSent"),
        "last_dispensed_at": last_dispensed_at(oc, now_dt),
    }
    return {
        "id": f["id"],
        "network_id": network_id(f["network"]),
        "name": f["name"],
        "url": f["url"],
        "currency": f["currency"],
        "status": STATUS_MAP.get(st.get("status", "unknown"), "unknown"),
        "verification": st.get("verificationTier", "http"),
        "checked_at": st.get("checkedAt"),
        "amount": f.get("amount"),
        "cooldown": f.get("cooldown"),
        "friction": friction_of(f),
        "automatable": bool(f.get("automatable", False)),
        "claim_api": f.get("claim_api"),
        "notes": f.get("notes"),
        "evidence": evidence,
    }


def write(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def main():
    with open(os.path.join(ROOT, "data", "faucets.json"), encoding="utf-8") as fh:
        faucets = json.load(fh)
    with open(os.path.join(ROOT, "data", "status.json"), encoding="utf-8") as fh:
        status = json.load(fh)
    by_id = {r["id"]: r for r in status.get("results", [])}

    generated_at = status.get("generatedAt") or datetime.now(timezone.utc).isoformat()
    now_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    next_update = (now_dt + timedelta(hours=UPDATE_INTERVAL_HOURS)).replace(microsecond=0).isoformat()

    objs = [faucet_object(f, by_id.get(f["id"], {}), now_dt) for f in faucets]

    def envelope(**extra):
        base = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "next_update_expected_at": next_update,
        }
        base.update(extra)
        return base

    write(os.path.join(API_DIR, "faucets.json"), envelope(faucets=objs))
    write(os.path.join(API_DIR, "all.json"), envelope(faucets=objs))

    # One file per network so consumers can fetch a single network on static
    # hosting (no query-param filtering possible).
    by_net = {}
    for o in objs:
        by_net.setdefault(o["network_id"], []).append(o)
    for nid, group in by_net.items():
        write(os.path.join(API_DIR, "networks", f"{nid}.json"), envelope(network_id=nid, faucets=group))

    print(f"API: {len(objs)} faucets, {len(by_net)} networks -> /api/v1/")
    print(f"  automatable: {sum(1 for o in objs if o['automatable'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
