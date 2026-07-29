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
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

# A block older than this means the chain is (probably) halted, not just slow.
# Generous enough for any testnet's block time; a genuine halt blows past it.
CHAIN_STALL_SECONDS = 600

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


def evm_liveness(rpc, now_ts):
    """The assertion that matters: is the chain advancing? A halted chain answers
    RPC perfectly, so we check the latest block's age, not just reachability."""
    # Default is "couldn't check" (null), NOT "down" (false) — a client/network
    # failure on our side must not be published as the chain being unreachable.
    # chain_advancing is only ever true/false when we actually read a block:
    #   readable + fresh -> true, readable + stale -> false (halted), else null.
    out = {"rpc_responding": None, "chain_advancing": None,
           "latest_block": None, "block_age_seconds": None}
    try:
        req = urllib.request.Request(
            rpc,
            data=json.dumps({"jsonrpc": "2.0", "method": "eth_getBlockByNumber",
                             "params": ["latest", False], "id": 1}).encode(),
            # Cloudflare-fronted RPCs (publicnode, base.org…) 403 the default
            # "Python-urllib" User-Agent, so set a real one.
            headers={"Content-Type": "application/json",
                     "User-Agent": "testnetfaucets.dev-liveness/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            block = json.loads(r.read().decode())["result"]
        num = int(block["number"], 16)
        age = max(0, now_ts - int(block["timestamp"], 16))
        out.update(rpc_responding=True, latest_block=num, block_age_seconds=age,
                   chain_advancing=age < CHAIN_STALL_SECONDS)
    except Exception:
        pass
    return out


def network_object(nid, meta, faucet_ids, now_iso, liveness):
    return {
        "id": nid,
        "chain_id": meta.get("chain_id"),
        "name": meta.get("name", nid),
        "family": meta.get("family", "other"),
        "rpc": meta.get("rpc"),  # public; lets consumers query the chain themselves
        "lifecycle": meta.get("lifecycle"),
        "liveness": {"checked_at": now_iso, **liveness},
        "faucet_ids": faucet_ids,
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

    # Group faucets by network.
    by_net = {}
    for o in objs:
        by_net.setdefault(o["network_id"], []).append(o)

    # Network registry + liveness (EVM checked live in parallel; others null).
    registry = {}
    reg_path = os.path.join(ROOT, "data", "networks.json")
    if os.path.exists(reg_path):
        with open(reg_path, encoding="utf-8") as fh:
            registry = json.load(fh)
    now_ts = int(now_dt.timestamp())
    null_live = {"rpc_responding": None, "chain_advancing": None,
                 "latest_block": None, "block_age_seconds": None}

    def live_for(nid):
        meta = registry.get(nid, {})
        if meta.get("family") == "evm" and meta.get("rpc"):
            return nid, evm_liveness(meta["rpc"], now_ts)
        return nid, dict(null_live)

    with ThreadPoolExecutor(max_workers=8) as pool:
        liveness = dict(pool.map(live_for, by_net.keys()))

    networks = []
    for nid in sorted(by_net):
        meta = registry.get(nid, {"name": nid, "family": "other",
                                  "lifecycle": {"status": "active", "sunset_date": None,
                                                "successor_network_id": None,
                                                "announcement_url": None, "last_reviewed": None}})
        networks.append(network_object(
            nid, meta, [o["id"] for o in by_net[nid]], generated_at, liveness[nid]))
    net_by_id = {n["id"]: n for n in networks}

    write(os.path.join(API_DIR, "faucets.json"), envelope(faucets=objs))
    write(os.path.join(API_DIR, "networks.json"), envelope(networks=networks))
    write(os.path.join(API_DIR, "all.json"), envelope(networks=networks, faucets=objs))

    # One file per network so consumers can fetch a single network on static
    # hosting (no query-param filtering possible).
    for nid, group in by_net.items():
        write(os.path.join(API_DIR, "networks", f"{nid}.json"),
              envelope(network=net_by_id.get(nid), faucets=group))

    advancing = sum(1 for n in networks if n["liveness"]["chain_advancing"])
    deprecated = [n["id"] for n in networks if n["lifecycle"]["status"] != "active"]
    print(f"API: {len(objs)} faucets, {len(networks)} networks -> /api/v1/")
    print(f"  automatable: {sum(1 for o in objs if o['automatable'])} | "
          f"chain_advancing: {advancing} | deprecated: {deprecated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
