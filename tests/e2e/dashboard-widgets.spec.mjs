import { test, expect, openApp, gotoTab } from './fixtures.mjs';

// The Dashboard is a gauge page (gauges.json v4 names it `dashboard`): same
// grid, same editor, same tiles. Widget tiles (status, metric, messages,
// terminal, actions, CAN sender) can sit on it or on any gauge page.

const v3Layout = { v: 3, pages: [{ id: 1, name: 'Main', items: [
  { id: 1, name: 'udc', type: 'radial', min: 0, max: 500, x: 0, y: 0, w: 3, h: 3 },
] }] };

const saved = (mock) => fetch(mock.url + '/gauges.json').then(r => r.json());
const dashPageOf = (data) => data.pages.find(p => String(p.id) === String(data.dashboard));

test.describe('Dashboard widgets', () => {
  test('a fresh device gets a Dashboard built from widget tiles', async ({ page, mock }) => {
    await openApp(page, mock);
    const dash = page.locator('#dashboard');
    await expect(dash.locator('.gauge-tile.widget-tile')).toHaveCount(5);
    await expect(dash.locator('.hero-state')).toHaveText('Inverter idle'); // the mock inverter starts in Off
    await expect(dash.locator('#commandinput')).toBeVisible();
    await expect(dash.locator('button', { hasText: 'Start inverter in manual mode' })).toBeVisible();
    await expect(dash.locator('.widget-messages')).toContainText('Inverter messages');
  });

  test('a v3 layout keeps its gauge pages and gains a Dashboard page on save', async ({ page, mock }) => {
    await mock.putFile('gauges.json', v3Layout);
    await openApp(page, mock);
    await gotoTab(page, 'Gauges');
    await expect(page.locator('#gauges .svg-gauge')).toHaveCount(1);
    await expect(page.locator('#gauge-pages .page-pill')).toHaveText(['Main']); // the Dashboard's page isn't a gauge page
    await page.locator('button', { hasText: 'Edit Layout' }).click();
    await page.locator('button', { hasText: 'Save & Done' }).click();
    await expect.poll(async () => (await saved(mock)).v).toBe(4);
    const data = await saved(mock);
    expect(dashPageOf(data).items.map(g => g.type)).toEqual(['status', 'messages', 'terminal', 'actions', 'cansend']);
    const main = data.pages.find(p => p.name === 'Main');
    expect(main.items).toHaveLength(1);
    expect(main.items[0]).toMatchObject({ name: 'udc', type: 'radial', max: 500 });
  });

  test('a gauge moves from a gauge page onto the Dashboard', async ({ page, mock }) => {
    await mock.putFile('gauges.json', v3Layout);
    await openApp(page, mock);
    await gotoTab(page, 'Gauges');
    await page.locator('button', { hasText: 'Edit Layout' }).click();
    await page.locator('#gauges .gauge-tile').first().click();
    await page.locator('#tile-move').selectOption({ label: 'Dashboard' });
    await expect(page.locator('#gauges .gauge-tile')).toHaveCount(0);
    await page.locator('button', { hasText: 'Save & Done' }).click();
    await expect.poll(async () => dashPageOf(await saved(mock)).items.some(g => g.name === 'udc')).toBe(true);
    await gotoTab(page, 'Dashboard');
    await expect(page.locator('#dashboard .svg-gauge')).toHaveCount(1);
    await expect(page.locator('#dashboard .svg-gauge .g-val')).toContainText(/\d/);
  });

  test('a widget tile works on a gauge page too', async ({ page, mock }) => {
    await mock.putFile('gauges.json', { v: 3, pages: [{ id: 1, name: 'Main', items: [
      { id: 1, name: 'udc', type: 'metric', label: 'Pack', x: 0, y: 0, w: 3, h: 2 },
      { id: 2, type: 'actions', btns: ['stop'], x: 0, y: 2, w: 6, h: 1 },
    ] }] });
    await openApp(page, mock);
    await gotoTab(page, 'Gauges');
    const metric = page.locator('#gauges .widget-metric');
    await expect(metric.locator('.metric-label')).toHaveText('Pack');
    await expect(metric.locator('.metric-value')).toContainText(/^\d{3}\.\dV$/);
    const actions = page.locator('#gauges .widget-actions button');
    await expect(actions).toHaveText(['Stop inverter']); // only the chosen button
  });

  test('editing the Dashboard uses the gauge editor without page tools', async ({ page, mock }) => {
    await openApp(page, mock);
    await page.locator('#dashboard button', { hasText: 'Edit Layout' }).click();
    await expect(page.locator('h3', { hasText: 'Edit Dashboard' })).toBeVisible();
    await expect(page.locator('button', { hasText: 'New page' })).toHaveCount(0);
    await expect(page.locator('#gauge-pages')).toHaveCount(0);
    // A tap configures a widget instead of firing it
    await page.locator('#dashboard .widget-tile', { hasText: 'Start inverter' }).click();
    await expect(page.locator('.modal-content select').first()).toHaveValue('actions');
    expect(await mock.commands()).not.toContain('start 2');
  });

  test('on a phone the Dashboard stacks its tiles in reading order', async ({ page, mock }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await openApp(page, mock);
    const grid = page.locator('#dashboard .grid-stack');
    await expect(grid).toHaveClass(/stacked/);
    const tops = await page.locator('#dashboard .grid-stack-item').evaluateAll(els => els.map(e => e.getBoundingClientRect().top));
    expect(tops.length).toBe(5);
    // status first, the full-width terminal below it, the CAN sender last
    expect(tops[0]).toBeLessThan(tops[2]);
    expect(tops[2]).toBeLessThan(tops[4]);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
