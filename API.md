# testnetfaucets.dev — JSON API (v1)

Static JSON served from the CDN. No server, no keys, no rate limits. Data is
regenerated on each daily check; assert freshness against `next_update_expected_at`.

## Endpoints

| URL | Contents |
| --- | --- |
| `https://testnetfaucets.dev/api/v1/all.json` | Everything |
| `https://testnetfaucets.dev/api/v1/faucets.json` | The faucet array |
| `https://testnetfaucets.dev/api/v1/networks/<network-id>.json` | One network's faucets |

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

## Coming next (not in v1 yet)

- **`networks.json`** with per-network `lifecycle` (`active | deprecated | sunset
  | dead`, `successor_network_id`, `announcement_url`, `last_reviewed`) and
  `liveness` (`rpc_responding`, `chain_advancing`, `latest_block`,
  `block_age_seconds`). Lifecycle discipline: **no network gets a non-`active`
  status without an `announcement_url` and a dated `last_reviewed`** — a wrong
  deprecation breaks someone's pipeline.
- **`testnet-preflight`** CLI — fails a CI job before it runs if a network is
  deprecated/halted, printing the successor + announcement; and warns when a
  test wallet's balance drops below a threshold, printing the automatable faucet.

## Compatibility contract

- **IDs (`id`, `network_id`) are stable and never reused.**
- **Adding fields is non-breaking.** Consumers must ignore unknown fields.
- **Removing or renaming a field, or changing a value's meaning, bumps `/v2/`**,
  with `/v1/` kept live for at least 90 days after `/v2/` ships.
