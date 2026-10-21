import { defineConfig } from '@playwright/test';

const CI = !!process.env.CI;

export default defineConfig({
  testDir: 'tests/e2e',
  fullyParallel: true,
  forbidOnly: CI,
  // Locally: no retries, so a flaky spec fails loudly instead of hiding.
  // CI: retry twice; a pass-on-retry is reported as "flaky" (and annotated
  // by the github reporter) rather than silently green.
  retries: CI ? 2 : 0,
  // Every test spins up its own mock ESP; the hosted runners have 4 vCPUs
  workers: CI ? 3 : undefined,
  reporter: CI
    ? [['list'], ['github'], ['html', { open: 'never', outputFolder: 'playwright-report' }]]
    : [['list']],
  timeout: 30000,
  expect: { timeout: 5000 },
  use: {
    viewport: { width: 1280, height: 900 },
    trace: CI ? 'retain-on-first-failure' : 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
