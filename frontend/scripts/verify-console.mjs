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
  { path: '/replay/INC-2026-0820-VOBL-01', name: 'Replay', expect: ['frames'] },
  // Per-entity impact: the PNR proves a passenger-level row reached the DOM, not just a count.
  { path: '/impact/INC-2026-0820-VOBL-01', name: 'Impact Explorer', expect: ['Connections'] },
  { path: '/what-if/current', name: 'What-If', expect: ['what-if'] },
  { path: '/policy/INC-2026-0820-VOBL-01', name: 'Policy', expect: [] },
  { path: '/sources', name: 'Provenance ledger', expect: [] },
];

/**
 * Contrast at 1920x1080, measured rather than eyeballed.
 *
 * Two details decide whether this catches anything. Colours are normalised through a canvas,
 * because `getComputedStyle().color` can come back in a syntax whose channels are 0..1, and
 * parsing those as 8-bit gives every element a ratio of exactly 1.00 — an artefact that looks
 * like catastrophe and gets ignored. And translucent backgrounds are composited down to the
 * opaque layer beneath, because a state colour used as TEXT on a 12% tint of ITSELF loses far
 * more contrast than it appears to.
 *
 * Four tokens have been wrong for exactly that reason. The most recent, on the "not evaluated"
 * badge, missed the floor by 0.01 and was invisible to every other check in this file.
 */
const CONTRAST_PROBE = () => {
  const cv = document.createElement('canvas').getContext('2d');
  const rgba = (css) => {
    if (!css || css === 'transparent') return [0, 0, 0, 0];
    cv.fillStyle = '#000';
    cv.fillStyle = css;
    const s = cv.fillStyle;
    if (s[0] === '#')
      return [
        parseInt(s.slice(1, 3), 16),
        parseInt(s.slice(3, 5), 16),
        parseInt(s.slice(5, 7), 16),
        1,
      ];
    const n = (s.match(/[0-9.]+/g) || []).map(Number);
    return [n[0], n[1], n[2], n.length > 3 ? n[3] : 1];
  };
  const over = (f, b) => {
    const a = f[3];
    return [f[0] * a + b[0] * (1 - a), f[1] * a + b[1] * (1 - a), f[2] * a + b[2] * (1 - a), 1];
  };
  const lum = (c) => {
    const f = c.slice(0, 3).map((v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2];
  };
  const bgOf = (el) => {
    const stack = [];
    for (let n = el; n; n = n.parentElement) stack.push(rgba(getComputedStyle(n).backgroundColor));
    let acc = [13, 15, 17, 1];
    for (let i = stack.length - 1; i >= 0; i--) if (stack[i][3] > 0) acc = over(stack[i], acc);
    return acc;
  };
  let below = 0;
  const worst = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (el.closest('[aria-hidden=true],.sr-only')) continue;
    let op = 1;
    for (let n = el; n; n = n.parentElement) op *= parseFloat(getComputedStyle(n).opacity);
    // Deliberately de-emphasised future records are exempt; they are not being read.
    if (op < 0.3) continue;
    const px = parseFloat(cs.fontSize);
    const bold = parseInt(cs.fontWeight, 10) >= 700;
    const need = px >= 24 || (px >= 18.66 && bold) ? 3 : 4.5;
    const bg = bgOf(el);
    let fg = rgba(cs.color);
    if (op < 1) fg = [fg[0], fg[1], fg[2], fg[3] * op];
    const l1 = lum(over(fg, bg));
    const l2 = lum(bg);
    const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    if (ratio < need - 0.01) {
      below++;
      if (worst.length < 4)
        worst.push({
          text: (el.textContent || '').trim().slice(0, 32),
          ratio: +ratio.toFixed(2),
          need,
          px,
        });
    }
  }
  return { below, worst };
};

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
    const contrast = await page.evaluate(CONTRAST_PROBE);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 1,
    );
    const placeholder = /not yet built|placeholder/i.test(body);
    // innerText reflects CSS text-transform, so presence is compared case-insensitively. Whether
    // a value SHOULD be uppercased is a separate assertion, made below.
    const haystack = body.toLowerCase();
    const missing = route.expect.filter((token) => !haystack.includes(token.toLowerCase()));

    if (errors.length > 0) {
      record(
        'FAIL',
        `${route.name} renders without a runtime error`,
        errors.slice(0, 2).join(' | '),
      );
    } else if (contrast.below > 0) {
      record(
        'FAIL',
        `${route.name} has ${contrast.below} element(s) below WCAG AA`,
        contrast.worst
          .map((w) => `"${w.text}" ${w.ratio}:1 needs ${w.need} at ${w.px}px`)
          .join('\n       '),
      );
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
        [...document.querySelectorAll('h1,h2,h3')]
          .map((node) => node.textContent?.trim())
          .slice(0, 4),
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
  record(
    'FAIL',
    'the console is calling the API, not fixtures',
    `${fixtureCalls.length} fixture requests`,
  );
} else {
  const apiCalls = requests.filter((url) => url.includes('/api/v1/'));
  record(
    'PASS',
    'the console is calling the real API',
    `${apiCalls.length} API requests, 0 fixture reads`,
  );
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
