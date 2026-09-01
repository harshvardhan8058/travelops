/**
 * Verify the documented offline Passenger View against committed API fixtures.
 *
 * Run with a Vite server started using `VITE_USE_FIXTURES=true`:
 *
 *   npm run verify:passenger-fixture -- http://127.0.0.1:5173
 */

/** @type {import('playwright').BrowserType} */
let chromium;
try {
  ({ chromium } = await import('playwright'));
} catch {
  console.error('verify:passenger-fixture needs Playwright and its matching Chromium build.');
  process.exit(2);
}

const BASE = process.argv[2] ?? 'http://127.0.0.1:5173';
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const page = await context.newPage();
const errors = [];
const requests = [];

page.on('pageerror', (error) => errors.push(String(error)));
page.on('console', (message) => {
  if (message.type() === 'error') errors.push(message.text());
});
page.on('request', (request) => requests.push(request.url()));

try {
  await page.goto(`${BASE}/passenger/K4X8YR`, { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(1000);

  await page
    .getByText('View how this booking was prioritised, plus references and sources', {
      exact: true,
    })
    .click();
  await page.waitForTimeout(100);

  const main = await page.locator('main').innerText();
  const required = [
    // The trip itself — `GET /bookings/{pnr}`'s own fixture, and the reason this route now has
    // one: this contract carries the flight facts `incident_group_impacts.json` never did.
    'K4X8YR',
    '6E 811',
    'VOBL',
    'VABB',
    'VIDP',
    'Current disruption',
    'What happened',
    'Current confirmed status',
    'What TravelOps is doing',
    'What to expect next',
    // The fabrication guard: present regardless of the linked incident's recorded state.
    'has not been confirmed for this booking',
    // The secondary, collapsed technical section — proves the priority-ranking fixture reached
    // the DOM, not just the trip.
    'persisted_records',
  ];
  const forbidden = [
    'PASSENGER_IMPACT_UNAVAILABLE',
    'BOOKING_UNAVAILABLE',
    'sample booking',
    'console-sample',
    'An airline colleague is reviewing your rebooking',
    // Operator vocabulary the spec names explicitly. None of it belongs on this screen.
    'orchestration',
    'assurance gate',
    'plan task',
    'action id',
    'decision log',
    'schema id',
    'workflow engine',
  ];
  const missing = required.filter((token) => !main.toLowerCase().includes(token.toLowerCase()));
  const unexpected = forbidden.filter((token) => main.toLowerCase().includes(token.toLowerCase()));
  const fixtureReads = requests.filter((url) => url.includes('/fixtures/'));
  const apiReads = requests.filter((url) => url.includes('/api/v1/'));

  if (errors.length > 0) throw new Error(`console errors: ${errors.join(' | ')}`);
  if (missing.length > 0) throw new Error(`missing from <main>: ${missing.join(', ')}`);
  if (unexpected.length > 0) throw new Error(`unexpected in <main>: ${unexpected.join(', ')}`);
  if (!fixtureReads.some((url) => url.endsWith('/fixtures/booking.json'))) {
    throw new Error('booking fixture was not requested');
  }
  if (!fixtureReads.some((url) => url.endsWith('/fixtures/incident_group_detail.json'))) {
    throw new Error('current-group fixture was not requested');
  }
  if (!fixtureReads.some((url) => url.endsWith('/fixtures/incident_group_impacts.json'))) {
    throw new Error('passenger-impact fixture was not requested');
  }
  if (apiReads.length > 0)
    throw new Error(`${apiReads.length} live API request(s) in fixture mode`);

  console.log(
    '[PASS] Passenger View renders the booking, disruption, and priority fixtures with no fabricated outcome',
  );
  console.log(`       ${fixtureReads.length} fixture reads, 0 live API reads, 0 runtime errors`);
} catch (error) {
  console.error(`[FAIL] ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
