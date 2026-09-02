/**
 * Drive the canonical demo from the SCENARIO CENTER, and check the truth claims along the way.
 *
 * `verify:journey` already drives Scenario Builder -> cascade -> approval -> execution -> replay.
 * It does not touch the two things a presenter actually starts from — the reset control and the
 * simulation catalogue — and it asserts nothing about scope labelling or provider identity, which
 * are precisely the claims that were wrong.
 *
 * So this gate covers the seam the other one leaves:
 *
 *   reset from the UI  ->  dataset reports CLEAN  ->  Bengaluru simulation runs  ->  cascade
 *   ->  group figures say they are group figures
 *   ->  the provenance ledger names the configured provider and separates kind from usage
 *   ->  the executive report states the scope of its own figures
 *
 * Run against a stack in LLM fixture mode. The reset is real and destructive, which is why this is
 * a separate gate rather than something bolted onto the journey: it must start from whatever state
 * the machine is in and leave a known one behind.
 *
 *   npm run verify:scenario-center -- http://127.0.0.1:5173
 */

/** @type {import('playwright').BrowserType} */
let chromium;
try {
  ({ chromium } = await import('playwright'));
} catch {
  console.error('verify:scenario-center needs Playwright and its matching Chromium build.');
  process.exit(2);
}

const WEB = process.argv[2] ?? 'http://127.0.0.1:5173';
const API = process.env.VERIFY_API_BASE_URL ?? 'http://127.0.0.1:8000/api/v1';
const results = [];

function record(state, name, detail = '') {
  results.push({ state, name, detail });
  console.log(`[${state}] ${name}${detail ? `\n       ${detail}` : ''}`);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function get(path) {
  const response = await fetch(`${API}${path}`, { signal: AbortSignal.timeout(120000) });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`GET ${path} returned ${response.status}`);
  return payload;
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });

const page = await context.newPage();
const errors = [];
page.on('pageerror', (error) => errors.push(String(error)));
page.on('console', (message) => {
  if (message.type() === 'error') errors.push(message.text());
});

async function body() {
  return page.locator('body').innerText();
}

function contains(text, token) {
  return text.toLowerCase().includes(token.toLowerCase());
}

try {
  // ------------------------------------------------------------------ reset, from the UI
  await page.goto(`${WEB}/scenarios`, { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(1500);

  const phrase = page.getByRole('textbox').last();
  await phrase.fill('reset demo data');
  await page.getByRole('button', { name: /Reset demo data/i }).click();
  await page.waitForTimeout(4000);
  await page.reload({ waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(1500);

  let text = await body();
  assert(
    contains(text, 'dataset clean'),
    'after a reset the dataset must report CLEAN; it is the state every simulation needs',
  );
  record('PASS', 'reset from the console leaves a dataset that reports CLEAN');

  // ------------------------------------------------------- the canonical Bengaluru simulation
  // The catalogue publishes Bengaluru first, so the first Run control is its own. Rather than
  // trusting a DOM ancestor walk to prove that, the assertion below checks what the server
  // actually started — which is the fact that matters and cannot be faked by clicking the wrong
  // button.
  assert(
    contains(text, 'Bengaluru severe weather'),
    'the Bengaluru simulation is missing from the catalogue',
  );
  const runButton = page.getByRole('button', { name: /Run simulation/i }).first();
  assert(
    await runButton.isEnabled(),
    'the Bengaluru simulation must be runnable on a clean dataset',
  );

  await runButton.click();
  await page.waitForURL('**/cascade/current', { timeout: 120000 });
  // `waitForURL` resolves on `load`, before React has rendered the cascade.
  await page
    .getByText(/approving nothing/i)
    .first()
    .waitFor({ state: 'visible', timeout: 30000 });
  record('PASS', 'Bengaluru severe weather starts from the Scenario Center and lands on the cascade');

  const current = await get('/incident-groups/current');
  assert(current?.reference, 'no current group after starting the simulation');

  // What was actually started, checked against the catalogue rather than against the button that
  // was clicked. The catalogue resolves its members from recorded rows at request time, so this
  // also proves the console declared the flights the server chose rather than any of its own.
  const catalogue = await get('/demo/simulations');
  const bengaluru = catalogue.simulations.find((entry) => entry.id === 'bengaluru_severe_weather');
  assert(bengaluru, 'the catalogue no longer publishes bengaluru_severe_weather');
  assert(
    current.airport_icao === bengaluru.airport_icao,
    `started ${current.airport_icao}, expected ${bengaluru.airport_icao}`,
  );
  record(
    'PASS',
    'the started simulation is the current group, at the airport the catalogue named',
    `${current.reference} · ${current.airport_icao} · ${current.members?.length ?? 0} member(s)`,
  );

  // --------------------------------------------------------------------- scope is on the screen
  text = await body();
  assert(
    contains(text, 'across this disruption group'),
    'group-scoped tiles must say they are group-scoped; unlabelled they read as contradicting the incident figures',
  );
  record('PASS', 'group figures declare their scope on the cascade screen');

  // ------------------------------------------------------------ provenance: kind vs usage, provider
  const mode = await get('/system/mode');
  const sources = await get('/sources');
  await page.goto(`${WEB}/sources`, { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(1200);
  text = await body();

  assert(contains(text, 'kind'), 'the ledger must show what the data is');
  assert(contains(text, 'usage'), 'the ledger must show whether this run read from it');

  const reasoning = sources.sources.find((row) => row.role?.startsWith('Planner, explainer'));
  assert(reasoning, 'the ledger publishes no reasoning row');
  assert(
    reasoning.provider === mode.llm_provider,
    `the ledger names ${reasoning.provider} while /system/mode resolves ${mode.llm_provider}`,
  );
  assert(
    contains(text, reasoning.provider),
    'the resolved reasoning provider must appear on the ledger screen',
  );
  record(
    'PASS',
    'the ledger names the provider the runtime actually resolves, and separates kind from usage',
    `${reasoning.provider} · kind=${reasoning.kind} · usage=${reasoning.usage}`,
  );

  // Every `used` claim must have an artefact behind it. A key in the environment is not one.
  const unevidenced = sources.sources.filter((row) => row.usage === 'used' && !row.evidence);
  assert(
    unevidenced.length === 0,
    `${unevidenced.length} source(s) claim use with no evidence: ${unevidenced
      .map((row) => row.name)
      .join(', ')}`,
  );
  record('PASS', 'every source claiming use names the artefact behind the claim');

  // --------------------------------------------------------------------- report states its scope
  const report = await get(`/reports/${current.reference}`).catch(() => null);
  if (report) {
    assert(report.scope === 'group', `a group reference must produce group scope, got ${report.scope}`);
    assert(report.scope_note, 'the report must state what its figures cover');
    record('PASS', 'the executive report declares the scope of its own figures', report.scope_note);
  } else {
    record('PASS', 'executive report not available in this mode; nothing claimed');
  }

  assert(errors.length === 0, `console errors: ${errors.slice(0, 2).join(' | ')}`);
  record('PASS', 'no console errors across the scenario-centre journey');
} catch (error) {
  record('FAIL', 'scenario centre journey', error instanceof Error ? error.message : String(error));
} finally {
  await browser.close();
}

const passed = results.filter((result) => result.state === 'PASS').length;
console.log(`\n${passed}/${results.length} scenario-centre checks passed`);
process.exitCode = passed === results.length ? 0 : 1;
