/**
 * Playwright config (CommonJS for "type": "module" projects). Base URL from env.
 */
const baseURL =
  process.env.BASE_URL ||
  process.env.PLAYWRIGHT_BASE_URL ||
  'http://localhost:10080'

module.exports = {
  testDir: './e2e',
  // The UI-regression tier has its own config (playwright.ui.config.cjs) and a
  // dedicated served build; keep its specs out of the default suite so a plain
  // `test:e2e` never tries to run them against the wrong base URL.
  testIgnore: ['**/ui-regression/**', '**/access-control/**'],
  globalSetup: require.resolve('./e2e/access-control/global-setup.cjs'),
  globalTeardown: require.resolve('./e2e/access-control/global-teardown.cjs'),
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: process.env.CI ? 'list' : 'html',
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'on-first-retry'
  },
  projects: [{ name: 'chromium', use: { channel: 'chromium' } }],
  timeout: 30000
}
