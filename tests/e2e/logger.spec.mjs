import { test, expect, openApp, gotoTab } from './fixtures.mjs';

test.describe('Data logger', () => {
  test('logs lines at a bounded rate instead of hammering the device', async ({ page, mock }) => {
    await openApp(page, mock);
    await gotoTab(page, 'Data Logger');
    await page.locator('button', { hasText: 'Add field to log' }).click();
    await page.locator('#logger div', { hasText: /^Select\.\.\.$/ }).last().click();
    await page.locator('#logger .hover-row', { hasText: /^udc/ }).first().click();

    // The loop used to fire the next request the moment one returned: the
    // single-threaded ESP server was starved, and an instant reply (the live
    // demo) froze the page. Now it's paced to at most 20 samples/s.
    let cmdRequests = 0;
    page.on('request', r => { if (r.url().includes('/cmd?')) cmdRequests++; });
    await page.locator('button', { hasText: 'Start logging' }).click();
    await expect(page.locator('#logger .pill', { hasText: 'Recording' })).toBeVisible();
    const t0 = Date.now();
    await expect.poll(() => page.locator('#data-logger-text-area').inputValue()
      .then(v => v.trim().split('\n').length)).toBeGreaterThan(5);
    await page.waitForTimeout(1000); // absence check: a runaway loop would pile up hundreds more
    const elapsed = (Date.now() - t0) / 1000;
    await page.locator('button', { hasText: 'Stop logging' }).click();

    // 20/s pacing plus the rest of the app's own polling, with slack
    expect(cmdRequests).toBeLessThan(elapsed * 20 + 15);
    const lines = (await page.locator('#data-logger-text-area').inputValue()).trim().split('\n');
    expect(lines[0]).toBe('udc');
    expect(lines[1]).toMatch(/^-?\d/);
  });
});
