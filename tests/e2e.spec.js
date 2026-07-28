// End-to-end checks against the LIVE site: the page renders, the data isn't
// stale, and the on-chain claims a user sees actually match the served data.
const { test, expect } = require("@playwright/test");

const SITE = process.env.VERIFY_SITE || "https://testnetfaucets.dev";
const MAX_AGE_H = 36;

test("homepage renders faucet cards with no JS errors", async ({ page }) => {
  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(SITE, { waitUntil: "networkidle" });
  const cards = page.locator("article.card");
  await expect(cards.first()).toBeVisible();
  expect(await cards.count(), "should render many faucet cards").toBeGreaterThan(20);
  expect(errors, "no console/page errors").toEqual([]);
});

test("data is fresh, not stale", async ({ request }) => {
  const res = await request.get(`${SITE}/data/status.json`);
  expect(res.ok()).toBeTruthy();
  const s = await res.json();
  const ageH = (Date.now() - new Date(s.generatedAt).getTime()) / 3.6e6;
  expect(ageH, `data is ${ageH.toFixed(1)}h old`).toBeLessThan(MAX_AGE_H);
});

test("the 'last checked' line renders", async ({ page }) => {
  await page.goto(SITE, { waitUntil: "networkidle" });
  await expect(page.locator("#generated")).toContainText(/Last checked/i);
});

test("on-chain filter shows exactly the verified faucets", async ({ page }) => {
  await page.goto(SITE, { waitUntil: "networkidle" });
  const chip = page.locator(".tier-filter");
  await expect(chip).toBeVisible();

  const n = parseInt((await chip.textContent()).match(/\((\d+)\)/)[1], 10);
  expect(n).toBeGreaterThan(0);

  await chip.click();
  // render() rebuilds the list to only matching faucets.
  await expect(page.locator("article.card")).toHaveCount(n);
  // every remaining card carries the on-chain badge.
  await expect(page.locator("article.card .tier.onchain")).toHaveCount(n);
});

test("a card's on-chain balance matches the served data", async ({ page, request }) => {
  const s = await (await request.get(`${SITE}/data/status.json`)).json();
  const oc = s.results.find((r) => r.id === "covalent-avax");
  test.skip(!oc?.onchain?.balanceStr, "no AVAX on-chain data to check");

  await page.goto(SITE, { waitUntil: "networkidle" });
  const card = page.locator("article.card", {
    has: page.locator(".ticker", { hasText: "AVAX-C" }),
  });
  // the integer part of e.g. "6,695 AVAX" must appear on the card.
  const num = oc.onchain.balanceStr.split(" ")[0];
  await expect(card).toContainText(num);
});
