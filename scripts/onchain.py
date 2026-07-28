#!/usr/bin/env python3
"""
On-chain liveness checks for faucets whose dispensing wallet is known.

The idea: you don't need to solve a captcha to know whether a faucet can pay.
Most faucets dispense from a public hot wallet. If that wallet holds funds and
has been sending payouts, the faucet is alive — regardless of whether its
front-end sits behind Cloudflare. This is a stronger signal than "the page
loaded", and it sidesteps the bot-protection that makes an HTTP check lie.

Keyless: EVM balance + nonce come from public JSON-RPC (works for every EVM
chain); recency ("last dispensed N days ago") comes from a Blockscout explorer
where one is available. No API keys, stdlib only.
"""

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

TIMEOUT = 15

# Per-chain endpoints. `rpc` is a keyless JSON-RPC node (publicnode); `explorer`
# is a keyless Blockscout API base (or None → balance/nonce only, no recency).
CHAIN_PROFILES = {
    "sepolia": {
        "rpc": "https://ethereum-sepolia-rpc.publicnode.com",
        "explorer": "https://eth-sepolia.blockscout.com/api",
        "symbol": "ETH", "decimals": 18, "kind": "evm",
    },
    "amoy": {
        "rpc": "https://polygon-amoy-bor-rpc.publicnode.com",
        "explorer": "https://amoy.polygonscan.com/api",  # etherscan-style; may need key → treated as best-effort
        "symbol": "POL", "decimals": 18, "kind": "evm",
    },
    "fuji": {
        "rpc": "https://avalanche-fuji-c-chain-rpc.publicnode.com",
        "explorer": None,
        "symbol": "AVAX", "decimals": 18, "kind": "evm",
    },
    "bsc-testnet": {
        "rpc": "https://bsc-testnet-rpc.publicnode.com",
        "explorer": None,
        "symbol": "BNB", "decimals": 18, "kind": "evm",
    },
    "flare-coston2": {
        "rpc": "https://coston2-api.flare.network/ext/C/rpc",
        "explorer": "https://coston2-explorer.flare.network/api",
        "symbol": "C2FLR", "decimals": 18, "kind": "evm",
    },
    "filecoin-calibration": {
        "rpc": "https://api.calibration.node.glif.io/rpc/v1",
        "explorer": "https://filfox.info/api/v1",  # bespoke; handled separately if used
        "symbol": "tFIL", "decimals": 18, "kind": "evm",
    },
    "core-test2": {
        "rpc": "https://rpc.test2.btcs.network",
        "explorer": None,  # explorer API requires login → balance/nonce only
        "symbol": "tCORE2", "decimals": 18, "kind": "evm",
    },
    "sei-atlantic": {
        "rpc": "https://evm-rpc-testnet.sei-apis.com",
        "explorer": None,
        "symbol": "SEI", "decimals": 18, "kind": "evm",
    },
}

HEADERS = {"Content-Type": "application/json", "User-Agent": "testnetfaucets.dev-onchain/1.0"}


def _post_json(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=HEADERS, method="POST"
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def _get_json(url):
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def _rpc(rpc_url, method, params):
    resp = _post_json(rpc_url, {"jsonrpc": "2.0", "method": method, "params": params, "id": 1})
    if "error" in resp:
        raise RuntimeError(resp["error"])
    return resp["result"]


def evm_balance_and_nonce(profile, address):
    """(balance_native_float, tx_count_int). tx_count = payouts ever sent."""
    rpc = profile["rpc"]
    wei = int(_rpc(rpc, "eth_getBalance", [address, "latest"]), 16)
    nonce = int(_rpc(rpc, "eth_getTransactionCount", [address, "latest"]), 16)
    return wei / (10 ** profile["decimals"]), nonce


def blockscout_last_outbound_days(explorer, address, now_ts):
    """Days since the wallet's most recent OUTBOUND tx, or None if unavailable."""
    url = (
        f"{explorer}?module=account&action=txlist&address={address}"
        f"&sort=desc&page=1&offset=20"
    )
    try:
        data = _get_json(url)
    except Exception:
        return None
    if str(data.get("status")) != "1" or not isinstance(data.get("result"), list):
        return None
    addr = address.lower()
    for tx in data["result"]:
        if (tx.get("from") or "").lower() == addr:
            try:
                ts = int(tx["timeStamp"])
            except (KeyError, ValueError):
                continue
            return round((now_ts - ts) / 86400, 1)
    return None


def check(cfg, now_ts=None):
    """Run the on-chain check for one faucet.

    cfg = {"chain": "sepolia", "wallet": "0x...", "minBalance": 0.01, "maxIdleDays": 45}
    Returns a dict with balance/activity evidence and an `ok` verdict, or an
    `error` if the wallet couldn't be read. Never raises.
    """
    now_ts = now_ts or int(time.time())
    chain = cfg["chain"]
    address = cfg["wallet"]
    profile = CHAIN_PROFILES.get(chain)
    if not profile:
        return {"ok": False, "error": f"unknown chain '{chain}'"}

    try:
        balance, nonce = evm_balance_and_nonce(profile, address)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    idle_days = None
    if profile.get("explorer"):
        idle_days = blockscout_last_outbound_days(profile["explorer"], address, now_ts)

    min_balance = cfg.get("minBalance", 0)
    max_idle = cfg.get("maxIdleDays", 45)
    symbol = profile["symbol"]

    # Verdict: funded, has sent payouts, and (if we can tell) sent recently.
    ok = balance > min_balance and nonce > 0
    if idle_days is not None and idle_days > max_idle:
        ok = False

    if balance >= 1000:
        bal_str = f"{balance:,.0f}"
    elif balance >= 1:
        bal_str = f"{balance:,.2f}".rstrip("0").rstrip(".")
    else:
        bal_str = f"{balance:.4g}"
    parts = [f"holds {bal_str} {symbol}", f"{nonce:,} payouts sent"]
    if idle_days is not None:
        parts.append(f"last dispensed {idle_days:g}d ago")
    evidence = " · ".join(parts)

    return {
        "ok": ok,
        "chain": chain,
        "symbol": symbol,
        "balance": round(balance, 6),
        "payoutsSent": nonce,
        "lastDispenseDays": idle_days,
        "evidence": evidence,
    }


if __name__ == "__main__":
    # Manual smoke test: pass "chain address" on the command line.
    import sys
    if len(sys.argv) == 3:
        print(json.dumps(check({"chain": sys.argv[1], "wallet": sys.argv[2]}), indent=2))
    else:
        print("usage: onchain.py <chain> <address>")
