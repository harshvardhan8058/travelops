/* global document, window, getComputedStyle */
/**
 * Drive the final demo journey through the real UI: Scenario Builder -> Cascade -> Recovery ->
 * approval -> execution -> passenger projection -> replay -> provenance.
 *
 * Run against a freshly migrated and seeded stack in LLM fixture mode, without demo injection.
 * Every lifecycle mutation is performed by a visible browser control; verifier-side API calls are
 * read-only oracles used to prove the projections describe the same generated records.
 */

/** @type {import('playwright').BrowserType} */
let chromium;
try {
  ({ chromium } = await import('playwright'));
} catch {
  console.error('verify:journey needs Playwright and its matching Chromium build.');
  process.exit(2);
}

const WEB = process.argv[2] ?? 'http://127.0.0.1:5173';
const BROWSER_API_BASE = process.env.VITE_API_BASE_URL?.replace(/\/$/, '');
const VERIFY_API_BASE = process.env.VERIFY_API_BASE_URL?.replace(/\/$/, '');
const API = VERIFY_API_BASE || BROWSER_API_BASE || 'http://127.0.0.1:8000/api/v1';
const VIEWPORTS = [
  { name: 'projector', width: 1920, height: 1080, timelineVisible: true },
  { name: 'desktop', width: 1600, height: 900, timelineVisible: true },
  { name: 'laptop', width: 1366, height: 768, timelineVisible: false },
];
const results = [];
const browserWrites = [];

function record(state, name, detail = '') {
  results.push({ state, name, detail });
  console.log(`[${state}] ${name}${detail ? `\n       ${detail}` : ''}`);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function contains(body, token) {
  return body.toLowerCase().includes(token.toLowerCase());
}

function recordedGeneratorKind(generator) {
  if (generator === 'fallback-playbook') return 'deterministic_fallback';
  if (generator === 'planner-agent') return 'model_authored';
  return 'unclassified';
}

function normaliseStatus(status) {
  return String(status).trim().toLowerCase().replace(/\s+/g, '_');
}

async function get(path) {
  const response = await fetch(`${API}${path}`, { signal: AbortSignal.timeout(300000) });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`GET ${path} returned ${response.status}: ${JSON.stringify(payload)}`);
  }
  return payload;
}

async function waitFor(getter, predicate, description, timeout = 120000) {
  const deadline = Date.now() + timeout;
  let latest;
  while (Date.now() < deadline) {
    latest = await getter();
    if (predicate(latest)) return latest;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`${description}; last value: ${JSON.stringify(latest)}`);
}

function responseMatches(response, method, pathPattern) {
  const request = response.request();
  return request.method() === method && pathPattern.test(new URL(response.url()).pathname);
}

function assertSourceStrip(body) {
  const normalised = body.replace(/\s+/g, ' ');
  assert(/LLM\s+(LIVE|FIXTURE|OFF)/i.test(normalised), 'effective LLM mode is not visible');
  assert(/FLT\s+(LIVE|FIXTURE)/i.test(normalised), 'effective flight-status mode is not visible');
  assert(/WX\s+(LIVE|FIXTURE)/i.test(normalised), 'effective weather mode is not visible');
  /*
   * NOTIFY publishes the backend's own word for the transport — `console`, `mailtrap`, `gmail` —
   * while the live/simulated distinction is carried by the chip's posture and its tooltip.
   * `api/runtimeModes.ts` is deliberate about that: it renders what `/system/mode` returned and
   * never substitutes a friendlier synonym, so `NOTIFY CONSOLE` says more than `NOTIFY SIMULATED`
   * did — it names which transport is in play as well as the fact that nothing was delivered.
   *
   * This assertion therefore checks the strip is populated with a value the contract can actually
   * publish. Whether that value may read `live` is a semantic question owned by
   * `runtimeModes.test.ts`, which pins `real_email_enabled` — not the mode string — as the only
   * thing that earns a live posture. Widening the alternation here does not loosen that: a chip
   * that wrongly claimed delivery would still fail there.
   */
  assert(
    /NOTIFY\s+(LIVE|SIMULATED|CONSOLE|MAILTRAP|GMAIL)/i.test(normalised),
    'effective notification mode is not visible',
  );
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });

