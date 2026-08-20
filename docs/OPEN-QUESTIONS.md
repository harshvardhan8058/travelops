# Open Questions and External Dependencies

Architecture/product choices are settled. These are remaining external facts, access rights or reviews.
Ownership and escalation rules are in [`24-input-acquisition.md`](24-input-acquisition.md); public-source
retrieval is development-owned first.

## 1. DGCA primary source and review — blocks verified legal claims

Development first attempts the official public CAR download and metadata archive. Team help is needed
only if portal/device restrictions block access, and for the authorised SME review of the resulting rule
sheet.

Still needed:

- current CAR Section 3, Series M, Part IV PDF
- revision/effective metadata and amendments
- reviewed rule sheet from an authorised aviation/legal/domain SME

Until supplied, use `POLICY_MODE=demo`, display `DEMO POLICY FIXTURE`, and show no legally authoritative
rupee amount. This does **not** block Stage 2 engineering.

## 2. Coforge internal tools — blocks only the internal-tool scoring claim

Need an official eligible tool name, internal documentation, actual team access and a real use. Ask the
mentor, Arcolab/CIMS architecture/platform lead, TechCon SharePoint resources, or
`TechCon.x@Coforge.com`.

Do not invent a Coforge tool, use a placeholder logo, or integrate something only to tick a box. If
nothing is available, omit the claim and explain the provider boundary.

## 3. Demo environment — blocks reliable live presentation

Need confirmation that the exact presentation device can run Docker Compose, access GitHub and—if live
mode is planned—reach AWC, Groq and SMTP. The supplied SharePoint page warns that non-compliant devices
cannot download/sync, so this must be tested rather than assumed.

Offline fixture mode handles network loss. A Docker/runtime restriction still requires the team's IT or
project DevOps support.

## 4. Credentialed enhancements — do not share secrets

- Groq project API key and account-specific rate-limit screenshot/text
- Mailtrap or approved Gmail/SMTP configuration
- 2–3 controlled demo recipient addresses stored only in local allowlist

Fixture/off/console modes unblock development if these are delayed.

## 5. AIKosh schedule file — blocks calling schedules “real”

The catalogue page is known, but the raw artifact, schema and licence are not archived. Development owns
the first download/validation attempt. The team is asked only if login or managed-device restrictions
block access. Until validation passes, use synthetic schedules and label them honestly.

## 6. Optional SME validation — improves problem/value credibility

A short airline-operations SME interview should validate the disruption workflow, automation/approval
boundary, crew-pairing explanation and meaningful prototype metric. Without it, these remain design
hypotheses—not measured user research.

## Explicitly settled; do not ask again

- Team: SkyForge AI; project: TravelOps AI; Registration ID 201.
- Operations Controller is the primary user.
- Crew scope is impact/coordination only, not legality validation.
- Bookings, passenger data, hotels, transport, flight status and bulk channels are simulated/synthetic.
- Stack is FastAPI + React/TypeScript + Postgres + Redis + Groq + Docker.
- Custom orchestrator; MCP/LangGraph/CrewAI are not required.
- Three reasoning agents, ten deterministic services, one orchestrator.
- Decision Assurance Gate replaces confidence-based execution.
- Submitted presentation is frozen.
- UI is graphite/instrument-cyan with no purple or default AI aesthetic.
