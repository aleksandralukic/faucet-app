# Project status & next steps

_Snapshot of where **testnetfaucets.dev** is and what's worth doing next._

## What it is

A live directory of blockchain testnet faucets that **health-checks every faucet
daily** and, where possible, **verifies on-chain** that the faucet's wallet is
actually funded. Static site on GitHub Pages, custom domain, no server.

- **Live:** https://testnetfaucets.dev
- **Repo:** https://github.com/aleksandralukic/faucet-app

## Current state

- **41 faucets** across ~37 currencies / 35+ networks.
- **8 verified on-chain** (Avalanche, Sei, Sepolia/pk910, Filecoin, Core, Flare,
  Stellar, Bitcoin) across 4 chain types (EVM, Filecoin/Filfox, Stellar/Horizon,
  UTXO). See [`ONCHAIN.md`](ONCHAIN.md).
- **SEO:** intent-based titles, per-currency pages, `/down/`, sitemap, JSON-LD,
  real favicon + og:image. Verified in Google Search Console; sitemap submitted.
- **UX:** get-tokens framing, comparison tables, on-chain evidence panels,
  "⛓ Verified on-chain" filter, usefulness-based ordering, cache-busted assets.

## How it works (architecture)

| Piece | Role |
| --- | --- |
| `data/faucets.json` | The source list you edit. |
| `scripts/check_faucets.py` | Daily HTTP check + failure classification + on-chain override. |
| `scripts/onchain.py` | Keyless on-chain liveness (RPC / Blockscout / Filfox / Horizon / UTXO). |
| `scripts/build_site.py` | Renders static HTML (homepage, per-currency, `/down/`, sitemap). |
| `.github/workflows/daily-check.yml` | Runs the check + build, commits, deploys via the branch builder. |
| `scripts/verify.py` + `tests/` | **Independently verify the live site is truthful** (below). |
| `.github/workflows/verify.yml` | Runs the verifier every 6h; alerts on failure. |

Deploy model: Pages serves from `main` (branch builder). The daily job commits
built pages; **do not add a second Pages deploy job** — it races the branch
builder (that bug cost us a day early on).

## Truth verification (the trust backstop)

Two layers, run every 6 hours by `verify.yml` against the **live** site:

1. **`scripts/verify.py`** (data & on-chain) — fetches what the site serves and
   re-derives the claims: **freshness** (fails if data is >36h old while the UI
   implies it's current), **on-chain truth** (re-queries every verified wallet
   live and fails if a served balance/payout can't be real), and **consistency**
   (counts add up, required fields present). Also soft-probes a sample of
   "working" faucets.
2. **`tests/e2e.spec.js`** (Playwright) — loads the rendered page: cards render
   with no JS errors, the "last checked" line shows, the on-chain filter reveals
   exactly the verified faucets, and a card's displayed balance matches the data.

**Notifications:** a failing run emails the repo owner (GitHub default). For a
phone push, install the **ntfy** app, subscribe to a topic, and set a repo
variable `NTFY_TOPIC` to that topic (Settings → Secrets and variables → Actions
→ Variables). Failures then push to your phone with a link to the run.

## Next steps

**Growth (highest leverage now — the product is mature):**
1. **Watch Google Search Console** weekly. It's ~a week since the title/CTR
   overhaul; real query data should be landing. Let it guide any content work.
2. **Backlinks / distribution** — the on-chain verification is a genuine
   differentiator. A LinkedIn post or a short write-up ("I verify testnet faucets
   on-chain") earns links the on-page work can't. LinkedIn post is planned.

**Small product wins (optional):**
3. **LTC on-chain** — needs the real *dispensing* address (the `from` on a
   payout you *receive*; the page's wallet is a return address).
4. **Trim dead weight** — remove Fantom (chain deprecated) and reconsider the
   DOGE faucet (no live public replacement found).
5. More dispenser addresses (BNB/Amoy are blocked; see [`ONCHAIN.md`](ONCHAIN.md)).

**Otherwise: maintenance mode.** It self-updates daily and now self-verifies
every 6h. Add faucets/addresses as you find them; watch GSC.

## Gotchas / operational notes

- GitHub disables scheduled workflows after **60 days of repo inactivity** — the
  daily commit keeps it active, but if everything goes quiet, a manual run
  re-arms it.
- Assets are cache-busted with `?v=<build>`; a UI change reaches users on the
  next deploy, not after a 4h cache.
- `verify.py` and the E2E tests hit the **live** site, so they also catch a
  broken deploy, not just bad data.
