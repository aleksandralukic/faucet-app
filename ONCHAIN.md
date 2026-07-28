# On-chain liveness — progress tracker

How far along the on-chain verification tier is, which faucets are covered, and
how to add more. See [`scripts/onchain.py`](scripts/onchain.py) for the checker.

## Concepts

- **Keyless** — every check uses public endpoints that need **no API key or
  signup**: public JSON-RPC nodes (for EVM balances) and open explorer APIs
  (Blockscout, Filfox). Nothing to register, nothing to rotate, nothing to leak.
- **Payout count** — how many transactions the dispensing wallet has *sent*
  (its nonce, on EVM). A faucet wallet that has sent millions of transactions is
  unmistakably a faucet, and proves it actually dispenses rather than just
  holding a balance.
- **Last-dispense recency** — days since the wallet's most recent *outbound*
  transaction. "Last dispensed 0d ago" means it paid someone out today, i.e.
  it's alive right now. Only available where a keyless explorer exists for the
  chain; otherwise we fall back to balance + payout count.

A faucet is marked **on-chain verified** when its wallet holds funds **and** has
a payout history (and, where we can tell, dispensed recently). That verdict
**overrides** the HTTP check — so a faucet behind Cloudflare that the HTTP check
would call "degraded" is correctly "up".

## Verified — live at the on-chain tier (5)

| Chain | Faucet (id) | Dispenser wallet | Evidence (at time of writing) |
| --- | --- | --- | --- |
| Avalanche Fuji | `covalent-avax` | `0x2352d20fc81225c8ecd8f6faa1b37f24fed450c9` | 6,695 AVAX · 2.69M payouts |
| Sei atlantic-2 | `sei-atlantic` | `0x9452fe1f7cdffcb2819c052656746be795598055` | 75,710 SEI · 1.18M payouts |
| Ethereum Sepolia | `pk910-sepolia` | `0x6Cc9397c3B38739daCbfaA68EaD5F5D77Ba5F455` | 9,614 ETH · 5.4M payouts · 0d |
| Filecoin Calibration | `beryx-filecoin` | `t1laee6wd4pznv424xu7lgnrlmqa77hueeav4jgxi` | 106,586 tFIL · 45,874 msgs · 0d |
| Core test2 | `core-testnet` | `0x0ce88ad9a045596a39b2cd7943117d01b21ffd84` | 991,656 tCORE2 · 8,343 payouts |

## Pending — need the dispenser address

The blocker is always the same: the faucet's dispensing wallet isn't published,
and the chain's explorer blocks automated lookups. The fix is a **manual claim**
— request tokens to any address, then read the `from` address on the payout
transaction. That's the dispenser; validate it with `scripts/onchain.py`.

| Chain | Faucet | Status |
| --- | --- | --- |
| Polygon Amoy | `polygon-faucet-*` | Blocked: GitHub auth on the faucet 404s (can't claim). Candidate `0xc3af235c41376a8c07d9db5c0d06ec5cc1c52a3e` is a real live Amoy faucet but couldn't be confirmed as *this* faucet's (multi-chain operator) — not used. |
| BNB testnet | `bnb-smart-chain` | Dispenser not published; BscScan blocks lookups. |
| Flare Coston2 | `flare-faucet` | Payout wallet not isolable from FTSO system traffic. |
| Ethereum Sepolia (Google Cloud) | `sepolia-faucet` | Google's hot wallet isn't published — but `pk910-sepolia` covers Sepolia on-chain already. |

## Not covered yet

- **Litecoin, Dogecoin** (`bitaps-ltc`, `shibe-technology`) — UTXO chains; the
  EVM/Filfox paths don't apply. Would need a Blockstream/BlockCypher-style API.
- **Fantom testnet** (`fantom-faucet`) — chain deprecated after the Sonic
  rebrand; RPC/explorer are dead. Candidate for removal.

## How to add a faucet to the on-chain tier

1. **Get the dispenser address** — claim the faucet once, read the `from` on the
   payout transaction (any block explorer shows it).
2. **Validate it:** `python3 scripts/onchain.py <chain> <address>` — a real
   faucet shows a large payout count and a healthy balance.
3. **Add the chain** to `CHAIN_PROFILES` in `scripts/onchain.py` if it's new
   (EVM needs an RPC; add a keyless explorer for recency if one exists).
4. **Wire it** in `data/faucets.json`: `"onchain": { "chain": "...", "wallet": "..." }`.

## Chains supported in `CHAIN_PROFILES`

`sepolia` (+Blockscout recency), `amoy`, `fuji`, `bsc-testnet`, `flare-coston2`,
`core-test2`, `sei-atlantic`, `filecoin-calibration` (Filfox).
