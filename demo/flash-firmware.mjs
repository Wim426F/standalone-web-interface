// Bundles release images into the built site for the installer page
// (demo/flash/): the newest stable release and the newest release of any kind
// (the Dev channel, which moves on to a stable release that's newer than the
// last pre-release), each as ESP Web Tools manifests beside the page. Served
// from the same origin, so the browser can fetch them; GitHub's release
// downloads can't be read cross-origin.
//
//   node demo/flash-firmware.mjs [siteDir]      (default: _site; GH_TOKEN optional)
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REPO = process.env.GITHUB_REPOSITORY || 'wjcloudy/esp32-web-interface';
const IMAGE = /^(esp32|esp32s3)_(.+)-0x000\.bin$/;
const CHIP = { esp32: 'ESP32', esp32s3: 'ESP32-S3' };

const headers = { Accept: 'application/vnd.github+json', 'User-Agent': 'oi-flash-bundle' };
if (process.env.GH_TOKEN) headers.Authorization = 'Bearer ' + process.env.GH_TOKEN;

async function json(url) {
  const r = await fetch(url, { headers });
  if (!r.ok) throw new Error(url + ': HTTP ' + r.status);
  return r.json();
}

// Full-flash images of one release, downloaded once per tag
async function bundle(release, flashDir) {
  const tag = release.tag_name;
  if (!/^v[0-9][0-9A-Za-z.-]*$/.test(tag)) throw new Error('unexpected tag ' + tag);
  const builds = [];
  for (const asset of release.assets) {
    const m = IMAGE.exec(asset.name);
    if (!m) continue;
    const rel = 'fw/' + tag + '/' + asset.name;
    const dest = path.join(flashDir, rel);
    if (!fs.existsSync(dest)) {
      const r = await fetch(asset.browser_download_url, { headers: { 'User-Agent': headers['User-Agent'] } });
      if (!r.ok) throw new Error(asset.name + ': HTTP ' + r.status);
      fs.mkdirSync(path.dirname(dest), { recursive: true });
      fs.writeFileSync(dest, Buffer.from(await r.arrayBuffer()));
    }
    builds.push({ chipFamily: CHIP[m[1]], parts: [{ path: rel, offset: 0 }] });
  }
  if (!builds.length) throw new Error(tag + ' has no *-0x000.bin images');
  return {
    name: 'OpenInverter Web Interface',
    version: tag,
    new_install_prompt_erase: true,
    new_install_improv_wait_time: 0, // the firmware doesn't speak Improv
    builds: builds.sort((a, b) => a.chipFamily.localeCompare(b.chipFamily)),
  };
}

export async function bundleFirmware(site = path.join(ROOT, '_site')) {
  const flashDir = path.join(site, 'flash');
  const releases = (await json(`https://api.github.com/repos/${REPO}/releases?per_page=30`))
    .filter(r => !r.draft && r.assets.some(a => IMAGE.test(a.name)));
  const byDate = (a, b) => new Date(b.published_at) - new Date(a.published_at);
  const stable = releases.filter(r => !r.prerelease).sort(byDate)[0];
  const newest = releases.slice().sort(byDate)[0];
  const out = {};
  if (stable) out.stable = await bundle(stable, flashDir);
  if (newest) out.dev = newest === stable ? out.stable : await bundle(newest, flashDir);
  for (const [ch, manifest] of Object.entries(out)) {
    fs.writeFileSync(path.join(flashDir, 'manifest-' + ch + '.json'), JSON.stringify(manifest, null, 2));
  }
  return { stable: out.stable && out.stable.version, dev: out.dev && out.dev.version };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const site = process.argv[2] ? path.resolve(process.argv[2]) : undefined;
  const got = await bundleFirmware(site);
  console.log('installer images: stable ' + (got.stable || 'none') + ', dev ' + (got.dev || 'none'));
}
