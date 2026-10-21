import { test, expect, openApp, gotoTab } from './fixtures.mjs';

// Plot tab: one combined 'get' per fetch, with &repeat=Burst so the firmware
// returns Burst+1 sample groups per request (UART). Each group must become
// one chart point per series — never a partial/shifted group.

const seedPlots = (mock, plots, extra = {}) =>
  mock.putFile('plots.json', { plots, maxValues: 500, burstLength: 5, ...extra });

// Points per dataset of every chart on the Plot tab
const chartPoints = (page) => page.evaluate(() =>
  [...document.querySelectorAll('#plot canvas')].map(c => {
    const ch = window.Chart.getChart(c);
    return ch ? ch.data.datasets.map(d => d.data.length) : [];
  }));

const chartYs = (page, ds = 0) => page.evaluate((ds) => {
  const ch = window.Chart.getChart(document.querySelector('#plot canvas'));
  return ch ? ch.data.datasets[ds].data.map(p => p.y) : [];
}, ds);

// Capture the /cmd get requests the plotter issues
function trackGets(page) {
  const urls = [];
  page.on('request', r => {
    const u = new URL(r.url());
    if (u.pathname === '/cmd' && (u.searchParams.get('cmd') || '').startsWith('get ')) urls.push(u);
  });
  return urls;
}

const startButton = (page) => page.locator('#plot button', { hasText: /^(Start|Stop)$/ });

test.describe('Plot tab', () => {
  test('Burst>1 fetches N+1 sample groups per request and plots every group', async ({ page, mock }) => {
    await seedPlots(mock, [{ id: 1, items: [{ name: 'udc', axis: 'left' }, { name: 'tmphs', axis: 'right' }] }], { burstLength: 4 });
    const gets = trackGets(page);
    await openApp(page, mock);
    await gotoTab(page, 'Plot');
    await expect(page.locator('#plot canvas')).toHaveCount(1);
    await startButton(page).click();
    await expect(startButton(page)).toHaveText('Stop');

    // Points arrive in whole groups of burst+1 = 5, both series in lockstep
    await expect.poll(async () => (await chartPoints(page))[0][0]).toBeGreaterThanOrEqual(10);
    const [[a, b]] = await chartPoints(page);
    expect(a).toBe(b);
    expect(a % 5).toBe(0);

    const plotGets = gets.filter(u => u.searchParams.get('cmd') === 'get udc,tmphs');
    expect(plotGets.length).toBeGreaterThan(0);
    expect(plotGets.every(u => u.searchParams.get('repeat') === '4')).toBe(true);

    // Values land on the right series
    expect(new Set(await chartYs(page, 0))).toEqual(new Set([398.5]));
    expect(new Set(await chartYs(page, 1))).toEqual(new Set([31.2]));
  });

  test('live value changes show up in the plotted series', async ({ page, mock }) => {
    await seedPlots(mock, [{ id: 1, items: [{ name: 'udc' }] }]);
    await openApp(page, mock);
    await gotoTab(page, 'Plot');
    await startButton(page).click();
    await expect.poll(async () => (await chartYs(page)).length).toBeGreaterThan(0);
    await mock.setSpot('udc', 123.4);
    await expect.poll(async () => (await chartYs(page)).includes(123.4)).toBe(true);
  });

  test('Stop halts sampling', async ({ page, mock }) => {
    await seedPlots(mock, [{ id: 1, items: [{ name: 'udc' }] }]);
    const gets = trackGets(page);
    await openApp(page, mock);
    await gotoTab(page, 'Plot');
    await startButton(page).click();
    await expect.poll(async () => (await chartPoints(page))[0][0]).toBeGreaterThan(0);
    await startButton(page).click();
    await expect(startButton(page)).toHaveText('Start');
    // Let any in-flight fetch land, then the count must stay put. The loop
    // re-arms every 100 ms, so ~10 loop periods of silence proves it stopped.
    let settled = -1;
    await expect.poll(async () => {
      const n = (await chartPoints(page))[0][0];
      const same = n === settled; settled = n; return same;
    }, { intervals: [300] }).toBe(true);
    const getsAfterStop = gets.length;
    await page.waitForTimeout(1000); // negative check: nothing may arrive
    expect(gets.length).toBe(getsAfterStop);
    expect((await chartPoints(page))[0][0]).toBe(settled);
  });

  test('Burst setting saves to plots.json and changes the repeat count', async ({ page, mock }) => {
    await seedPlots(mock, [{ id: 1, items: [{ name: 'udc' }] }]);
    const gets = trackGets(page);
    await openApp(page, mock);
    await gotoTab(page, 'Plot');
    await page.locator('#plot button', { hasText: 'Edit Layout' }).click();
    const burst = page.locator('#plot label', { hasText: 'Burst' }).locator('xpath=following-sibling::input[1]');
    await burst.fill('2');
    await page.locator('#plot button', { hasText: 'Save & Done' }).click();
    await expect.poll(async () => {
      const r = await fetch(mock.url + '/plots.json').then(r => r.json());
      return r.burstLength;
    }).toBe(2);
    await startButton(page).click();
    await expect.poll(async () => (await chartPoints(page))[0][0]).toBeGreaterThanOrEqual(6);
    expect((await chartPoints(page))[0][0] % 3).toBe(0);
    expect(gets.filter(u => u.searchParams.get('cmd') === 'get udc').every(u => u.searchParams.get('repeat') === '2')).toBe(true);
  });

  test('a name list over the 128-char UART limit falls back to chunked single samples', async ({ page, mock }) => {
    // 10 long names: 'get ' + 10*20 chars ≫ 128 — one combined get would be
    // truncated by the firmware and every series would mis-map
    const names = Array.from({ length: 10 }, (_, i) => 'very_long_value_nm' + String(i).padStart(2, '0'));
    for (const [i, n] of names.entries()) await fetch(mock.url + `/__test/add-value?name=${n}&value=${i + 1}`);
    await seedPlots(mock, [{ id: 1, items: names.map(name => ({ name })) }]);
    const gets = trackGets(page);
    await openApp(page, mock);
    await gotoTab(page, 'Plot');
    await startButton(page).click();
    await expect.poll(async () => (await chartPoints(page))[0][0]).toBeGreaterThanOrEqual(2);
    // Every request fits the terminal buffer and carries no burst
    expect(gets.length).toBeGreaterThan(0);
    for (const u of gets) {
      expect(u.searchParams.get('cmd').length).toBeLessThanOrEqual(128);
      expect(u.searchParams.get('repeat')).toBeNull();
    }
    // ...and each series got its own value
    for (let i = 0; i < names.length; i++) expect(new Set(await chartYs(page, i))).toEqual(new Set([i + 1]));
  });

  test('CAN mode: repeat is ignored by the firmware, so each fetch is one point', async ({ page, mock }) => {
    await mock.enableCan();
    await seedPlots(mock, [{ id: 1, items: [{ name: 'udc' }, { name: 'tmphs' }] }]);
    await openApp(page, mock);
    await gotoTab(page, 'Plot');
    await startButton(page).click();
    await expect.poll(async () => (await chartPoints(page))[0][0]).toBeGreaterThanOrEqual(3);
    const [[a, b]] = await chartPoints(page);
    expect(a).toBe(b); // the tab-separated reply feeds both series
    expect(new Set(await chartYs(page, 0))).toEqual(new Set([398.5]));
    expect(new Set(await chartYs(page, 1))).toEqual(new Set([31.2]));
  });
});
