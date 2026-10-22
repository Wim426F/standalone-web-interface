// Builds the live demo: the real web interface from data/, as a static site
// that runs on demo/demo-data.js's simulated inverter (see demo/demo-esp.js).
//
//   node demo/build.mjs [outDir]        (default: _site)
//
// Served from any path (GitHub Pages puts it under /<repo>/): the page's own
// references are already relative, and the demo transport maps the app's
// root-relative device paths onto the site. No service worker and no web app
// manifest - the demo is a page, not an app to install - and search engines
// are asked to leave it out.
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DATA = path.join(ROOT, 'data');
const DEMO = path.join(ROOT, 'demo');

// Device-only pages and the PWA plumbing stay out of the demo
const SKIP = new Set(['sw.js', 'manifest.json']);

const BANNER_CSS = `
#oi-demo-banner{position:fixed;left:50%;bottom:14px;transform:translateX(-50%);z-index:10000;
  max-width:calc(100vw - 24px);padding:7px 16px;border-radius:999px;font:500 12.5px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  background:#f5b301;color:#1d1600;box-shadow:0 6px 24px rgba(0,0,0,.35);text-align:center}
#oi-demo-banner a{color:inherit;font-weight:700}
#oi-demo-banner .oi-demo-notice{font-weight:700;margin-top:2px}
@media (max-width:600px){#oi-demo-banner{left:0;right:0;bottom:0;transform:none;max-width:none;border-radius:0;padding:6px 10px}}`;

export function buildDemoSite(out = path.join(ROOT, '_site')) {
  fs.rmSync(out, { recursive: true, force: true });
  fs.mkdirSync(out, { recursive: true });
  // Directory entries carry their type, so there's no separate stat call
  // that the file could change after
  const entries = fs.readdirSync(DATA, { withFileTypes: true });
  const names = entries.map(e => e.name);
  for (const entry of entries) {
    const name = entry.name;
    const src = path.join(DATA, name);
    if (!entry.isFile() || SKIP.has(name)) continue;
    if (name.endsWith('.gz')) {
      // gz-only assets (vendored libraries) are served unpacked; the rest
      // are copies of a source file that is copied as-is
      const plain = name.slice(0, -3);
      if (!names.includes(plain) && !SKIP.has(plain)) fs.writeFileSync(path.join(out, plain), zlib.gunzipSync(fs.readFileSync(src)));
      continue;
    }
    fs.copyFileSync(src, path.join(out, name));
  }
  // Bundled web fonts live in a sub-folder
  fs.cpSync(path.join(DATA, 'fonts'), path.join(out, 'fonts'), { recursive: true });
  for (const name of ['demo-data.js', 'demo-esp.js']) fs.copyFileSync(path.join(DEMO, name), path.join(out, name));

  let page = fs.readFileSync(path.join(out, 'index.html'), 'utf8');
  page = page.replace(/<link rel="manifest"[^>]*>\s*/, '');
  const head = '<meta name="robots" content="noindex" />\n' +
    '<style>' + BANNER_CSS + '\n</style>\n' +
    // Before every app script: app.js asks the "device" for files as it loads
    '<script src="demo-data.js"></script>\n<script src="demo-esp.js"></script>\n';
  const at = page.indexOf('<link href="style.css"');
  if (at < 0) throw new Error('data/index.html: no style.css link to put the demo scripts before');
  page = page.slice(0, at) + head + page.slice(at);
  page = page.replace(/<title>[^<]*<\/title>/, '<title>OpenInverter web interface - live demo</title>');
  fs.writeFileSync(path.join(out, 'index.html'), page);
  // GitHub Pages would otherwise run the site through Jekyll
  fs.writeFileSync(path.join(out, '.nojekyll'), '');
  return out;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const out = buildDemoSite(process.argv[2] ? path.resolve(process.argv[2]) : undefined);
  console.log('demo site in ' + out);
}