// Vite uses host loopback for a normal browser. Chromium inside the web image instead reaches the
// same API through its container address. Only verifier traffic is rewritten.
if (BROWSER_API_BASE && VERIFY_API_BASE && BROWSER_API_BASE !== VERIFY_API_BASE) {
  await context.route(`${BROWSER_API_BASE}/**`, async (route) => {
    const requestUrl = route.request().url();
    const response = await route.fetch({
      url: `${VERIFY_API_BASE}${requestUrl.slice(BROWSER_API_BASE.length)}`,
    });
    await route.fulfill({ response });
  });
}

function watch(page) {
  const errors = [];
  const requests = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('request', (request) => {
    requests.push(request.url());
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      browserWrites.push(`${request.method()} ${new URL(request.url()).pathname}`);
    }
  });
  return { errors, requests };
}

async function goto(page, path) {
  await page.goto(`${WEB}${path}`, { waitUntil: 'networkidle', timeout: 120000 });
  await page.waitForTimeout(750);
}

async function pageBody(page, openDetails = false) {
  if (openDetails) {
    await page.locator('details').evaluateAll((nodes) => {
      for (const node of nodes) node.open = true;
    });
    await page.waitForTimeout(100);
  }
  return page.locator('body').innerText();
}

async function assertHealthyPage(page, observed, expected, absent = [], openDetails = false, options = {}) {
  const body = await pageBody(page, openDetails);
  const missing = expected.filter((token) => !contains(body, token));
  const unexpected = absent.filter((token) => contains(body, token));
  const fixtureReads = observed.requests.filter((url) => url.includes('/fixtures/'));
  assert(observed.errors.length === 0, `console errors: ${observed.errors.slice(0, 2).join(' | ')}`);
  assert(fixtureReads.length === 0, `${fixtureReads.length} fixture request(s)`);
  assert(missing.length === 0, `missing from page: ${missing.join(', ')}`);
  assert(unexpected.length === 0, `unexpected on page: ${unexpected.join(', ')}`);
  // The passenger route renders in its own customer-portal shell, not the operator console, so it
  // carries none of the operator's runtime-mode chips this check otherwise requires.
  if (!options.skipSourceStrip) assertSourceStrip(body);
  return body;
}

async function physicalFlightStatus(incidentReference) {
  const flights = await get('/flights');
  const recordedFlight = flights.flights?.find(
    (flight) => flight.incident_reference === incidentReference,
  );
  assert(recordedFlight, `GET /flights has no row for ${incidentReference}`);

  const page = await context.newPage();
  const observed = watch(page);
  try {
    await goto(page, '/');
    const table = page.locator('main table').first();
    const headers = await table.locator('thead th').allInnerTexts();
    const statusColumn = headers.findIndex(
      (header) => normaliseStatus(header.split('\n')[0]) === 'flight_status',
    );
    assert(statusColumn >= 0, 'Command Center has no Flight status column');
    const row = table.locator('tbody tr').filter({ hasText: incidentReference }).first();
    assert((await row.count()) === 1, `Command Center row for ${incidentReference} is missing`);
    const uiStatus = normaliseStatus(await row.locator('td').nth(statusColumn).innerText());
    const recordedStatus = normaliseStatus(recordedFlight.status);
    assert(uiStatus.length > 0, `flight status for ${incidentReference} is empty`);
    assert(
      uiStatus === recordedStatus,
      `Command Center shows ${uiStatus}, but GET /flights records ${recordedStatus}`,
    );
    await assertHealthyPage(page, observed, [
      incidentReference,
      'Flight status describes the operation itself',
    ]);
    return recordedStatus;
  } finally {
    await page.close();
  }
}

