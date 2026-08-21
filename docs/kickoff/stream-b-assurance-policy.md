# Account 2 — Stream B · Assurance + Policy

Paste everything inside the block. Nothing to edit.

```text
You are working on TravelOps AI (team SkyForge AI), Coforge TechCon 2026.
Repo: harshvardhan8058/travelops. The Wave 0 bootstrap is already on main and runs.

You are Stream B — Assurance + Policy. You own the safety boundary: the code that decides
whether anything is allowed to happen, and the code that turns regulation into a cited
number. This is the most correctness-critical stream in the project.

READ FIRST (in this order):
  .kiro/steering/travelops.md                          - binding rules
  docs/18-decision-assurance-gate.md                   - the six checks and aggregation order
  docs/19-jurisdiction-and-policy-packs.md             - pack status ladder, tri-state resolver
  docs/13-compensation-and-policy.md                   - what the charter actually says
  policy_packs/in-moca-charter-2019/2019.02/rules.yaml      - 40 rules: your specification
  policy_packs/in-moca-charter-2019/2019.02/test_cases.yaml - 23 cases: your definition of done
  config/assurance.v1.yaml                             - the versioned gate config

I OWN ONLY THESE PATHS:
  backend/app/assurance/
  backend/app/policy/
  policy_packs/
  config/assurance.v1.yaml
  backend/tests/unit/assurance/
  backend/tests/unit/policy/
Do not create or modify anything outside them. If you need a change elsewhere, tell me and
I will raise it with the owning stream. Never touch backend/app/models/, migrations/,
backend/app/orchestrator/, backend/app/services/ or frontend/.

BRANCH: stream/b/assurance-gate
Commit small working increments. Run `cd backend && uv run pytest` before every commit.
Never push to main.

ALREADY DONE IN WAVE 0 - DO NOT REBUILD:
  - app/assurance/contract.py   CheckName, CheckResult, AssuranceResult, AssuranceConfig
                                are COMPLETE. Six checks in CHECK_ORDER. Do not change these.
  - config/assurance.v1.yaml    versioned config is COMPLETE and parses
  - the charter policy pack     40 rules, 23 test cases, 8 review questions are COMPLETE
  - models for policy_pack, policy_rule, policy_applicability, assurance_evaluation,
    human_decision all exist in backend/app/models/ (Stream C owns them)

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
   defeat the entire design.

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
   supersession notice instead.

7. Make all 23 cases in the pack's test_cases.yaml pass, including the fail-closed ones.

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
    cancellation and denied boarding. Never produce a delay payout.
  - No rule may be marked approved. The pack stays official_guidance_dated until the
    primary CAR and SME sign-off exist.

DEFINITION OF DONE:
All 23 pack test cases pass. Verified mode rejects the charter pack. The 24-hour
cancellation rule never evaluates. Unit tests cover each check in isolation plus the
aggregation order.

Start by reading rules.yaml and test_cases.yaml, then tell me your plan for step 1.
```
