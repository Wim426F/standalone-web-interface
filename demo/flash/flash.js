// Installer page: Stable/Dev channel switch for the ESP Web Tools button.
// The deploy workflow drops manifest-stable.json / manifest-dev.json and the
// release images beside this page (demo/flash-firmware.mjs), so flashing
// never leaves this site.
(function () {
  'use strict';
  const button = document.getElementById('installer');
  const notes = { stable: 'Tested releases.', dev: 'Pre-releases built from every change on the dev branch: newer, less tested.' };
  const versions = {};

  const load = (ch) => fetch('manifest-' + ch + '.json', { cache: 'no-store' })
    .then(r => (r.ok ? r.json() : null))
    .then(m => {
      versions[ch] = m && m.version;
      document.getElementById('ver-' + ch).textContent = versions[ch] || 'not available';
      return !!versions[ch];
    })
    .catch(() => false);

  const pick = (ch) => {
    button.setAttribute('manifest', 'manifest-' + ch + '.json');
    document.getElementById('channel-note').textContent = notes[ch] +
      (versions[ch] ? ' Installs ' + versions[ch] + '.' : '');
    const activate = button.querySelector('button[slot="activate"]');
    if (activate) activate.disabled = !versions[ch];
    try { localStorage.setItem('oi-flash-channel', ch); } catch (e) {}
  };

  Promise.all([load('stable'), load('dev')]).then(([stable, dev]) => {
    document.getElementById('manifest-error').hidden = stable || dev;
    let ch = 'stable';
    try { if (localStorage.getItem('oi-flash-channel') === 'dev') ch = 'dev'; } catch (e) {}
    if (!versions[ch]) ch = stable ? 'stable' : (dev ? 'dev' : 'stable');
    const radio = document.querySelector('#channel input[value="' + ch + '"]');
    if (radio) radio.checked = true;
    pick(ch);
  });

  document.getElementById('channel').addEventListener('change', (e) => {
    if (e.target && e.target.name === 'channel') pick(e.target.value);
  });
})();
