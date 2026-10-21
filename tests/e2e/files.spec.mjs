import { test, expect, openApp, gotoTab } from './fixtures.mjs';

test.describe('Files tab', () => {
  test('uploads a file to SPIFFS and lists it', async ({ page, mock }) => {
    await openApp(page, mock);
    await gotoTab(page, 'Files');
    await page.locator('#updatefile2').setInputFiles({
      name: 'canmap.json', mimeType: 'application/json', buffer: Buffer.from('{"x":1}'),
    });
    await expect(page.locator('tr', { hasText: 'canmap.json' })).toBeVisible();
    expect((await mock.state()).files).toContain('canmap.json');
  });

  test('deletes a file', async ({ page, mock }) => {
    await openApp(page, mock);
    await gotoTab(page, 'Files');
    await page.locator('#updatefile2').setInputFiles({
      name: 'todelete.txt', mimeType: 'text/plain', buffer: Buffer.from('bye'),
    });
    const row = page.locator('tr', { hasText: 'todelete.txt' });
    await expect(row).toBeVisible();
    page.on('dialog', d => d.accept());
    await row.locator('button', { hasText: 'Delete' }).click();
    await expect(row).toHaveCount(0);
    expect((await mock.state()).files).not.toContain('todelete.txt');
  });
  test('the list reflects files already on the device, and delete targets the listed name', async ({ page, mock }) => {
    await mock.putFile('a.json', '{}');
    await mock.putFile('b.bin', 'xx');
    const deletes = [];
    page.on('request', r => { if (r.method() === 'DELETE') deletes.push(new URL(r.url())); });
    await openApp(page, mock);
    await gotoTab(page, 'Files');
    await expect(page.locator('#files tbody tr')).toHaveCount(2);
    page.on('dialog', d => d.accept());
    await page.locator('#files tr', { hasText: 'a.json' }).locator('button', { hasText: 'Delete' }).click();
    await expect(page.locator('#files tbody tr')).toHaveCount(1);
    await expect(page.locator('#files tbody tr')).toContainText('b.bin');
    // The Files tab sends the bare SPIFFS name; firmware handleFileDelete
    // must accept it with or without the leading '/'
    expect(deletes.map(u => u.pathname + u.search)).toEqual(['/edit?f=a.json']);
    expect((await mock.state()).files).toEqual(['b.bin']);
  });

  test('uploading over an existing file replaces its content', async ({ page, mock }) => {
    await mock.putFile('notes.txt', 'old');
    await openApp(page, mock);
    await gotoTab(page, 'Files');
    await page.locator('#updatefile2').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('new') });
    await expect.poll(async () => fetch(mock.url + '/notes.txt').then(r => r.text())).toBe('new');
    await expect(page.locator('#files tbody tr', { hasText: 'notes.txt' })).toHaveCount(1);
  });
});
