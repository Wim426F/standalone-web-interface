// Smoke test for the live demo (demo/build.mjs): the real UI, built as a
// static site and served under a sub-path like GitHub Pages, must come up on
// the simulated inverter with no device behind it.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test, expect, gotoTab } from './fixtures.mjs';
import { buildDemoSite } from '../../demo/build.mjs';
import { serveStatic } from '../../demo/serve.mjs';

let server;
test.beforeAll(async () => {
  const site = buildDemoSite(fs.mkdtempSync(path.join(os.tmpdir(), 'oi-demo-e2e-')));
  server = await serveStatic(site, { base: '/esp32-web-interface/' });
});
test.afterAll(async () => { if (server) await server.close(); });

async function openDemo(page, query = '') {
  // Any request that escapes the demo transport to the device's own paths
  // would 404 on Pages: fail loudly instead
  const escaped = [];
  page.on('response', r => { if (r.status() >= 400) escaped.push(r.status() + ' ' + r.url()); });
  await page.goto(server.url + query);
  await expect(page.locator('#oi-demo-banner')).toContainText('simulated inverter');
  await expect(page.locator('#version')).toContainText('Web: v1.12');
  return escaped;
}

test('dashboard runs on the simulated inverter, with the demo banner', async ({ page }) => {
  const escaped = await openDemo(page);
  await expect(page.locator('.hero-state')).toHaveText('Running');
  await expect(page.locator('#device-name')).toHaveText('Demo Roadster');
  await expect(page.locator('.hero-metrics .metric-value').first()).toHaveText(/^\d{3}\.\dV$/);
  await page.locator('#commandinput').fill('get udc');
  await page.locator('#commandinput').press('Enter');
  await expect(page.locator('#commandoutput')).toContainText(/> get udc\s+\d{3}\.\d\d/);
  expect(escaped).toEqual([]);
});

test('main tabs render with data', async ({ page }) => {
  const escaped = await openDemo(page);
  await gotoTab(page, 'Parameters');
  await expect(page.locator('#params')).toContainText('udcmin');
  await gotoTab(page, 'Spot Values');
  await expect(page.locator('#spotValues')).toContainText('udc');
  await gotoTab(page, 'Gauges');
  await expect(page.locator('#gauges')).toContainText('Driving');
  await expect(page.locator('#gauges')).toContainText('rpm');
  await gotoTab(page, 'Plot');
  await page.getByRole('button', { name: 'Start' }).click();
  await expect(page.getByRole('button', { name: 'Stop' })).toBeVisible();
  await gotoTab(page, 'Settings');
  await expect(page.locator('#content')).toContainText('garage-wifi');
  expect(escaped).toEqual([]);
});

test('rebooting is harmless and says why', async ({ page }) => {
  await openDemo(page);
  await page.getByRole('button', { name: 'Reboot ESP32' }).click();
  await page.getByRole('button', { name: 'Confirm', exact: true }).click();
  await expect(page.locator('#oi-demo-banner')).toContainText('Demo mode: nothing was rebooted');
  await expect(page.locator('.hero-state')).toHaveText('Running');
});

test('CAN mode lists the device mappings', async ({ page }) => {
  await openDemo(page, '?can=1#canmapping');
  await expect(page.locator('#content')).toContainText('0x3E8');
  await expect(page.locator('#oi-demo-banner')).toContainText('UART mode');
});

test('updating from GitHub is harmless and says why', async ({ page }) => {
  await openDemo(page, '#update');
  await page.getByRole('button', { name: /get releases/i }).click();
  await expect(page.locator('#update select').first()).toContainText('v1.12');
  page.on('dialog', d => d.accept());
  await page.getByRole('button', { name: /download & install/i }).click();
  await expect(page.locator('#oi-demo-banner')).toContainText('Demo mode: no image was downloaded or flashed');
});
