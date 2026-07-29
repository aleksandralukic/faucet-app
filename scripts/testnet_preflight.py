#!/usr/bin/env python3
"""
testnet-preflight — fail a CI job BEFORE the suite runs, with a diagnosis
instead of a mystery, when a testnet has gone away or a wallet has drained.

It reads the public testnetfaucets.dev API (no key, no install beyond Python)
and checks two things nobody else catches ahead of time:

  1. Network lifecycle + liveness — is a network you depend on deprecated,
     sunset, or halted? Prints the successor and the announcement link.
  2. Wallet balance — is a test wallet below the threshold your suite needs?
     Prints the automatable faucet for that network so the fix is one line.

Examples
  # Fail if either network is deprecated (default) or halted:
  testnet_preflight.py --networks ethereum-sepolia,polygon-amoy --fail-on halted

  # Fail if the wallet holds < 0.1 ETH on Sepolia:
  testnet_preflight.py --balance 0xYourAddr:0.1@ethereum-sepolia

Exit codes: 0 = all good, 1 = a check failed, 2 = usage/fetch error.
Stdlib only.
"""

import argparse
import json
import sys
import urllib.request

API = "https://testnetfaucets.dev/api/v1/all.json"
BAD_LIFECYCLE = {"deprecated", "sunset", "dead"}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "testnet-preflight/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def rpc_balance(rpc, address):
    req = urllib.request.Request(
        rpc,
        data=json.dumps({"jsonrpc": "2.0", "method": "eth_getBalance",
                         "params": [address, "latest"], "id": 1}).encode(),
        # Many public RPCs 403 the default "Python-urllib" User-Agent.
        headers={"Content-Type": "application/json",
                 "User-Agent": "testnet-preflight/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return int(json.loads(r.read().decode())["result"], 16) / 1e18


def check_networks(data, ids, fail_on):
    nets = {n["id"]: n for n in data.get("networks", [])}
    failed = False
    for nid in ids:
        n = nets.get(nid)
        if not n:
            print(f"  ? {nid}: not in the directory (typo, or not tracked yet)")
            failed = True
            continue
        lc = n.get("lifecycle") or {}
        live = n.get("liveness") or {}
        status = lc.get("status", "active")
        advancing = live.get("chain_advancing")

        if status in BAD_LIFECYCLE:
            succ = lc.get("successor_network_id") or "see announcement"
            print(f"  ✗ {nid}: {status.upper()} — successor: {succ} — {lc.get('announcement_url')}")
            failed = True
        elif fail_on == "halted" and advancing is False:
            age = live.get("block_age_seconds")
            print(f"  ✗ {nid}: chain HALTED (last block {age}s old, RPC still answering)")
            failed = True
        else:
            note = "" if advancing is None else f", advancing={advancing}"
            print(f"  ✓ {nid}: {status}{note}")
    return failed


def check_balances(data, specs):
    nets = {n["id"]: n for n in data.get("networks", [])}
    faucets = data.get("faucets", [])
    failed = False
    for spec in specs:
        try:
            addr_min, nid = spec.split("@", 1)
            addr, minimum = addr_min.rsplit(":", 1)
            minimum = float(minimum)
        except ValueError:
            print(f"  ! bad --balance '{spec}' (want addr:min@network)")
            failed = True
            continue
        n = nets.get(nid)
        if not n or not n.get("rpc"):
            print(f"  ! {nid}: no RPC available for a balance check (EVM only for now)")
            failed = True
            continue
        try:
            bal = rpc_balance(n["rpc"], addr)
        except Exception as e:
            print(f"  ! {nid}: couldn't read balance ({type(e).__name__})")
            failed = True
            continue
        if bal < minimum:
            hint = next((f for f in faucets
                         if f["network_id"] == nid and f.get("automatable")), None)
            fix = (f" — top up via {hint['claim_api']['method']} {hint['claim_api']['endpoint']}"
                   if hint else " — no automatable faucet; see testnetfaucets.dev")
            print(f"  ✗ {addr[:10]}… on {nid}: {bal:.4g} < {minimum} needed{fix}")
            failed = True
        else:
            print(f"  ✓ {addr[:10]}… on {nid}: {bal:.4g} (>= {minimum})")
    return failed


def main():
    ap = argparse.ArgumentParser(prog="testnet-preflight", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--networks", help="comma-separated network ids to check")
    ap.add_argument("--fail-on", choices=["deprecated", "halted"], default="deprecated",
                    help="'deprecated' (default) fails on lifecycle only; "
                         "'halted' also fails a stalled chain")
    ap.add_argument("--balance", action="append", default=[], metavar="ADDR:MIN@NETWORK",
                    help="fail if ADDR holds < MIN native tokens on NETWORK (repeatable)")
    ap.add_argument("--api", default=API, help="override the API URL")
    args = ap.parse_args()

    if not args.networks and not args.balance:
        ap.error("give --networks and/or --balance")

    try:
        data = fetch(args.api)
    except Exception as e:
        print(f"preflight: couldn't fetch {args.api}: {e}", file=sys.stderr)
        return 2

    stale = data.get("next_update_expected_at")
    print(f"testnet-preflight · data generated {data.get('generated_at')}")
    failed = False
    if args.networks:
        print("networks:")
        failed |= check_networks(data, [x.strip() for x in args.networks.split(",") if x.strip()], args.fail_on)
    if args.balance:
        print("balances:")
        failed |= check_balances(data, args.balance)

    print("\n" + ("FAIL — see above." if failed else "OK — preflight passed."))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
