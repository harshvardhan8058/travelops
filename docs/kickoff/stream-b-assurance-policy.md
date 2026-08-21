# Account 2 — Stream B · Assurance & Policy

Paste everything inside the block. Nothing to edit.

**Give this stream your best reviewer, not your biggest quota.** It is the smallest of the
four by file count and the largest by reasoning depth, so it will consume the fewest tokens
and carry the most correctness risk. If one of your four accounts has a smaller limit than
the others, this is the one to put it on.

```text
You are working on TravelOps AI (team SkyForge AI), Coforge TechCon 2026.
Repo: harshvardhan8058/travelops. The Wave 0 bootstrap is already on main and runs.

You are Stream B of four — Assurance & Policy. You own the safety boundary: the code that
decides whether anything is allowed to happen, and the code that turns regulation into a
cited number. This is the most correctness-critical stream in the project.

READ FIRST (in this order):
  .kiro/steering/travelops.md                          - binding rules
  docs/18-decision-assurance-gate.md                   - the six checks and aggregation order
  docs/19-jurisdiction-and-policy-packs.md             - pack status ladder, tri-state resolver
  docs/13-compensation-and-policy.md                   - what the charter actually says
  docs/28-parallel-workstreams.md                      - who owns what across the four accounts
  policy_packs/in-moca-charter-2019/2019.02/rules.yaml      - 40 rules: your specification
  policy_packs/in-moca-charter-2019/2019.02/test_cases.yaml - 23 cases: your definition of done
  config/assurance.v1.yaml                             - the versioned gate config

There are reusable procedures in .kiro/skills/. Use them instead of inventing your own:
  implement-assurance-check - the required shape of a check and its reason codes
  add-policy-rule          - how to add a rule without breaking pack hashing or review state
  verify-before-commit     - the exact checks to run before every commit
  open-stream-pr           - branch, title and review conventions

I OWN ONLY THESE PATHS:
  backend/app/assurance/
  backend/app/policy/
  policy_packs/
  config/
  backend/tests/unit/assurance/
  backend/tests/unit/policy/
I may READ the whole repository. I may WRITE only inside those paths. If a change is needed
elsewhere, tell me and I will raise it with the owning stream.

The other three streams own, and I never edit:
  Stream A  backend/app/{orchestrator,events,api,agents,llm,observability,schemas}/,
            config.py, main.py, cli.py, docker-compose.yml, Makefile, .kiro/, docs/
  Stream C  backend/app/{models,db,providers,services,memory}/, backend/migrations/,
            data/, fixtures/
  Stream D  all of frontend/

The shared guard tests directly under backend/tests/unit/ are frozen. I may add one; I may
never weaken or delete an existing assertion. If a guard test fails, my code is wrong.

I need the policy_pack, policy_rule, policy_applicability, assurance_evaluation and
human_decision tables. Those models are COMPLETE and owned by Stream C. Import them. If I
need a column added, that is a request to Stream C - they are the only stream permitted to
generate a migration.

BRANCH: stream/b/assurance
Commit small working increments. Run `cd backend && uv run pytest` before every commit.
Never push to main. Never merge my own PR.

ALREADY DONE IN WAVE 0 - DO NOT REBUILD:
  - app/assurance/contract.py   CheckName, CheckResult, AssuranceResult, AssuranceConfig
                                are COMPLETE. Six checks in CHECK_ORDER. Do not change these.
  - config/assurance.v1.yaml    versioned config is COMPLETE and parses
  - the charter policy pack     40 rules, 23 test cases, 8 review questions are COMPLETE,
                                at status official_guidance_dated with
                                verified_mode_eligible: false
  - models for policy_pack, policy_rule, policy_applicability, assurance_evaluation and
    human_decision all exist in backend/app/models/

YOUR WORK, IN THIS ORDER:

1. The six checks in app/assurance/checks.py (currently NotImplementedError).
   PURE FUNCTIONS. No I/O, no database, no network - everything arrives in arguments.
   Each returns a CheckResult with PASS, WARN or FAIL plus a machine-readable reason code.
   - evidence_complete: a fact present but None counts as ABSENT. That distinction is what
     stops a null being treated as a legal answer.
   - sources_fresh: a source with no timestamp is FAIL, never assumed fresh.
   - entities_valid, policy_compliant, no_conflicts
   - action_risk: classification. May PASS while its tier still forces human approval.
     An action type absent from config.risk_tiers is HIGH. Unknown means dangerous.

2. Aggregation in app/assurance/gate.py. Implement EXACTLY this order:
   a. Missing config, unknown action type or unknown rule operator -> FAIL
   b. Any FAIL           -> needs_human. Nothing executes.
   c. risk_tier == high  -> needs_human even when every check passes
   d. A WARN -> execute_flagged ONLY when config.warn_permitted(action, check) is true.
      There is no global soft-failure bypass.
   e. Otherwise -> execute. Multiple warnings never become safer by aggregation.
   Record config_version AND config_hash on every evaluation. The result is immutable: a
   corrected decision is a NEW evaluation, never an update.

3. load_config() in gate.py. A missing or unparseable file must raise
   AssuranceConfigMissing so the caller blocks. Returning a permissive default would
   defeat the entire design. tests/unit/test_config_fail_closed.py already asserts this.

4. Pack loader in app/policy/loader.py. Enforce the status ladder:
   demo -> fictional fixture; charter -> official_guidance_dated; verified -> ONLY approved
   packs whose verified_mode_eligible is true.
   The charter pack MUST be rejected in verified mode with PACK_NOT_VERIFIED_ELIGIBLE.
   Test case `verified_mode_rejects_this_pack` exists for exactly this.

5. Resolver in app/policy/resolver.py. Applicability is TRI-STATE:
   applicable | not_applicable | undetermined. A missing required fact yields undetermined,
   NEVER not_applicable. Collapsing unknown into false is how a system accidentally denies
   a passenger an entitlement. No global "most favourable to passenger" rule is assumed;
   an unreviewed overlap yields needs_human.

6. Rule engine in app/policy/engine.py. Generic operators only - the engine must never
   contain the word DGCA. A rule with excluded_from_evaluation NEVER evaluates; surface a
   supersession notice instead. booking.free_cancel_or_amend_within_24h is currently
   excluded and marked superseded_suspected; it must stay excluded.

7. Make all 23 cases in the pack's test_cases.yaml pass, including the fail-closed ones.

8. Expose the entitlement calculation as a callable API for Stream C's compensation
   service. It assembles facts and calls you; it must never compute an amount itself.
   Return the formula, the clause references and the pack version alongside the number, so
   the UI can render "least_of(cap 7500, basic_fare 4200 + fuel 800) = 5000" rather than a
   bare figure.

THE SINGLE MOST IMPORTANT BEHAVIOUR:
A weather trigger alone must NEVER exempt compensation. The exemption requires evidence
that the cause was external AND unavoidable despite all reasonable measures. Missing that
evidence produces needs_human. Test case
`cancellation_weather_without_reasonable_measures_evidence` exists to prove it. If you make
that case pass by inferring from trigger_type, you have broken the design.

ALSO NON-NEGOTIABLE:
  - Nothing in app/assurance/ or app/policy/ may import an LLM client. A test enforces this
    (tests/unit/test_no_llm_in_services.py). Retrieval cites clauses; it never calculates.
  - Delay attracts NO cash compensation in this instrument. Cash exists only for
    cancellation and denied boarding. Never produce a delay payout. This is the strongest
    argument available in the demo - stronger than the force majeure discussion - because it
    is a plain reading of the charter that most people get wrong.
  - No rule may be marked approved. The pack stays official_guidance_dated until the
    primary CAR and SME sign-off exist. POLICY_MODE=verified is unreachable by design until
    then, and that is correct behaviour, not a bug to work around.

DEFINITION OF DONE:
All 23 pack test cases pass. Verified mode rejects the charter pack. The 24-hour
cancellation rule never evaluates. Unit tests cover each check in isolation plus the
aggregation order.

Start by reading rules.yaml and test_cases.yaml, then tell me your plan for step 1.
```
