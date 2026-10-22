import { test, expect } from './fixtures.mjs';

// remote.html loads openinverter.org's support script, which hands each
// helper command to inverter.sendCmd() (data/inverter.js) and posts the
// reply back. The real script long-polls openinverter.org; this stand-in
// relays a scripted list of commands instead, so nothing leaves the test.
const SUPPORT_STUB = `
var sent = [];
function listen() {
  var cmds = window.__supportCmds || [];
  (function next(i) {
    if (i >= cmds.length) { document.body.setAttribute('data-done', '1'); return; }
    inverter.sendCmd(cmds[i], function(reply) {
      sent.push(reply);
      var li = document.createElement('li');
      li.className = 'support-reply';
      li.textContent = cmds[i] + ' => ' + reply;
      document.getElementById('events').appendChild(li);
      next(i + 1);
    });
  })(0);
}`;

async function openRemote(page, mock, cmds) {
  await page.route('https://openinverter.org/support/ssdemo.js', r =>
    r.fulfill({ contentType: 'text/javascript', body: SUPPORT_STUB }));
  await page.addInitScript(c => { window.__supportCmds = c; }, cmds);
  await page.goto(mock.url + '/remote.html');
  await expect(page.locator('body')).toHaveAttribute('data-done', '1');
  return page.locator('.support-reply').allTextContents();
}

test.describe('Remote support page', () => {
  test('relays helper commands to the inverter (sign-in off)', async ({ page, mock }) => {
    const replies = await openRemote(page, mock, ['get udc', 'set fweak 72']);
    expect(replies[0]).toMatch(/^get udc => \d/);
    expect(replies[1]).not.toMatch(/Error/);
    await expect.poll(async () => (await mock.state()).inverter.params.fweak.value).toBe(72);
  });

  test("runs with the opener's role: a viewer serves reads, writes are refused", async ({ page, mock }) => {
    await mock.addUser('admin', 'admin-pass-1', 'admin');
    await mock.addUser('vera', 'viewer-pass', 'viewer');
    const { cookie } = await mock.signIn('vera', 'viewer-pass');
    const [name, value] = cookie.split(';')[0].split('=');
    await page.context().addCookies([{ name, value, url: mock.url }]);
    const before = (await mock.state()).inverter.params.fweak.value;

    const replies = await openRemote(page, mock, ['get udc', 'set fweak 73']);
    expect(replies[0]).toMatch(/^get udc => \d/);
    expect(replies[1]).toContain("isn't allowed");
    expect((await mock.state()).inverter.params.fweak.value).toBe(before);
  });

  test('tells the helper when nobody is signed in on the device', async ({ page, mock }) => {
    await mock.addUser('admin', 'admin-pass-1', 'admin');
    const replies = await openRemote(page, mock, ['get udc']);
    expect(replies[0]).toContain('not signed in');
  });
});
