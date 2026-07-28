// @ts-check
module.exports = {
  testDir: "./tests",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  retries: 1, // the live site can blip; one retry avoids flaky-network false alarms
  reporter: "list",
  use: { headless: true, actionTimeout: 15_000 },
};