async function inspectAtViewport(
  name,
  path,
  viewport,
  expected,
  absent = [],
  openDetails = false,
  options = {},
) {
  const page = await context.newPage();
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  const observed = watch(page);
  try {
    await goto(page, path);
    await assertHealthyPage(page, observed, expected, absent, openDetails, options);
    const layout = await page.evaluate(() => {
      const main = document.querySelector('main');
      const timeline = document.querySelector('aside[aria-label="Decision timeline"]');
      const nestedVertical = [...document.querySelectorAll('main *')]
        .filter((element) => {
          const style = getComputedStyle(element);
          return (
            element.scrollHeight > element.clientHeight + 2 &&
            (style.overflowY === 'auto' || style.overflowY === 'scroll')
          );
        })
        .map((element) => `${element.tagName}.${String(element.className).slice(0, 80)}`);
      return {
        documentWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
        mainWidth: main?.scrollWidth ?? null,
        mainClientWidth: main?.clientWidth ?? null,
        documentOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
        mainOverflow: Boolean(main && main.scrollWidth > main.clientWidth + 1),
        horizontalOffenders: [...document.querySelectorAll('body *')]
          .filter((element) => {
            const box = element.getBoundingClientRect();
            return box.right > window.innerWidth + 1 || box.left < -1;
          })
          .slice(0, 8)
          .map((element) => ({
            tag: element.tagName,
            className: String(element.className).slice(0, 100),
            box: Math.round(element.getBoundingClientRect().right),
            scrollWidth: element.scrollWidth,
            clientWidth: element.clientWidth,
          })),
        nestedVertical,
        timelineVisible: Boolean(timeline && getComputedStyle(timeline).display !== 'none'),
      };
    });
    assert(
      !layout.documentOverflow,
      `document overflows horizontally (${layout.documentWidth}/${layout.viewportWidth}): ${JSON.stringify(layout.horizontalOffenders)}`,
    );
    assert(
      !layout.mainOverflow,
      `<main> overflows horizontally (${layout.mainWidth}/${layout.mainClientWidth}): ${JSON.stringify(layout.horizontalOffenders)}`,
    );
    assert(
      layout.nestedVertical.length === 0,
      `nested vertical scroll containers: ${layout.nestedVertical.join(', ')}`,
    );
    // The passenger route carries no Decision Timeline at any viewport — it is not part of the
    // operator shell this check otherwise verifies.
    const expectedTimeline = options.expectTimeline ?? viewport.timelineVisible;
    assert(
      layout.timelineVisible === expectedTimeline,
      `timeline should be ${expectedTimeline ? 'visible' : 'hidden'}`,
    );
    record('PASS', `${name} at ${viewport.width}x${viewport.height}`, 'no overflow, nested scroll, fixture read, or runtime error');
  } finally {
    await page.close();
  }
}

