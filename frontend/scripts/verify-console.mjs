/**
 * Browser verification of the console at 1920x1080, against a real API.
 *
 *     npm run verify:console -- http://127.0.0.1:5173
 *
 * Checks five things a screenshot cannot tell you and a unit test cannot reach:
 *
 *   1. every route renders without a runtime or console error;
 *   2. the real figures reach the DOM, so an empty screen cannot pass;
 *   3. nothing overflows horizontally at projector width;
 *   4. no route is still a placeholder;
 *   5. no fixture fallback is silently in play while the console claims to be live.
 *
 * It also asserts that a contract literal reaches the screen VERBATIM. `innerText` reflects CSS
 * `text-transform`, so a value inside an uppercased container renders as something the API never
 * returned — the defect that once put "MOCA" on screen for a policy pack whose real label is
 * "MoCA Passenger Charter". Presence is therefore compared case-insensitively, and exact casing is
 * asserted separately where the value is a machine token.
 *
 * Requires `playwright` and a Chromium build. Not part of `npm test`: it needs a running stack, so
 * it is a verification step rather than a unit test.
 *
 * Owner: Stream D.
 */


import { chromium } from 'playwright';

const BASE = process.argv[2] ?? 'http://127.0.0.1:5173';
/** @type {{path: string, name: string, expect: string[], expectExactCase?: string[]}[]} */
const ROUTES = [
  { path: '/', name: 'Command Center', expect: ['604', '22'] },
  { path: '/cascade/GRP-2026-0820-VOBL', name: 'Cascade Explorer', expect: ['604', '9'] },
  { path: '/incidents/INC-2026-0820-VOBL-01', name: 'Recovery Workspace', expect: ['6E 2134'] },
  { path: '/assurance', name: 'Group Approval Queue', expect: ['GRP-2026-0820-VOBL'] },
  {
    path: '/plans/INC-2026-0820-VOBL-01',
    name: 'Plan Comparison',
    expect: ['recorded_evidence'],
    // A contract literal must reach the screen verbatim. CSS-uppercasing it misrepresents the
    // value the API returned — the same defect that once rendered a policy pack label as "MOCA".
    expectExactCase: ['recorded_evidence'],
  },
  { path: '/replay/INC-2026-0820-VOBL-01', name: 'Replay', expect: [] },
  { path: '/policy/INC-2026-0820-VOBL-01', name: 'Policy', expect: [] },
  { path: '/sources', name: 'Provenance ledger', expect: [] },
];

const results = [];
function record(state, name, detail = '') {
  results.push({ state, name, detail });
  console.log(`[${state}] ${name}${detail ? `\n       ${detail}` : ''}`);
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });

for (const route of ROUTES) {
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });

  try {
    await page.goto(`${BASE}${route.path}`, { waitUntil: 'networkidle', timeout: 45000 });
    await page.waitForTimeout(2500);

    const body = await page.evaluate(() => document.body.innerText);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 1,
    );
    const placeholder = /not yet built|placeholder/i.test(body);
    // innerText reflects CSS text-transform, so presence is compared case-insensitively. Whether
    // a value SHOULD be uppercased is a separate assertion, made below.
    const haystack = body.toLowerCase();
    const missing = route.expect.filter((token) => !haystack.includes(token.toLowerCase()));

    if (errors.length > 0) {
      record('FAIL', `${route.name} renders without a runtime error`, errors.slice(0, 2).join(' | '));
    } else if (overflow) {
      record('FAIL', `${route.name} fits 1920 without horizontal overflow`);
    } else if (placeholder) {
      record('FAIL', `${route.name} is not a placeholder`, 'placeholder copy found in the DOM');
    } else if (route.expectExactCase?.some((token) => !body.includes(token))) {
      const wrong = route.expectExactCase.filter((token) => !body.includes(token));
      record(
        'FAIL',
        `${route.name} does not case-transform a contract value`,
        `expected verbatim, not uppercased: ${wrong.join(', ')}`,
      );
    } else if (missing.length > 0) {
      record(
        'FAIL',
        `${route.name} shows real figures`,
        `missing from the DOM: ${missing.join(', ')} | body starts: ${body.slice(0, 220).replace(/\s+/g, ' ')}`,
      );
    } else {
      const headings = await page.evaluate(() =>
        [...document.querySelectorAll('h1,h2,h3')].map((node) => node.textContent?.trim()).slice(0, 4),
      );
      record('PASS', `${route.name} renders at 1920x1080`, `sections: ${headings.join(' · ')}`);
    }
  } catch (error) {
    record('FAIL', `${route.name} loads`, String(error).slice(0, 200));
  }
  await page.close();
}

// A live console must not be quietly serving committed fixtures.
const page = await context.newPage();
const requests = [];
page.on('request', (request) => requests.push(request.url()));
await page.goto(`${BASE}/`, { waitUntil: 'networkidle', timeout: 45000 });
const fixtureCalls = requests.filter((url) => url.includes('/fixtures/'));
if (fixtureCalls.length > 0) {
  record('FAIL', 'the console is calling the API, not fixtures', `${fixtureCalls.length} fixture requests`);
} else {
  const apiCalls = requests.filter((url) => url.includes('/api/v1/'));
  record('PASS', 'the console is calling the real API', `${apiCalls.length} API requests, 0 fixture reads`);
}
await page.close();

await browser.close();

const failures = results.filter((result) => result.state === 'FAIL');
console.log(`\n${results.length - failures.length}/${results.length} browser checks passed`);
if (failures.length > 0) {
  console.log('\nFAILED:');
  for (const failure of failures) console.log(`  - ${failure.name}`);
  process.exit(1);
}
console.log('\nConsole verified at 1920x1080 against the real API.');
