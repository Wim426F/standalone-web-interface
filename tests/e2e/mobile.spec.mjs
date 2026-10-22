import { test, expect, openApp, gotoTab } from './fixtures.mjs';

// Phone-sized smoke tests: every tab renders at a 375px-wide touch viewport
// without uncaught errors (fixture) and without the page itself scrolling
// sideways — wide tables may scroll inside their own container, the page
// must not.

test.use({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });

const TABS = [
  ['Dashboard', '#dashboard'],
  ['Parameters', '#parameters'],
  ['Spot Values', '#spotvalues'],
  ['Plot', '#plot'],
  ['Gauges', '#gauges'],
  ['Data Logger', '#logger'],
  ['CAN Mapping', '#canmapping'],
  ['Files', '#files'],
  ['Update', '#update'],
  ['Settings', '#settings'],
  ['Support', '#support'],
];

const horizontalOverflow = (page) => page.evaluate(() =>
  document.documentElement.scrollWidth - document.documentElement.clientWidth);

test.describe('Mobile viewport', () => {
  for (const [label, marker] of TABS) {
    test(`${label} tab renders without horizontal page scroll`, async ({ page, mock }) => {
      await openApp(page, mock);
      await gotoTab(page, label);
      await expect(page.locator(marker).first()).toBeVisible();
      expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
    });
  }

  test('navigation is reachable and switches tabs by tap', async ({ page, mock }) => {
    await openApp(page, mock);
    // Bottom bar: the most used tabs, one tap away
    const bar = page.locator('#bottombar');
    await expect(bar).toBeVisible();
    await bar.locator('.bb-item', { hasText: 'Params' }).tap();
    await expect(page.locator('#params tr', { hasText: 'fweak' })).toBeVisible();
    await expect(page).toHaveURL(/#parameters$/);
    // More opens the full menu as a drawer; picking a section closes it
    await expect(page.locator('.tablink', { hasText: 'Settings' })).not.toBeInViewport();
    await page.locator('#bottombar-more').tap();
    await page.locator('.tablink', { hasText: 'Settings' }).tap();
    await expect(page).toHaveURL(/#settings/);
    await expect(page.locator('.tablink', { hasText: 'Settings' })).not.toBeInViewport();
    await expect(page.locator('#bottombar-more')).toHaveClass(/active/); // Settings lives under More
  });

  test('a parameter can be edited on a phone', async ({ page, mock }) => {
    await openApp(page, mock);
    await gotoTab(page, 'Parameters');
    const input = page.locator('#params tr', { hasText: 'udcmin' }).locator('input[type="number"]');
    await input.tap();
    await input.fill('455');
    await input.press('Enter');
    await expect.poll(async () => (await mock.state()).inverter.params.udcmin.value).toBe(455);
  });

  test('plotter runs at phone width', async ({ page, mock }) => {
    await mock.putFile('plots.json', { plots: [{ id: 1, items: [{ name: 'udc' }] }], maxValues: 100, burstLength: 2 });
    await openApp(page, mock);
    await gotoTab(page, 'Plot');
    await page.locator('#plot button', { hasText: /^Start$/ }).tap();
    await expect.poll(() => page.evaluate(() => {
      const ch = window.Chart.getChart(document.querySelector('#plot canvas'));
      return ch ? ch.data.datasets[0].data.length : 0;
    })).toBeGreaterThanOrEqual(3);
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
  });

  test('CAN mode settings render at phone width', async ({ page, mock }) => {
    await mock.enableCan();
    await openApp(page, mock);
    await gotoTab(page, 'Settings');
    await expect(page.locator('#can-scan-btn')).toBeVisible();
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
  });
});
