/**
 * Real-browser Scenario Builder journey. Requires a migrated, seeded API with no active cascade.
 * It deliberately remains separate from verify-console so that gate stays exactly 12/12.
 * Owner: Stream D.
 */

/** @type {import('playwright').BrowserType} */
let chromium;
try {
  ({ chromium } = await import('playwright'));
} catch {
  console.error('verify:scenario needs Playwright and its Chromium build.');
  process.exit(2);
}

const WEB_BASE = (process.argv[2] ?? 'http://127.0.0.1:5173').replace(/\/$/, '');
const API_BASE = (
  process.env.VERIFY_API_BASE_URL ??
  process.env.VITE_API_BASE_URL ??
  'http://127.0.0.1:8000/api/v1'
).replace(/\/$/, '');

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const page = await context.newPage();
// Fixed browser-session keys make this journey safely replay the same create/start requests when it
// is rerun against an unchanged verification database, rather than creating a conflicting scenario.
await page.addInitScript(() => {
  const ids = ['00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000002'];
  let index = 0;
  Object.defineProperty(globalThis.crypto, 'randomUUID', {
    value: () => ids[Math.min(index++, ids.length - 1)],
  });
});
const failures = [];
const scenarioResponses = [];

page.on('console', (message) => {
  const text = message.text();
  // The app shell probes the fixed demo incident before any scenario is started; on a seed-only
  // database those two reads correctly return 404. They are unrelated to this route's lifecycle.
  if (message.type() === 'error' && !text.includes('status of 404')) {
    failures.push(`console: ${text}`);
  }
});
page.on('pageerror', (error) => failures.push(`page: ${error.message}`));
page.on('requestfailed', (request) =>
  failures.push(
    `request: ${request.method()} ${request.url()} ${request.failure()?.errorText ?? ''}`,
  ),
);
page.on('response', (response) => {
  const url = new URL(response.url());
  if (response.request().method() === 'POST' && url.pathname.includes('/api/v1/scenarios')) {
    scenarioResponses.push({ path: url.pathname, status: response.status() });
  }
});

try {
  await page.goto(`${WEB_BASE}/scenarios/new`, { waitUntil: 'networkidle', timeout: 30_000 });
  await page.getByRole('radio', { name: /Bengaluru monsoon storm/i }).click();
  await page.getByLabel('Starts at (UTC)').fill('2026-08-20T15:36');
  await page.getByRole('button', { name: 'Validate & preview', exact: true }).click();

  const runButton = page.getByRole('button', { name: /Create & run/i });
  await runButton.waitFor({ state: 'visible' });
  if (await runButton.isDisabled()) {
    throw new Error(
      `Create & run stayed disabled: ${(await page.locator('body').innerText()).slice(0, 1200)}`,
    );
  }

  await runButton.click();
  try {
    await page.waitForURL(/\/cascade\/SCN-[^/?#]+$/, { timeout: 30_000 });
  } catch {
    throw new Error(
      `start did not navigate; responses=${JSON.stringify(scenarioResponses)}; ` +
        `screen=${(await page.locator('body').innerText()).slice(-1800)}`,
    );
  }
  const match = /\/cascade\/(SCN-[^/?#]+)$/.exec(new URL(page.url()).pathname);
  if (!match)
    throw new Error(`start did not navigate to a concrete Scenario cascade: ${page.url()}`);
  const reference = decodeURIComponent(match[1]);

  await page.getByText(reference, { exact: true }).first().waitFor({ state: 'visible' });
  const createResponse = scenarioResponses.find(({ path }) => path === '/api/v1/scenarios');
  const startResponse = scenarioResponses.find(({ path }) =>
    path.endsWith(`/scenarios/${reference}/start`),
  );
  if (createResponse?.status !== 201) {
    throw new Error(
      `create response was ${createResponse?.status ?? 'not observed'}, expected 201`,
    );
  }
  if (startResponse?.status !== 200) {
    throw new Error(`start response was ${startResponse?.status ?? 'not observed'}, expected 200`);
  }

  const currentResponse = await fetch(`${API_BASE}/incident-groups/current`, {
    signal: AbortSignal.timeout(15_000),
  });
  if (!currentResponse.ok) {
    throw new Error(`GET /incident-groups/current returned ${currentResponse.status}`);
  }
  const current = await currentResponse.json();
  if (current.reference !== reference) {
    throw new Error(`current resolved to ${current.reference}, expected ${reference}`);
  }

  await page.goto(`${WEB_BASE}/cascade/current`, { waitUntil: 'networkidle', timeout: 30_000 });
  await page.getByText(reference, { exact: true }).first().waitFor({ state: 'visible' });

  if (failures.length > 0) throw new Error(failures.join('\n'));
  console.log(`[PASS] Scenario Builder created and started ${reference}`);
  console.log('[PASS] /cascade/current resolved the newly active Scenario cascade');
  console.log('Scenario Builder browser journey passed.');
} catch (error) {
  console.error(`[FAIL] ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
