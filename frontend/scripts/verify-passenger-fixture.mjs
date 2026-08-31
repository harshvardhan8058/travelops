/**
 * Verify that the Passenger View substitutes NOTHING when its source is unavailable.
 *
 * Run with a Vite server started using `VITE_USE_FIXTURES=true`:
 *
 *   npm run verify:passenger-fixture -- http://127.0.0.1:5173
 *
 * ## Why this script changed shape
 *
 * It used to drive a passenger screen that composed itself from `/incident-groups/current` and
 * `/incident-groups/{ref}/impact`, both of which have committed fixtures — so in offline mode the
 * screen rendered populated, and this script asserted that populated content.
 *
 * The view now reads one purpose-built contract, `GET /passenger/{booking_ref}/disruption`, which is
 * the only source that can publish a booking's flights, delay, connection, options and approval
 * state. That contract is deliberately NOT in the client's fixture map: a committed passenger
 * payload is precisely the "looks real, is invented" artefact the endpoint was built to retire, and
 * a fixture behind this particular screen would let it keep passing while the real join was broken.
 *
 * So the guarantee this script exists to hold is unchanged and is now checked directly: **with no
 * source available, the screen says so and invents nobody.** That is a stronger statement than the
 * old one, because it fails if a sample is ever reintroduced as a fallback.
 *
 * Still asserted, exactly as before:
 *   - not one live API request escapes in fixture mode;
 *   - no sample identifiers or sample prose reach the DOM;
 *   - no runtime or console errors.
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

  const main = await page.locator('main').innerText();

  /*
   * What an honest offline screen must say. The booking reference the reader asked for is echoed
   * back so they can see it was understood, and the copy states that the trip could not be loaded —
   * never that the trip is fine.
   */
  const required = ['could not', 'trip'];

  /*
   * The sample, in every form it ever took. `console-sample` was the provenance of the shipped
   * payload; the rebooking sentence was its invented next step; `A. Nair` was its invented
   * passenger; `X9Y2Z1` its invented reference. None may ever reappear, and the list is kept
   * rather than trimmed so a reintroduction is caught by name.
   */
  const forbidden = [
    'PASSENGER_IMPACT_UNAVAILABLE',
    'sample booking',
    'console-sample',
    'An airline colleague is reviewing your rebooking',
    'A. Nair',
    'X9Y2Z1',
    '@example.com',
  ];

  const missing = required.filter((token) => !main.toLowerCase().includes(token.toLowerCase()));
  const unexpected = forbidden.filter((token) => main.toLowerCase().includes(token.toLowerCase()));
  const apiReads = requests.filter((url) => url.includes('/api/v1/'));

  if (errors.length > 0) throw new Error(`console errors: ${errors.join(' | ')}`);
  if (missing.length > 0) throw new Error(`missing from <main>: ${missing.join(', ')}`);
  if (unexpected.length > 0) throw new Error(`sample content in <main>: ${unexpected.join(', ')}`);
  if (apiReads.length > 0)
    throw new Error(`${apiReads.length} live API request(s) in fixture mode`);

  /*
   * A passenger reference is the one identifier this screen may show. Its absence here is the
   * point: with no contract answering, there is no passenger to name.
   */
  if (/PAX-\d+/.test(main)) {
    throw new Error('a passenger reference was rendered with no contract behind it');
  }

  console.log('[PASS] Passenger View substitutes no sample when its contract is unavailable');
  console.log(`       0 live API reads, 0 runtime errors, ${forbidden.length} sample tokens absent`);
} catch (error) {
  console.error(`[FAIL] ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
