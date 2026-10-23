import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test as base, expect, gotoTab } from './fixtures.mjs';

// The UI against the PC backend (pyapp/): the real Python server, started
// with --simulate so a simulated inverter answers on a virtual CAN bus. This
// drives the actual server rather than a stand-in, so the contract between
// app.js and server.py is what gets tested. Needs Python with python-can
// (pip install ./pyapp); without it the spec is skipped locally and fails in CI.

const PYTHON = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');

function startPcBackend() {
  const dataDir = mkdtempSync(join(tmpdir(), 'oi-pc-'));
  const proc = spawn(PYTHON, ['-u', '-m', 'openinverter_web', '--simulate', '--port', '0', '--no-browser', '--data-dir', dataDir],
    { cwd: join(import.meta.dirname, '..', '..', 'pyapp'), stdio: ['ignore', 'pipe', 'pipe'] });
  const stop = () => { proc.kill(); rmSync(dataDir, { recursive: true, force: true }); };
  return new Promise((resolve, reject) => {
    let out = '';
    const fail = (why) => { clearTimeout(timer); stop(); reject(new Error(why + '\n' + out)); };
    const timer = setTimeout(() => fail('PC backend did not start within 15 s'), 15000);
    const onData = (d) => {
      out += d;
      const m = out.match(/Serving (http:\/\/\S+?)\/?\s/);
      if (m) { clearTimeout(timer); resolve({ url: m[1], stop }); }
    };
    proc.stdout.on('data', onData);
    proc.stderr.on('data', onData);
    proc.on('error', (e) => fail('could not run ' + PYTHON + ': ' + e.message));
    proc.on('exit', (code) => fail('PC backend exited with code ' + code));
  });
}

const test = base.extend({
  pc: async ({}, use) => {
    let pc;
    try {
      pc = await startPcBackend();
    } catch (e) {
      if (process.env.CI) throw e;
      test.skip(true, 'PC backend unavailable (pip install ./pyapp): ' + e.message.split('\n')[0]);
    }
    await use(pc);
    pc.stop();
  },
});

const settings = (pc) => fetch(pc.url + '/settings').then(r => r.json());

async function openPc(page, pc) {
  await page.goto(pc.url);
  await expect(page.locator('#version')).toContainText('-pc');
}

test.describe('PC backend', () => {
  test('loads live values from the simulated inverter and nothing is refused', async ({ page, pc }) => {
    // The server refuses cross-origin requests; its own page must never hit that
    const refused = [];
    page.on('response', r => { if (r.status() === 403) refused.push(r.url()); });
    await openPc(page, pc);
    await gotoTab(page, 'Spot Values');
    await expect(page.locator('#spotValues tr', { hasText: 'udc' })).toContainText(/\b(39\d|40\d)\.\d/);
    expect(refused).toEqual([]);
  });

  test('editing a parameter reaches the inverter over CAN', async ({ page, pc }) => {
    await openPc(page, pc);
    await gotoTab(page, 'Parameters');
    const input = page.locator('#params tr', { hasText: 'fweak' }).locator('input[type="number"]');
    await expect(input).toHaveValue('67');
    await input.fill('72');
    await input.press('Enter');
    await expect.poll(() => fetch(pc.url + '/cmd?cmd=get%20fweak').then(r => r.text())).toMatch(/^72(\.0+)?\s*$/);
  });

  test('Settings offers the CAN adapter picker instead of ESP pins and accounts', async ({ page, pc }) => {
    await openPc(page, pc);
    await gotoTab(page, 'Settings');
    await expect(page.locator('#pc-can-interface')).toBeVisible();
    await expect(page.getByText('CAN via python-can')).toBeVisible();
    await expect(page.locator('#iface-seg')).toHaveCount(0);           // no UART/CAN switch
    await expect(page.locator('#settings-users-tab')).toHaveCount(0);  // no sign-in on the PC
    await page.locator('#pc-can-bitrate').selectOption('250000');
    await page.locator('#iface-save').click();
    await expect.poll(async () => (await settings(pc)).can_bitrate).toBe(250000);
  });

  test('Update tab points at pip instead of ESP32 OTA', async ({ page, pc }) => {
    await openPc(page, pc);
    await gotoTab(page, 'Update');
    await expect(page.getByText('pip install --upgrade openinverter-web')).toBeVisible();
    await expect(page.getByText('Daily update check')).toHaveCount(0);
  });
});
