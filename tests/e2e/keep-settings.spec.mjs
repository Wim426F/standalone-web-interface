import { test, expect, openApp, gotoTab } from './fixtures.mjs';

// A web-interface OTA rewrites the whole filesystem. Device config
// (settings.json, uiprefs.json) is kept in NVS by the firmware; the UI
// stashes the layouts in the browser before flashing and puts back the ones
// the update erased. The mock's /espupdate wipes files the same way.

const GAUGES = { pages: [{ name: 'Track', items: [{ name: 'udc', type: 'radial' }] }] };
const PRESETS = { presets: [{ id: 1, name: 'Winter', params: { fweak: 70 } }] };
const PREFS = { theme: 'light', accentColor: '#ff8800' };

async function deviceFile(mock, name) {
  const r = await fetch(mock.url + '/' + name);
  return r.ok ? r.json() : null;
}

test.describe('Settings kept across a web-interface update', () => {
  test('layouts are restored after the update, device config is kept', async ({ page, mock }) => {
    await mock.putFile('gauges.json', JSON.stringify(GAUGES));
    await mock.putFile('presets.json', JSON.stringify(PRESETS));
    await mock.putFile('uiprefs.json', JSON.stringify(PREFS));
    await openApp(page, mock);
    await gotoTab(page, 'Update');

    page.on('dialog', d => d.accept());
    await page.locator('#esp-ota-file').setInputFiles({
      name: 'esp32_v9.9-ota.bin', mimeType: 'application/octet-stream', buffer: Buffer.from('OIWEBOTA-test-image'),
    });
    // The device took the image and wiped the layouts, as a real update does
    await expect.poll(async () => (await mock.state()).otaFlashes).toBe(1);
    const after = (await mock.state()).files;
    expect(after).not.toContain('gauges.json');
    expect(after).toContain('uiprefs.json'); // kept by the firmware (NVS)

    // Next load (the UI reloads itself after the reboot): layouts come back
    await page.reload();
    await expect(page.locator('body')).toContainText('Restored 2 layout files after the update');
    await expect.poll(() => deviceFile(mock, 'gauges.json')).toEqual(GAUGES);
    expect(await deviceFile(mock, 'presets.json')).toEqual(PRESETS);
    expect(await deviceFile(mock, 'uiprefs.json')).toEqual(PREFS);
    // The stash is used once, then dropped
    expect(await page.evaluate(() => localStorage.getItem('oi-update-restore'))).toBeNull();
  });

  test('a file already on the device is newer and is not overwritten', async ({ page, mock }) => {
    await openApp(page, mock);
    const newer = { pages: [{ name: 'Newer', items: [] }] };
    await mock.putFile('gauges.json', JSON.stringify(newer));
    await page.evaluate(g => localStorage.setItem('oi-update-restore',
      JSON.stringify({ at: Date.now(), files: { 'gauges.json': JSON.stringify(g) } })), GAUGES);
    await page.reload();
    await expect.poll(() => page.evaluate(() => localStorage.getItem('oi-update-restore'))).toBeNull();
    expect(await deviceFile(mock, 'gauges.json')).toEqual(newer);
  });

  test('a stale stash (older than a day) is discarded, not restored', async ({ page, mock }) => {
    await openApp(page, mock);
    await page.evaluate(g => localStorage.setItem('oi-update-restore',
      JSON.stringify({ at: Date.now() - 2 * 24 * 3600 * 1000, files: { 'gauges.json': JSON.stringify(g) } })), GAUGES);
    await page.reload();
    await expect.poll(() => page.evaluate(() => localStorage.getItem('oi-update-restore'))).toBeNull();
    expect((await mock.state()).files).not.toContain('gauges.json');
  });

  test('Reset device settings restores defaults but keeps layouts', async ({ page, mock }) => {
    await mock.putFile('gauges.json', JSON.stringify(GAUGES));
    await mock.putFile('uiprefs.json', JSON.stringify(PREFS));
    await fetch(mock.url + '/settings', { method: 'POST', body: 'dev_name=Landy', headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
    await openApp(page, mock);
    await gotoTab(page, 'Settings');
    await page.locator('.page-pill', { hasText: 'Configuration' }).click();
    page.on('dialog', d => d.accept());
    await page.locator('#reset-settings').click();
    await expect.poll(async () => (await mock.state()).settings.dev_name).toBe('');
    const files = (await mock.state()).files;
    expect(files).not.toContain('uiprefs.json');
    expect(files).toContain('gauges.json');
  });
});
