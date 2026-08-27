# 24. Input Ownership and Acquisition Checklist

This is the complete acquisition list. **Implementation can start immediately.** Public-source downloads
are development-owned first; the team is asked only for access-controlled material, credentials,
review authority, final-device checks, or escalation when a public portal blocks automated access.
Items below block specific claims or integrations—not the deterministic Stage 2 vertical slice.

Never paste API keys, passwords, app passwords, personal email addresses, passenger data or internal
Coforge-only content into Git or chat. Secrets go into local `.env`; internal documents remain in the
approved Coforge location unless sharing is authorised.

## Priority summary

| Priority | Input | Primary owner | What the team may need to do | Fallback |
| --- | --- | --- | --- | --- |
| ✅ **Received** | MoCA Passenger Charter, Feb 2019 | Team — **done 2026-08-20** | Optionally confirm PDF redistribution so we can archive the file and hash it | Encoded as `in-moca-charter-2019` |
| **P0** | Current DGCA CAR Part IV + amendments + revision metadata | Development first | Escalate only if portal/device policy blocks access; approve storage route | `charter` mode with dated-source badge |
| **P0** | Aviation/legal SME sign-off on the encoded charter rules | **Team** | Nominate authorised reviewer; they complete `review.yaml` | Remain in `POLICY_MODE=charter`, never `verified` |
| ✅ **Confirmed** | Demo machine runs Docker | Team — **done 2026-08-20** | Re-verify projector and offline backup nearer the checkpoint | — |
| **P1** | Groq key + observed limits | **Team, in progress** | Key stays in local `.env`; share only the console limits | Fixture/off modes |
| ⏸ **Deferred by team** | Coforge internal tools | Team will verify separately | — | Claim omitted; no invented integration |
| **P1** | Controlled SMTP/Mailtrap setup | **Team** | Configure local secret + allowlisted inboxes | Console/simulated notifications |
| **P1** | Coforge internal-tool names and access | **Team** | Obtain official eligibility/access | Omit claim; never invent integration |
| **P1** | AIKosh schedule artifact + terms | Development first | Escalate only if login/download restriction blocks access | Synthetic schedules |
| **P2** | Airline-operations SME interview notes | **Team** | Arrange short validation call | Label problem/value as hypothesis |
| **P2** | Optional brand assets | **Team, optional** | Provide approved SVG/PNG | Text-only SkyForge identity |

## P0-1 — Current DGCA primary document (development first; team escalation only if blocked)

The development side first attempts the official public download, archives metadata/hash and inspects
redistribution terms. The team does **not** need to duplicate that work. Team action is required only if
the DGCA portal blocks automated/public retrieval, a compliant corporate device is needed, or approved
internal storage/redistribution must be arranged.

### What to obtain

1. The current official PDF titled **DGCA Civil Aviation Requirements, Section 3, Series M, Part IV**
   concerning passenger facilities for denied boarding, cancellation and delay.
2. The page or screenshot showing **issue/revision number, publication/effective date and current
   status**.
3. Every amendment/corrigendum incorporated after that revision, or confirmation that the downloaded
   copy is consolidated.
4. Any official definitions or annexures referenced by the relevant clauses.

### Where to get it

