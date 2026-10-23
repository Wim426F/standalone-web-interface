import { test, expect, openApp, gotoTab } from './fixtures.mjs';

// Touch scrolling on a phone. The page-swipe surface once set
// touch-action: none on every tile, which was harmless while gauge pages
// always fitted the screen; once the Dashboard and phone gauge pages became
// taller than the screen, a drag that started on a tile no longer scrolled
// (one that started in a gap did — "sometimes scrolling doesn't work").
//
// Headless Chromium doesn't scroll from synthesized touch gestures at all
// (not even a bare page), so this checks the cause directly: every element
// from a tile's innermost content up to the page's scroll container must
// let the browser pan vertically.

test.use({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });

const tall = (base, n) => Array.from({ length: n }, (_, i) => (
  { id: base + i, name: 'udc', type: 'radial', min: 0, max: 500, x: 0, y: i * 4, w: 4, h: 4 }));
const twoTallPages = { v: 3, pages: [
  { id: 1, name: 'A', items: tall(1, 8) },
  { id: 2, name: 'B', items: tall(100, 8) },
] };

// touch-action values on the way up that would stop a vertical drag
const blockers = (page, sel) => page.evaluate((s) => {
  const vertical = (v) => v === 'auto' || v === 'manipulation' || /pan-y/.test(v);
  const bad = [];
  for (const start of document.querySelectorAll(s)) {
    for (let el = start; el && el !== document.body; el = el.parentElement) {
      const ta = getComputedStyle(el).touchAction;
      if (!vertical(ta)) bad.push((el.className.baseVal ?? el.className) + ': ' + ta);
      const oy = getComputedStyle(el).overflowY;
      if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 1) break; // reached the scroller
    }
  }
  return [...new Set(bad)];
}, sel);

test.describe('Touch scrolling on a phone', () => {
  test('nothing on the Dashboard stops a vertical drag from scrolling', async ({ page, mock }) => {
    await openApp(page, mock);
    await expect(page.locator('#dashboard .gauge-tile').first()).toBeVisible();
    expect(await blockers(page, '#dashboard .gauge-tile, #dashboard .gauge-tile *')).toEqual([]);
  });

  test('a gauge page with several pages scrolls from its tiles and still swipes', async ({ page, mock }) => {
    await mock.putFile('gauges.json', twoTallPages);
    await openApp(page, mock);
    await gotoTab(page, 'Gauges');
    await expect(page.locator('#gauges .svg-gauge').first()).toBeVisible();
    expect(await page.locator('#gauges .main-left').getAttribute('class')).toContain('gauge-swipe');
    expect(await blockers(page, '#gauges .gauge-tile, #gauges .gauge-tile *')).toEqual([]);
    // The page really is taller than the screen, so scrolling matters here
    expect(await page.locator('#gauges .main-left').evaluate(e => e.scrollHeight > e.clientHeight)).toBe(true);
  });
});
