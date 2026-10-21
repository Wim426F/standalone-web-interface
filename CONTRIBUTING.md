# Contributing

Thanks for contributing! This repo has an automated test suite that runs on
every pull request — here's how to work with it locally.

## Setup

- [Node.js](https://nodejs.org/) 20+ (for lint + E2E tests)
- [PlatformIO](https://platformio.org/) (to build/flash the firmware)
- Python 3 (asset tooling; ships with PlatformIO)

```sh
npm install
npx playwright install chromium
```

## Running the checks

Everything CI runs, in one go:

```sh
npm test
```

Or individually:

| Command | What it does |
|---|---|
| `npm run lint` | ESLint over `data/app.js`, `data/sw.js` and the test suite. Catches typo'd identifiers — the app is a no-build global-scope script, so these otherwise only explode at runtime. |
| `npm run check:gz` | Fails if any `data/*.gz` is stale. If you edit a file in `data/`, regenerate its `.gz` twin with `python gzip_assets.py` (any `pio run` does it too) and commit both. |
| `npm run test:e2e` | Playwright drives the real UI in headless Chromium against a **mock ESP** (`tests/mock-esp/server.mjs`) that implements the firmware's HTTP API and a simulated inverter speaking the protocol in `PROTOCOL.md`. |
| `npm run mock` | Runs the mock ESP at http://127.0.0.1:8765 so you can develop the UI with no hardware. |
| `pio test -e native` | Host unit tests for the firmware's pure logic in `src/web_logic.h` (query parsing, input validation, OTA header, UART reply framing, fuzz loops). Needs a host gcc/g++ — on Windows use WSL or Docker. `-e native_asan` runs them under AddressSanitizer + UBSan (Linux/macOS). |

`npm test` doesn't include the native tests. CI also runs `npm audit --audit-level=high`, and CodeQL over the C/C++ firmware, the JavaScript and the workflows. Dependabot raises alerts only (no automatic PRs).

**Firmware logic that parses untrusted input** (HTTP query strings, uploads, settings files, UART replies) belongs in `src/web_logic.h`, with tests in `test/test_web_logic`. Keep it free of hardware calls and globals so it builds on the host.

## Writing E2E tests

Specs live in `tests/e2e/*.spec.mjs`. Use the shared fixtures:

```js
import { test, expect, openApp, gotoTab } from './fixtures.mjs';

test('my feature', async ({ page, mock }) => {
  await openApp(page, mock);      // navigates + waits for first data fetch
  await gotoTab(page, 'Parameters');
  // drive the UI ...
  expect(await mock.commands()).toContain('set fweak 42'); // assert what reached the inverter
});
```

- Each test gets its **own mock ESP** on a random port — no shared state.
- Any uncaught browser error fails the test automatically.
- `mock.commands()` returns every serial command the "inverter" received;
  `mock.state()` dumps parameters, uploaded files and settings — assert
  effects, not just UI appearance.

If you add UI behaviour, add a spec. If you fix a bug, add the spec that
would have caught it.

- The mock must answer like the firmware does: when a handler in
  `esp32-web-interface.ino` changes its reply, update the mock and
  `tests/e2e/mock-contract.spec.mjs` with it. `mock.enableCan()` switches it
  to the CAN backend's reply formats; the SSE stream runs on its own port
  (`mock.ssePort`), like the firmware's :81 listener.
- Wait on state, not time: `expect(...)`/`expect.poll(...)` on the DOM or
  `mock.state()`. Locally tests run with no retries so flakes show up; CI
  retries twice and reports passes-on-retry as flaky.
- A known app bug gets a `test.fixme` with a comment naming the code, so it
  is tracked and flips to a real test once fixed.

## Firmware changes

`tests.yml` compiles all four PlatformIO environments (release and debug for
`esp32_wemos` and `esp32_t2can`) and builds the SPIFFS image on every PR — a
change that doesn't compile for any of them will fail CI. `build.yml` builds
the release images. Test on real
hardware where you can; note in the PR which backend (UART / CAN) you tested.

## Branches and releases

- **`dev`** is the working branch. Open PRs against it (or push to it);
  every push and PR runs the full suite (lint, E2E, firmware compile matrix)
  and builds the images as workflow artifacts.
- **`main`** is the release branch. Changes reach it by a PR from `dev`, which
  must pass the required checks. Every push to `main` runs the full suite
  again and, if it's green, automatically tags the next `vX.Y` (minor + 1),
  builds full-flash and OTA images for both boards and publishes them as a
  GitHub release with generated notes (edit the notes afterwards if needed).
- Put `[skip release]` in the head commit message to land on `main` without
  releasing. Pushing a `vX.Y` tag by hand still releases that exact tag.