- Start at the official [DGCA website](https://www.dgca.gov.in/).
- Navigate: **Regulations → Civil Aviation Requirements → Section 3 → Series M → Part IV**.
- If the portal attachment cannot be downloaded on the managed device, use a Coforge-compliant/domain-
  joined device, or ask the mentor/project aviation SME to download it from DGCA and share it through
  approved Coforge storage.
- The Ministry of Civil Aviation [Passenger Charter](https://www.civilaviation.gov.in/sites/default/files/2023-01/Passenger%20Charter%20MoCA%20India%20Feb%202019%20(1).pdf)
  is useful supporting material, but **does not replace the current CAR**.

### Handoff if development is blocked

If the public fetch succeeds, development creates the following structure. If it fails due to portal or
managed-device restrictions, the team provides an approved internal path or attaches the files only when
policy permits:

```text
policy_packs/in-dgca-car-3m4/<revision>/
  source.pdf
  amendments/
  source-metadata.yaml
```

`source-metadata.yaml` needs official URL, retrieved date, issue/revision, effective date, SHA-256 hash,
and redistribution note.

### Acceptance test

We can identify exact clauses for applicability, delay care, cancellation, denied boarding (if kept),
notice windows, formulas, exemptions, definitions and amendment history. If any remains ambiguous, the
rule remains draft.

### Deadline/fallback

Needed before **Stage 3 (1–2 September)** to reach verified regulatory intelligence. If unavailable, the
system runs `POLICY_MODE=charter` against the encoded February 2019 charter pack — real cited figures, with
a visible badge stating the source is dated and pending CAR verification.

**Two specific questions the primary source must settle:**

1. Did the reported **August 2024 revision of Part IV** change any encoded figure or threshold?
2. Did the reported **February 2026 amendment to Part II** move the no-charge cancellation window from 24
   to 48 hours? That rule is currently excluded from evaluation.

## P0-2 — India policy-pack SME review

### Who

A Coforge TTH/airline-domain SME, compliance/legal reviewer, or mentor explicitly willing to validate
our technical interpretation. Ask the mentor to nominate the right reviewer; do not assume any teammate
is authorised legal review.

### What to provide them

We will prepare a rule-review sheet after the source arrives. The reviewer fills/approves:

| Field | Required |
| --- | --- |
| Pack version/source hash | Yes |
| Rule ID and clause reference | Yes |
| Plain-language interpretation | Yes |
| Required input facts | Yes |
| Formula/output and currency | Yes |
| Edge cases/exemptions | Yes |
| Applicability/overlap handling | Yes |
| Test cases | Yes |
| Reviewer name/role/date/status | Yes |

### Where to arrange

- Ask the scheduled mentor directly.
- Ask the Arcolab/CIMS delivery or architecture lead for a TTH airline SME.
- If nobody internal is available, email the official hackathon contact shown on the TechCon page:
  `TechCon.x@Coforge.com`, requesting the approved path for domain validation.

### Deadline/fallback

Same as P0-1. No approval means no `VERIFIED` badge and no authoritative entitlement claim.

## P0-3 — Demo machine and network readiness

The SharePoint banner in the supplied page shows some actions are restricted on non-compliant devices.
That makes environment readiness a real risk.

### Team must verify on the exact presentation machine

- Docker/Docker Compose can be installed and run, or an approved alternative host exists.
- At least 8 GB free RAM, 10 GB disk, Chrome/Edge, and 1920×1080 output.
- Ports for frontend/API/Postgres/Redis are available or configurable.
- GitHub access and the repository can be cloned/pulled.
- If live mode is planned: HTTPS to AWC, Groq and SMTP provider is permitted on the venue/network.
- Screen sharing works and browser text is legible on the projector.
- A local backup video plays with Wi-Fi disabled.

### Where/how

Run the future `make doctor` and `make demo` on the same device and corporate network used for the
checkpoint. If Docker is blocked, ask Coforge IT/project DevOps for an approved local runtime or a
project cloud VM before Stage 2. Do not discover this during evaluation.

### Deadline/fallback

Verify before **Stage 2 (20–24 August)**. Network fallback is full fixture/offline mode. Runtime fallback
must be arranged by the team; a video alone is insurance, not the primary demo.

## P1-1 — Groq account and current limits

### Team action

1. Sign in at [Groq Console](https://console.groq.com/).
2. Confirm `openai/gpt-oss-120b` appears under supported models. Groq retired
   `llama-3.3-70b-versatile` on 2026-08-16 for free and developer tiers; requests to it return
   HTTP 400 `model_decommissioned`. Check [deprecations](https://console.groq.com/docs/deprecations)
   before a demo — a retired model takes down the planner candidate and both prose endpoints at
   once, and nothing in the test suite can see it coming.
3. Open the account rate-limit page and record RPM/RPD/TPM/TPD shown for this exact account/model.
4. Create a project-scoped API key.
5. Put it only in local `GROQ_API_KEY`; never send or commit it.

A screenshot/text of the **limits only** is safe to share. The secret is not.

### Deadline/fallback

Before Stage 3 live-agent work. Fixture and off modes unblock all earlier work.

## P1-2 — Demo email

### Preferred options

**Mailtrap:** create a project inbox, copy SMTP settings into local environment, and keep delivery inside
Mailtrap for development.

**Gmail:** use a team-controlled demo account. If organisational policy allows SMTP app passwords,
enable 2-Step Verification and follow Google's [App Password guidance](https://support.google.com/mail/answer/185833?hl=en).
Use a dedicated app password, never the account password. Workspace administrators may disable this;
ask IT rather than bypassing policy.

### Required local values

`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and an allowlist of 2–3 recipient addresses.
Do not put addresses in fixtures or screenshots. Bulk channels remain simulated.

### Deadline/fallback

Before semi-final rehearsal. Console delivery records are sufficient for Stage 2/3.

## P1-3 — Coforge internal tools

### What counts as useful input

An official name, internal documentation URL, permitted use, team access confirmation and—ideally—a
small integration surface for one of:

- approved AI/model gateway
- internal developer platform or API gateway
- design system/component library
- observability/logging platform
- CI/security/code-quality tooling
- airline/TTH accelerator or reference architecture

### Where to obtain it

1. Open the TechCon SharePoint “use of internal tools” or linked resources section if available.
2. Ask the mentor: **“Which Coforge internal tools are eligible for this criterion, and which are our
   team permitted to integrate?”**
3. Ask the Arcolab/CIMS architecture or platform lead.
4. If still unclear, email `TechCon.x@Coforge.com`.

### Acceptance/fallback

We need both an official name and actual access. A slide/logo without a real use is worse than omission.
If nothing is available, state honestly that the prototype uses open components and provider interfaces;
do not invent a Coforge product or placeholder.

## P1-4 — AIKosh flight-schedule artifact (development first; team escalation only if blocked)

Development first attempts the public catalogue/download, archives the raw artifact and terms, hashes
it, inspects columns/encoding/time zones and writes the loader contract test. The team is asked only if
the portal requires an organisational login, a compliant device, or a manual download development
cannot perform.

### Where

- Catalogue: [AIKosh Flight Schedule](https://aikosh.indiaai.gov.in/home/datasets/details/flight_schedule.html).
- Download the actual CSV/ZIP/JSON plus displayed licence/terms and update date.

### Handoff if escalation is needed

Provide the untouched original CSV/ZIP/JSON, URL, download date, licence screenshot/text and any data
dictionary through an approved path. Never edit the raw file before hashing.

### Acceptance/fallback

Until the file passes inspection, the UI must call schedules synthetic. Fallback schedules are already
part of the fixture plan, so this never blocks Stage 2.

## P2-1 — Airline operations SME validation

A 20–30 minute call is enough. Ask:

1. Which disruption-recovery tasks are most fragmented or slow?
2. Which actions may be automated and which always require approval?
3. What information must an operator see before approving a recovery?
4. Are crew pairings/positioning an accurate explanation for the 8→9 cascade?
5. Which prototype metric would indicate value: time to plan, passengers reaccommodated, missed
   connections avoided, or something else?

Write dated notes with role/organisation (no confidential details) and explicit “validated / corrected /
not confirmed” outcomes. Without this, problem and value claims remain hypotheses.

## P2-2 — Optional visual assets

Not required. If the team has an approved SkyForge AI logo, provide SVG/PNG plus permission to use it.
Do not use airline logos without permission. The default UI uses text identity and the no-purple design
system, which is sufficient.

## Inputs we do **not** need from the team

Do not spend time trying to arrange:

- real passenger/PNR data
- paid flight-status, hotel/GDS or SMS APIs
- real bookings, payments or refunds
- crew duty-time legality rules/certification for the MVP
- Kubernetes, Kafka, RabbitMQ, Neo4j or a graph database
- MCP, LangGraph, CrewAI or AutoGen
- EU/UK/US regulatory packs before the India path works
- production cloud infrastructure for Stage 2

## Agent-owned acquisition

The development side owns first-attempt public acquisition and archiving for: the DGCA public document,
AIKosh artifact, AWC API contract, OurAirports CSV, Open-Meteo attribution, Docling, public package
dependencies, synthetic data, fixture schedules, UI assets/icons and all implementation code. The team
is asked only for access-controlled, credentialed, review-authority or final-environment inputs—and to
escalate if a public portal/device policy blocks development.

*Public web-source content was summarized and rephrased for licensing compliance.*
