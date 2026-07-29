# testnetfaucets.dev — JSON API (v1)

Static JSON served from the CDN. No server, no keys, no rate limits. Data is
regenerated on each daily check; assert freshness against `next_update_expected_at`.

## Endpoints

| URL | Contents |
| --- | --- |
| `https://testnetfaucets.dev/api/v1/all.json` | Everything (networks + faucets) |
| `https://testnetfaucets.dev/api/v1/faucets.json` | The faucet array |
| `https://testnetfaucets.dev/api/v1/networks.json` | The network array |
| `https://testnetfaucets.dev/api/v1/networks/<network-id>.json` | One network + its faucets |

Query-param filtering (`?network=…`) does **not** work on static hosting — either
filter client-side (the payload is tiny) or fetch the per-network file.
`network-id` is the network name slugified, e.g. `ethereum-sepolia`, `polygon-amoy`.

## Envelope

Every file is wrapped in:

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-07-29T13:22:00+00:00",
  "next_update_expected_at": "2026-07-30T13:22:00+00:00",
  "faucets": [ ... ]
}
```

`next_update_expected_at` is the freshness contract: if it's in the past, the
feed has gone stale (our job stopped) — treat the data as unreliable without
needing to know our schedule.

## Faucet object

```json
{
  "id": "pk910-sepolia",
  "network_id": "ethereum-sepolia",
  "name": "pk910 Sepolia PoW Faucet",
  "url": "https://sepolia-faucet.pk910.de/",
  "currency": "ETH",
  "status": "working",
  "verification": "onchain",
  "checked_at": "2026-07-29T13:22:00+00:00",
  "amount": "variable (PoW)",
  "cooldown": null,
  "friction": ["captcha"],
  "automatable": false,
  "claim_api": null,
  "notes": "…",
  "evidence": {
    "http_status": 200,
    "wallet_balance": "9611.7",
    "wallet_balance_unit": "ETH",
    "payouts_sent": 5436567,
    "last_dispensed_at": "2026-07-29T13:22:00+00:00"
  }
}
```

- **`status`** — `working | degraded | down | manual | unknown`.
- **`verification`** — how the status was reached: `onchain` (we read the
  dispensing wallet's live balance + payout history — the strongest signal),
  `http` (the faucet's page responded), or `manual`.
- **`friction`** — barriers before a claim: `captcha`, `wallet`,
  `mainnet-balance`, `login:<service>`. The fields that predict whether a claim
  is even possible for a given consumer.
- **`automatable`** — `true` only when a script can claim without a browser,
  captcha, or login. A small, strictly-vetted set.
- **`claim_api`** — when `automatable`, the `{method, endpoint, …}` to call.
- **`evidence`** — the raw measurements. `wallet_balance` is a decimal string;
  a `0` balance on a `degraded` faucet means it's dry. `last_dispensed_at` is
  approximate to the day.

## Network object

```json
{
  "id": "ethereum-sepolia",
  "chain_id": 11155111,
  "name": "Ethereum Sepolia",
  "family": "evm",
  "lifecycle": {
    "status": "active",
    "sunset_date": null,
    "successor_network_id": null,
    "announcement_url": null,
    "last_reviewed": "2026-07-29"
  },
  "liveness": {
    "checked_at": "2026-07-29T13:39:31+00:00",
    "rpc_responding": true,
    "chain_advancing": true,
    "latest_block": 8123456,
    "block_age_seconds": 12
  },
  "faucet_ids": ["aave-staging", "sepolia-faucet", "pk910-sepolia"]
}
```

- **`lifecycle.status`** — `active | deprecated | sunset | dead`. **Discipline:
  no network carries a non-`active` status without an `announcement_url` and a
  dated `last_reviewed`** — a wrong deprecation breaks someone's pipeline, so it
  must be auditable, not our opinion. `last_reviewed` is refreshed on a schedule.
- **`liveness.chain_advancing`** — the assertion that matters: a *halted* chain
  answers RPC perfectly, so this is derived from the latest block's age, not mere
  reachability. `true` = block is fresh, `false` = readable but stale (halted),
  `null` = we couldn't read it this run (don't treat as down). Currently measured
  for EVM networks; `null` for other families.

## `testnet-preflight` — the CI gate

[`scripts/testnet_preflight.py`](scripts/testnet_preflight.py) turns this data
into a preflight check you run *before* your test suite, so a dead testnet or a
drained wallet fails with a diagnosis instead of a mid-run mystery. Stdlib only,
no install — `curl` the script into CI, or vendor it.

```bash
# Fail the job if a network you depend on is deprecated (default) or halted:
python3 testnet_preflight.py --networks ethereum-sepolia,polygon-amoy --fail-on halted
#   ✗ fantom-testnet: DEPRECATED — successor: … — https://docs.soniclabs.com/

# Fail if a test wallet has drained below the threshold your suite needs
# (EVM networks); prints the automatable faucet when one exists:
python3 testnet_preflight.py --balance 0xYourAddr:0.1@ethereum-sepolia
```

Exit `0` = passed, `1` = a check failed (with the reason), `2` = usage/fetch error.

## Compatibility contract

- **IDs (`id`, `network_id`) are stable and never reused.**
- **Adding fields is non-breaking.** Consumers must ignore unknown fields.
- **Removing or renaming a field, or changing a value's meaning, bumps `/v2/`**,
  with `/v1/` kept live for at least 90 days after `/v2/` ships.
