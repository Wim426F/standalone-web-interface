/* global parseVer, verNewer, VERSION_TAG_RE -- data/app.js globals, used inside page.evaluate */
import { test, expect, openApp, gotoTab } from './fixtures.mjs';

// Stable / Dev update channels (build.yml publishes dev pre-releases as
// vX.Y-dev.N). Stable asks GitHub for /releases/latest (never a
// pre-release); Dev reads the whole /releases list and takes the newest
// version, pre-releases included. The fixtures abort real GitHub traffic;
// these tests fulfil the API routes with scripted payloads.

const json = (route, body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
const ota = (tag) => [
  { name: 'esp32_' + tag + '-ota.bin', browser_download_url: 'http://example.invalid/esp32_' + tag + '-ota.bin' },
  { name: 'esp32s3_' + tag + '-ota.bin', browser_download_url: 'http://example.invalid/esp32s3_' + tag + '-ota.bin' },
];
const rel = (tag, extra = {}) => ({ tag_name: tag, prerelease: /-dev\./.test(tag), draft: false, assets: ota(tag), ...extra });

// A releases page as GitHub orders it (newest created first): previews of
// v99.2 above the stable v99.1, plus a draft, a hostile tag and a non-version
// tag that must never win
const RELEASES = [
  rel('v99.3-dev.1', { draft: true }),
  rel('<img src=x onerror=alert(1)> v100.0', { prerelease: true }),
  rel('nightly', { prerelease: true }),
  rel('v99.2-dev.10'),
  rel('v99.2-dev.9'),
  rel('v99.1'),
  rel('v99.2-dev.2'),
];

/** Count and answer both API shapes: /releases/latest and the /releases list. */
async function routeGithub(page, { latest = 'v99.1', list = RELEASES } = {}) {
  const calls = { latest: 0, list: 0 };
  await page.route('https://api.github.com/repos/**/releases/latest', route => { calls.latest++; return json(route, { tag_name: latest }); });
  await page.route(/api\.github\.com\/repos\/[^/]+\/[^/]+\/releases(\?.*)?$/, route => { calls.list++; return json(route, list); });
  return calls;
}

const channelSelect = (page) => page.locator('#update-channel');
const releaseSelect = (page) => page.locator('#update select.styled').first();
const prefsOnDevice = (mock) => fetch(mock.url + '/uiprefs.json').then(r => r.ok ? r.json() : {}).catch(() => ({}));

test.describe('Version ordering', () => {
  test('stable, dev and git-describe versions sort as releases do', async ({ page, mock }) => {
    await openApp(page, mock);
    const order = [
      'v4.9', 'v4.23', 'v4.23-12-gabc1234', 'v4.24-dev.1', 'v4.24-dev.1-3-g0a1b2c3-dirty',
      'v4.24-dev.2', 'v4.24-dev.10', 'v4.24', 'v4.24.1', 'v4.25-dev.1', 'v5.0',
    ];
    const sorted = await page.evaluate((o) => [...o].reverse().sort((a, b) => {
      const ka = parseVer(a), kb = parseVer(b);
      return verNewer(ka, kb) ? 1 : verNewer(kb, ka) ? -1 : 0;
    }), order);
    expect(sorted).toEqual(order);
    // Release tags accepted from the API: stable and dev only, nothing else
    const accepted = await page.evaluate(() => ['v4.24', 'v4.24.1', 'v4.24-dev.3', 'v4.24-dev.0', 'v4.24-dev.3-2-gabc',
      'v4.24-rc1', 'nightly', '<b>v4.24</b>', 'v4.24-dev.3 '].filter(t => VERSION_TAG_RE.test(t)));
    expect(accepted).toEqual(['v4.24', 'v4.24.1', 'v4.24-dev.3']);
  });
});

test.describe('Update channel', () => {
  test('Stable is the default: only /releases/latest is asked, pre-releases stay hidden', async ({ page, mock }) => {
    const calls = await routeGithub(page);
    await openApp(page, mock);
    await expect(page.locator('#update-badge')).toContainText('v99.1');
    expect(calls).toEqual({ latest: 1, list: 0 });
    await gotoTab(page, 'Update');
    await expect(channelSelect(page)).toHaveValue('stable');
    // The badge auto-loaded the list: stable releases only
    await expect(releaseSelect(page)).toHaveValue('v99.1');
    const tags = await releaseSelect(page).locator('option').allTextContents();
    expect(tags).toEqual(['v99.1']);
  });

  test('Dev offers the newest pre-release (numeric dev order, drafts and odd tags ignored)', async ({ page, mock }) => {
    const calls = await routeGithub(page);
    await openApp(page, mock);
    await expect(page.locator('#update-badge')).toContainText('v99.1');
    await gotoTab(page, 'Update');
    await channelSelect(page).selectOption('dev');
    // The check re-runs on the dev channel and the badge follows it
    await expect(page.locator('#update-badge')).toContainText('v99.2-dev.10');
    await expect(page.locator('#update-newer-note')).toContainText('v99.2-dev.10');
    expect(calls.latest).toBe(1); // not asked again on dev
    // The release list now carries the pre-releases, newest version first and
    // pre-selected, with this board's image; the draft is not offered
    await expect(releaseSelect(page)).toHaveValue('v99.2-dev.10');
    const tags = await releaseSelect(page).locator('option').allTextContents();
    expect(tags.slice(0, 4)).toEqual(['v99.2-dev.10', 'v99.2-dev.9', 'v99.2-dev.2', 'v99.1']);
    expect(tags).not.toContain('v99.3-dev.1');
    await expect(page.locator('#update select.styled').nth(1)).toHaveValue('http://example.invalid/esp32_v99.2-dev.10-ota.bin');
    // The hostile tag is only ever rendered as text
    expect(await page.locator('img[src="x"]').count()).toBe(0);
  });

  test('Dev moves on to the stable release that supersedes its previews', async ({ page, mock }) => {
    await routeGithub(page, { list: [rel('v99.2'), rel('v99.2-dev.3'), rel('v99.2-dev.2'), rel('v99.1')] });
    await fetch(mock.url + '/__test/otainfo', { method: 'POST', body: JSON.stringify({ version: 'v99.2-dev.3' }) });
    await page.addInitScript(() => { try { localStorage.setItem('updateChannel', 'dev'); } catch (e) {} });
    await openApp(page, mock, { version: 'v99.2-dev.3' });
    await expect(page.locator('#update-badge')).toContainText('v99.2 available');
    await gotoTab(page, 'Update');
    await expect(releaseSelect(page)).toHaveValue('v99.2');
  });

  test('a dev build is not nagged to "update" to an older stable release', async ({ page, mock }) => {
    await fetch(mock.url + '/__test/otainfo', { method: 'POST', body: JSON.stringify({ version: 'v99.2-dev.3-4-gabc1234' }) });
    const calls = await routeGithub(page, { latest: 'v99.1' });
    await openApp(page, mock, { version: 'v99.2-dev.3-4-gabc1234' });
    await expect.poll(() => calls.latest).toBe(1);
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 0)))));
    await expect(page.locator('#update-badge')).toHaveCount(0);
  });

  test('a stable release of the previewed version IS newer than its dev builds', async ({ page, mock }) => {
    await fetch(mock.url + '/__test/otainfo', { method: 'POST', body: JSON.stringify({ version: 'v99.2-dev.3' }) });
    await routeGithub(page, { latest: 'v99.2' });
    await openApp(page, mock, { version: 'v99.2-dev.3' });
    await expect(page.locator('#update-badge')).toContainText('v99.2 available');
  });

  test('the choice persists: across reloads and onto the device (uiprefs.json)', async ({ page, mock }) => {
    const calls = await routeGithub(page);
    await openApp(page, mock);
    await gotoTab(page, 'Update');
    await channelSelect(page).selectOption('dev');
    await expect.poll(async () => (await prefsOnDevice(mock)).updateChannel, { timeout: 5000 }).toBe('dev');
    expect(await page.evaluate(() => localStorage.getItem('updateChannel'))).toBe('dev');
    await page.reload();
    await expect(page.locator('#version')).toContainText('Web: v0.1-mock');
    await gotoTab(page, 'Update');
    await expect(channelSelect(page)).toHaveValue('dev');
    // ...and back: Stable drops the pre-releases from the list again
    await channelSelect(page).selectOption('stable');
    await expect(page.locator('#update-badge')).toContainText('v99.1');
    await expect.poll(async () => (await prefsOnDevice(mock)).updateChannel, { timeout: 5000 }).toBe('stable');
    await page.locator('button', { hasText: 'Get releases' }).click();
    await expect(releaseSelect(page).locator('option')).toHaveText(['v99.1']);
    expect(calls.latest).toBeGreaterThanOrEqual(2);
  });

  test('a fresh browser follows the channel stored on the device', async ({ page, mock }) => {
    await mock.putFile('uiprefs.json', { theme: 'system', updateChannel: 'dev' });
    // A stale stable-channel answer cached in this browser must not be reused
    await page.addInitScript(() => {
      try { localStorage.setItem('updateCheck', JSON.stringify({ tag: 'v99.1', ok: true, checkedAt: Date.now() })); } catch (e) {}
    });
    const calls = await routeGithub(page);
    await openApp(page, mock);
    await expect(page.locator('#update-badge')).toContainText('v99.2-dev.10');
    expect(calls.list).toBe(1);
    await gotoTab(page, 'Update');
    await expect(channelSelect(page)).toHaveValue('dev');
  });

  test('Stable with only pre-releases published says where they are', async ({ page, mock }) => {
    await routeGithub(page, { latest: 'v0.0', list: [rel('v99.2-dev.1')] });
    await openApp(page, mock);
    await gotoTab(page, 'Update');
    await page.locator('button', { hasText: 'Get releases' }).click();
    await expect(page.locator('#update')).toContainText('Dev update channel');
    await channelSelect(page).selectOption('dev');
    await expect(releaseSelect(page)).toHaveValue('v99.2-dev.1');
  });
});