try {
  const page = await context.newPage();
  const observed = watch(page);
  await goto(page, '/scenarios/new');
  await assertHealthyPage(page, observed, ['Scenario builder', 'published scenario lifecycle']);

  await page.getByRole('radio', { name: /Bengaluru monsoon storm/i }).click();
  await page.locator('input[type="datetime-local"]').fill('2026-08-20T15:36');
  await page.getByRole('button', { name: 'Validate & preview', exact: true }).click();
  await page.getByText('Incidents this would open', { exact: true }).waitFor();
  const review = await page.locator('main').innerText();
  assert(contains(review, '6E 2134'), 'review does not name the selected flight');
  assert(contains(review, 'Computed by the engine when the scenario runs'), 'review invents or omits engine-owned figures');

  const createResponsePromise = page.waitForResponse(
    (response) => responseMatches(response, 'POST', /\/api\/v1\/scenarios$/),
    { timeout: 120000 },
  );
  const startResponsePromise = page.waitForResponse(
    (response) => responseMatches(response, 'POST', /\/api\/v1\/scenarios\/[^/]+\/start$/),
    { timeout: 120000 },
  );
  const cascadePromise = page.waitForURL('**/cascade/current', { timeout: 120000 });
  await page.getByRole('button', { name: 'Create & run', exact: true }).click();
  const [createResponse, startResponse] = await Promise.all([
    createResponsePromise,
    startResponsePromise,
    cascadePromise,
  ]);
  assert(createResponse.ok(), `scenario create returned ${createResponse.status()}`);
  assert(startResponse.ok(), `scenario start returned ${startResponse.status()}`);
  const created = await createResponse.json();
  const started = await startResponse.json();
  const groupReference = created.scenario_reference;
  assert(/^SCN-/.test(groupReference), `unexpected scenario reference ${groupReference}`);
  assert(started.scenario_reference === groupReference, 'start response describes another scenario');

  const selected = await waitFor(
    () => get('/incident-groups/current'),
    (group) => group.reference === groupReference,
    'current group did not select the UI-created scenario',
  );
  assert(selected.state === 'detected', `new scenario started in ${selected.state}`);
  // `waitForURL` resolves on `load`, which is before React has rendered the cascade — the panel is
  // still its skeleton for a moment. Asserting the body text straight after the navigation therefore
  // failed intermittently on a slower machine, reporting a missing string for a screen that was
  // merely a beat behind. Waiting for one element the cascade always renders turns that race into a
  // wait. A false failure in this gate is expensive: it reads exactly like a real one.
  await page.getByText(/approving nothing/i).first().waitFor({ state: 'visible', timeout: 30000 });
  await assertHealthyPage(page, observed, [groupReference, 'approving nothing']);
  record('PASS', 'Scenario Builder creates and starts one recorded scenario', `${groupReference} · ${selected.state}`);

  let waiting = selected;
  for (let attempt = 0; attempt < 6 && waiting.awaiting_approval_count === 0; attempt += 1) {
    const previousState = waiting.state;
    const advanceResponsePromise = page.waitForResponse(
      (response) =>
        responseMatches(
          response,
          'POST',
          new RegExp(`/api/v1/incident-groups/${groupReference}/run$`),
        ),
      { timeout: 300000 },
    );
    await page.getByRole('button', { name: /Advance disruption/i }).click();
    const advanceResponse = await advanceResponsePromise;
    assert(advanceResponse.ok(), `group advance returned ${advanceResponse.status()}`);
    waiting = await waitFor(
      () => get('/incident-groups/current'),
      (group) =>
        group.reference === groupReference &&
        (group.state !== previousState || group.awaiting_approval_count > 0),
      'scenario projection did not advance after the browser run',
      300000,
    );
    assert(!['blocked', 'failed'].includes(waiting.state), `group stopped in ${waiting.state}`);
  }
  assert(
    waiting.awaiting_approval_count > 0,
    `group did not reach approval; state ${waiting.state}, awaiting ${waiting.awaiting_approval_count}`,
  );

  const groupDetail = await get(`/incident-groups/${groupReference}`);
  const incidentReference = groupDetail.flights.find((flight) => flight.incident_reference)?.incident_reference;
  assert(incidentReference, 'the generated group has no incident reference');
  const incident = await get(`/incidents/${incidentReference}`);
  assert(incident.state === 'awaiting_approval', `incident stopped in ${incident.state}`);
  assert(incident.plan?.generator === 'fallback-playbook', 'deterministic fallback is not the plan of record');

  const systemMode = await get('/system/mode');
  const timeline = await get(`/incidents/${incidentReference}/timeline`);
  const proposals = timeline.entries.filter((entry) => entry.event_type === 'PLAN_PROPOSED');
  const fallbackProposal = [...proposals].reverse().find(
    (entry) => recordedGeneratorKind(entry.detail?.generator) === 'deterministic_fallback',
  );
  const plannerProposal = [...proposals].reverse().find(
    (entry) => recordedGeneratorKind(entry.detail?.generator) === 'model_authored',
  );
  assert(fallbackProposal, 'recorded deterministic fallback proposal missing');
  assert(
    Number.isSafeInteger(fallbackProposal.detail?.plan_id) && fallbackProposal.detail.plan_id > 0,
    `fallback proposal has invalid plan id ${fallbackProposal.detail?.plan_id}`,
  );
  assert(plannerProposal, 'recorded planner proposal missing');
  assert(
    Number.isSafeInteger(plannerProposal.detail?.plan_id) && plannerProposal.detail.plan_id > 0,
    `planner proposal has invalid plan id ${plannerProposal.detail?.plan_id}`,
  );
  assert(
    ['live', 'fixture', 'off'].includes(plannerProposal.detail?.llm_mode),
    `planner proposal has invalid LLM mode ${plannerProposal.detail?.llm_mode}`,
  );
  assert(
    plannerProposal.detail.llm_mode === systemMode.llm_mode,
    `planner proposal mode ${plannerProposal.detail.llm_mode} does not match system mode ${systemMode.llm_mode}`,
  );
  assert(
    plannerProposal.detail.plan_id !== incident.plan.id,
    'planner candidate is falsely selected as the plan of record',
  );
  record(
    'PASS',
    'planner candidate and deterministic plan remain separate recorded facts',
    `${plannerProposal.detail.generator} candidate; ${incident.plan.generator} plan of record`,
  );

  const impacts = await get(`/incident-groups/${groupReference}/impacts?limit=1000`);
  assert(impacts.passengers_assessed > 0, 'passenger priorities were not persisted');
  const passenger = impacts.passengers[0];
  assert(passenger?.pnr, 'no persisted booking reference was returned');

  await goto(page, `/incidents/${incidentReference}`);
  await assertHealthyPage(page, observed, [
    incidentReference,
    'planner candidate',
    'Deterministic fallback',
    'recorded plan of record',
    'Fallback playbook · deterministic',
    'Latest origin weather observation',
  ]);
  const physicalStatusBefore = await physicalFlightStatus(incidentReference);
  assert(
    physicalStatusBefore !== normaliseStatus(incident.state),
    `physical status duplicates incident workflow state ${incident.state}`,
  );
  assert(
    physicalStatusBefore !== normaliseStatus(waiting.state),
    `physical status duplicates group workflow state ${waiting.state}`,
  );

  await goto(page, '/assurance');
  await assertHealthyPage(page, observed, [
    groupReference,
    'Decision to make',
    'Risk and measured impact',
    'Cannot be covered',
    'human decisions required',
  ]);

  await goto(page, `/passenger/${passenger.pnr}`);
  await assertHealthyPage(
    page,
    observed,
    [
      passenger.pnr,
      'A decision is still needed',
      'You do not need to do anything in TravelOps right now.',
      'has not been confirmed for this booking',
    ],
    ['sample booking', 'console-sample', 'An airline colleague is reviewing your rebooking'],
    false,
    { skipSourceStrip: true },
  );

  let approved = 0;
  while (true) {
    const assurance = await get(`/incidents/${incidentReference}/assurance`);
    const pending = assurance.evaluations.find(
      (evaluation) => evaluation.decision === 'needs_human' && !evaluation.human_decision,
    );
    if (!pending) break;
    const currentIncident = await get(`/incidents/${incidentReference}`);
    const task = currentIncident.plan?.tasks.find((candidate) => candidate.assurance_id === pending.id);
    assert(task, `no plan task links to pending evaluation ${pending.id}`);

    await goto(page, `/incidents/${incidentReference}`);
    const taskButton = page.locator('main button').filter({ hasText: task.action_type }).first();
    assert((await taskButton.count()) === 1, `task control for ${task.action_type} is missing`);
    await taskButton.click();
    const reason = `final browser journey approval ${pending.id}`;
    const textarea = page.locator('main textarea').first();
    await textarea.waitFor({ state: 'visible', timeout: 30000 });
    await textarea.fill(reason);
    const decisionResponsePromise = page.waitForResponse(
      (response) => responseMatches(response, 'POST', new RegExp(`/api/v1/assurance/${pending.id}/decision$`)),
      { timeout: 120000 },
    );
    await page.getByRole('button', { name: 'Approve action', exact: true }).click();
    const decisionResponse = await decisionResponsePromise;
    assert(decisionResponse.ok(), `decision ${pending.id} returned ${decisionResponse.status()}`);
    const decision = await decisionResponse.json();
    assert(decision.decision === 'approved', `evaluation ${pending.id} was not approved`);
    assert(decision.reason === reason, `evaluation ${pending.id} did not persist the entered reason`);
    approved += 1;
  }
  assert(approved > 0, 'no human decision was recorded through the UI');
  record('PASS', 'human approvals are entered and persisted through Recovery', `${approved} action-level decision(s)`);

  await goto(page, `/incidents/${incidentReference}`);
  let terminalIncident = await get(`/incidents/${incidentReference}`);
  for (let attempt = 0; attempt < 6 && !['resolved', 'blocked', 'failed'].includes(terminalIncident.state); attempt += 1) {
    const runResponsePromise = page.waitForResponse(
      (response) => responseMatches(response, 'POST', new RegExp(`/api/v1/incidents/${incidentReference}/run$`)),
      { timeout: 300000 },
    );
    await page.getByRole('button', { name: 'Run workflow', exact: true }).click();
    const runResponse = await runResponsePromise;
    assert(runResponse.ok(), `incident run returned ${runResponse.status()}`);
    terminalIncident = await waitFor(
      () => get(`/incidents/${incidentReference}`),
      (value) => value.state !== terminalIncident.state || ['resolved', 'blocked', 'failed'].includes(value.state),
      'incident projection did not advance after Run workflow',
      120000,
    );
  }
  assert(terminalIncident.state === 'resolved', `incident ended ${terminalIncident.state}`);

  let resolvedGroup = await get('/incident-groups/current');
  if (resolvedGroup.state !== 'resolved') {
    await goto(page, '/cascade/current');
    for (let attempt = 0; attempt < 3 && resolvedGroup.state !== 'resolved'; attempt += 1) {
      const responsePromise = page.waitForResponse(
        (response) => responseMatches(response, 'POST', new RegExp(`/api/v1/incident-groups/${groupReference}/run$`)),
        { timeout: 300000 },
      );
      await page.getByRole('button', { name: /Advance disruption/i }).click();
      const response = await responsePromise;
      assert(response.ok(), `group completion returned ${response.status()}`);
      resolvedGroup = await get('/incident-groups/current');
    }
  }
  assert(resolvedGroup.reference === groupReference, 'current group changed during execution');
  assert(resolvedGroup.state === 'resolved', `group ended ${resolvedGroup.state}`);
  assert(resolvedGroup.awaiting_approval_count === 0, 'resolved group still awaits approval');

  const physicalStatusAfter = await physicalFlightStatus(incidentReference);
  assert(
    physicalStatusAfter !== normaliseStatus(terminalIncident.state),
    `physical status duplicates incident workflow state ${terminalIncident.state}`,
  );
  assert(
    physicalStatusAfter !== normaliseStatus(resolvedGroup.state),
    `physical status duplicates group workflow state ${resolvedGroup.state}`,
  );
  record(
    'PASS',
    'execution resolves the workflow while physical status remains independently sourced',
    `${incidentReference} · before ${physicalStatusBefore}; current ${physicalStatusAfter}`,
  );
  await page.close();

  const finalRoutes = [
    ['Command Center', '/', [groupReference, incidentReference, 'Flight status describes the operation itself'], [], false],
    ['Cascade', '/cascade/current', [groupReference, 'resolved', 'approving nothing'], [], false],
    ['Recovery', `/incidents/${incidentReference}`, [incidentReference, 'workflow resolved', 'Deterministic fallback', 'Actions executed'], [], false],
    ['Approval Queue', '/assurance', [groupReference, 'workflow resolved', 'incidents awaiting approval', 'Decision to make'], [], false],
    [
      'Passenger View',
      `/passenger/${passenger.pnr}`,
      [
        passenger.pnr,
        'review complete',
        'Our review of this disruption is complete',
        'The review has finished, but that does not mean your booking changed.',
        'has not been confirmed for this booking',
        'Operational workflow: resolved.',
        'persisted_records',
      ],
      ['sample booking', 'console-sample', 'An airline colleague is reviewing your rebooking'],
      true,
      { skipSourceStrip: true, expectTimeline: false },
    ],
    ['Replay', `/replay/${incidentReference}`, [incidentReference, 'frames'], [], false],
    ['Provenance', '/sources', ['Provenance', 'Source ledger', 'Registered sources'], [], false],
  ];

  for (const viewport of VIEWPORTS) {
    for (const [name, path, expected, absent, openDetails, options] of finalRoutes) {
      await inspectAtViewport(name, path, viewport, expected, absent, openDetails, options ?? {});
    }
  }

  const requiredWritePatterns = [
    /POST \/api\/v1\/scenarios$/,
    /POST \/api\/v1\/scenarios\/[^/]+\/start$/,
    new RegExp(`POST /api/v1/incident-groups/${groupReference}/run$`),
    /POST \/api\/v1\/assurance\/\d+\/decision$/,
    new RegExp(`POST /api/v1/incidents/${incidentReference}/run$`),
  ];
  for (const pattern of requiredWritePatterns) {
    assert(browserWrites.some((write) => pattern.test(write)), `browser never performed ${pattern}`);
  }
  record('PASS', 'all lifecycle writes originated from visible browser controls', `${browserWrites.length} observed write request(s)`);
} catch (error) {
  record('FAIL', 'final browser journey', String(error instanceof Error ? error.message : error));
} finally {
  await browser.close();
}

const failures = results.filter((result) => result.state === 'FAIL');
console.log(`\n${results.length - failures.length}/${results.length} final journey checks passed`);
if (failures.length > 0) process.exit(1);
console.log('\nFinal UI-driven journey verified at 1920x1080, 1600x900, and 1366x768.');
