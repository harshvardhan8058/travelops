/**
 * Drive controlled lifecycle transitions through the real API and verify every projection in
 * Chromium: disruption -> evidence -> planner candidate + deterministic plan of record ->
 * assurance -> human approval -> execution -> resolved workflow -> honest passenger projection.
 *
 * Run against a freshly migrated, seeded and `demo-cascade`-injected stack in LLM fixture mode.
 */

import { chromium } from 'playwright';

const WEB = process.argv[2] ?? 'http://127.0.0.1:5173';
const API = (process.env.VERIFY_API_BASE_URL ?? 'http://127.0.0.1:8000/api/v1').replace(/\/$/, '');
const GROUP = 'GRP-2026-0820-VOBL';
const PRIMARY = 'INC-2026-0820-VOBL-01';
const results = [];

function record(state, name, detail = '') {
  results.push({ state, name, detail });
  console.log(`[${state}] ${name}${detail ? `\n       ${detail}` : ''}`);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function call(method, path, body) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(300000),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`${method} ${path} returned ${response.status}: ${JSON.stringify(payload)}`);
  }
  return payload;
}

function contains(body, token) {
  return body.toLowerCase().includes(token.toLowerCase());
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });

async function inspectPage(name, path, expected, absent = []) {
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('request', (request) => requests.push(request.url()));
  try {
    await page.goto(`${WEB}${path}`, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(1500);
    const main = await page.locator('main').innerText();
    const missing = expected.filter((token) => !contains(main, token));
    const unexpected = absent.filter((token) => contains(main, token));
    const fixtureReads = requests.filter((url) => url.includes('/fixtures/'));
    assert(errors.length === 0, `console errors: ${errors.slice(0, 2).join(' | ')}`);
    assert(fixtureReads.length === 0, `${fixtureReads.length} fixture request(s)`);
    assert(missing.length === 0, `missing: ${missing.join(', ')}`);
    assert(unexpected.length === 0, `unexpected: ${unexpected.join(', ')}`);
    record('PASS', name, `${expected.length} lifecycle signal(s), 0 fixture reads`);
    return main;
  } finally {
    await page.close();
  }
}

async function primaryFlightStatus() {
  const page = await context.newPage();
  try {
    await page.goto(WEB, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(1500);
    const row = page.locator('main tbody tr').filter({ hasText: PRIMARY }).first();
    assert((await row.count()) === 1, `Command Center row for ${PRIMARY} is missing`);
    const status = (await row.locator('td').nth(5).innerText()).trim();
    assert(status.length > 0, `flight status for ${PRIMARY} is empty`);
    return status;
  } finally {
    await page.close();
  }
}

try {
  const initial = await call('GET', '/incident-groups/current');
  assert(initial.reference === GROUP, `current group is ${initial.reference}`);
  assert(initial.state === 'detected', `fresh journey must start detected, got ${initial.state}`);
  record('PASS', 'disruption starts from the recorded group', `${GROUP} · detected`);

  await inspectPage('Command Center shows the disruption and source boundary', '/', [
    GROUP,
    'real API',
    'Flight status describes the operation itself',
  ]);
  const physicalStatusBefore = await primaryFlightStatus();
  assert(
    physicalStatusBefore.toLowerCase() !== 'resolved',
    `physical flight status must not encode workflow resolution: ${physicalStatusBefore}`,
  );

  const waitingRun = await call('POST', `/incident-groups/${GROUP}/run`);
  const waitingStates = new Set(waitingRun.members.map((member) => member.state));
  assert(waitingStates.has('awaiting_approval'), `member states: ${[...waitingStates].join(', ')}`);

  const incident = await call('GET', `/incidents/${PRIMARY}`);
  assert(incident.state === 'awaiting_approval', `primary state is ${incident.state}`);
  assert(incident.plan?.generator === 'fallback-playbook', 'fallback is not the plan of record');

  const timeline = await call('GET', `/incidents/${PRIMARY}/timeline`);
  const proposals = timeline.entries.filter((entry) => entry.event_type === 'PLAN_PROPOSED');
  const fallbackProposal = proposals.find(
    (entry) =>
      typeof entry.detail?.generator === 'string' &&
      /fallback|playbook|deterministic/i.test(entry.detail.generator),
  );
  const plannerProposal = proposals.find(
    (entry) =>
      typeof entry.detail?.generator === 'string' &&
      !/fallback|playbook|deterministic/i.test(entry.detail.generator),
  );
  assert(fallbackProposal, 'recorded deterministic fallback proposal missing');
  assert(plannerProposal, 'recorded planner proposal missing');
  assert(
    plannerProposal.detail.plan_id !== incident.plan.id,
    'planner candidate was presented as the plan of record without backend authority',
  );
  record(
    'PASS',
    'planner output and deterministic plan are separate recorded facts',
    `plan of record ${incident.plan.generator}; planner ${plannerProposal.detail.generator} (${plannerProposal.detail.llm_mode ?? 'mode unavailable'}) is not selected`,
  );

  const waitingGroup = await call('GET', '/incident-groups/current');
  assert(waitingGroup.awaiting_approval_count > 0, 'no incidents are awaiting approval');
  const impacts = await call('GET', `/incident-groups/${GROUP}/impacts?limit=1000`);
  assert(impacts.passengers_assessed > 0, 'passenger priorities were not recorded');
  const passenger = impacts.passengers[0];
  assert(passenger?.pnr, 'no recorded PNR returned');

  await inspectPage(
    'Recovery Workspace distinguishes planner candidate from fallback',
    `/incidents/${PRIMARY}`,
    [
      'planner candidate',
      'fixture-replayed model output',
      'not selected; the deterministic playbook remains the plan of record',
      'Fallback playbook · deterministic',
      'Latest origin weather observation',
      'scored evidence refs',
    ],
  );
  await inspectPage('Approval Queue separates workflow from gate requirements', '/assurance', [
    `workflow ${waitingGroup.state.replace(/_/g, ' ')}`,
    'human decisions required',
    'incidents awaiting approval',
    'Human-gated tasks',
  ]);
  /*
   * The passenger view now reads `GET /passenger/{booking_ref}/disruption` rather than composing
   * itself from the group summary and the capped impact contract, so the tokens below are that
   * screen's own. The guarantee is unchanged and is what these assertions still hold: the recorded
   * booking is on screen, and no booking OUTCOME is claimed. What is no longer asserted here is the
   * group-level "incidents awaiting operator approval" count, because a per-booking view does not
   * show one — the Approval Queue check above owns that figure.
   */
  await inspectPage(
    'Passenger view shows the recorded booking without a fabricated outcome',
    `/passenger/${passenger.pnr}`,
    [passenger.pnr, 'No confirmed booking change is published', 'does not mean your booking'],
    ['sample booking', 'console-sample', 'An airline colleague is reviewing your rebooking'],
  );

  const groupDetail = await call('GET', `/incident-groups/${GROUP}`);
  let approved = 0;
  for (const flight of groupDetail.flights) {
    if (!flight.incident_reference) continue;
    const assurance = await call('GET', `/incidents/${flight.incident_reference}/assurance`);
    for (const evaluation of assurance.evaluations) {
      if (evaluation.decision !== 'needs_human' || evaluation.human_decision) continue;
      await call('POST', `/assurance/${evaluation.id}/decision`, {
        decision: 'approved',
        actor_id: 'operator-browser-verify',
        reason: 'primary browser journey verification',
      });
      approved += 1;
    }
  }
  assert(approved > 0, 'no human decisions were recorded');
  record('PASS', 'human approvals are persisted individually', `${approved} decisions`);

  let terminal;
  for (let attempt = 0; attempt < 6; attempt += 1) {
    terminal = await call('POST', `/incident-groups/${GROUP}/run`);
    if (['resolved', 'blocked', 'failed'].includes(terminal.state)) break;
  }
  assert(terminal?.state === 'resolved', `group ended ${terminal?.state}`);
  const resolvedGroup = await call('GET', '/incident-groups/current');
  assert(resolvedGroup.state === 'resolved', `group summary is ${resolvedGroup.state}`);
  assert(
    resolvedGroup.awaiting_approval_count === 0,
    'resolved group still has incidents awaiting approval',
  );
  const physicalStatusAfter = await primaryFlightStatus();
  assert(
    physicalStatusAfter === physicalStatusBefore,
    `physical flight status changed with workflow state: ${physicalStatusBefore} -> ${physicalStatusAfter}`,
  );
  assert(
    physicalStatusAfter.toLowerCase() !== 'resolved',
    `physical flight status must remain distinct from workflow resolution: ${physicalStatusAfter}`,
  );
  record(
    'PASS',
    'execution reaches the recorded resolved state',
    `${GROUP} · resolved; ${PRIMARY} flight status remains ${physicalStatusAfter}`,
  );

  await inspectPage(
    'Command Center keeps flight and workflow status separate after resolution',
    '/',
    ['RESOLVED', 'Flight status describes the operation itself', 'Incident linked'],
    ['In recovery'],
  );
  await inspectPage(
    'Recovery Workspace preserves plan attribution after execution',
    `/incidents/${PRIMARY}`,
    ['RESOLVED', 'planner candidate', 'Fallback playbook · deterministic', 'Actions executed'],
  );
  const approvalBody = await inspectPage(
    'Approval Queue reports resolved workflow and zero pending',
    '/assurance',
    ['workflow resolved', 'incidents awaiting approval', 'Human-gated tasks'],
  );
  assert(
    /incidents awaiting approval\s+0/i.test(approvalBody),
    'approval page does not show zero incidents awaiting approval',
  );
  record('PASS', 'resolved approval page does not turn a plan rule into pending state');

  await inspectPage(
    'Passenger view reports workflow completion without inventing booking completion',
    `/passenger/${passenger.pnr}`,
    [
      'The disruption is closed',
      'No confirmed booking change is published',
      'A resolved disruption means the operational workflow finished',
      'does not mean your booking was changed',
    ],
    ['sample booking', 'console-sample', 'An airline colleague is reviewing your rebooking'],
  );
} catch (error) {
  record('FAIL', 'primary browser journey', String(error instanceof Error ? error.message : error));
} finally {
  await browser.close();
}

const failures = results.filter((result) => result.state === 'FAIL');
console.log(
  `\n${results.length - failures.length}/${results.length} primary journey checks passed`,
);
if (failures.length > 0) process.exit(1);
console.log('\nPrimary browser journey verified against recorded API state.');
