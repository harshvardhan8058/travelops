# Open Questions

Everything previously listed here has been answered — see [`DECISIONS.md`](DECISIONS.md).

What follows is what genuinely remains. It is short, but the first item is significant.

---

## 1. What are Coforge's internal tools? ⚠️ **Blocking, and it costs score**

**"Use of Internal Tools" is one of six official judging criteria** — roughly a sixth of the score —
and it is the only criterion the current design does not address at all.

Every other criterion is covered:

| Criterion | Covered by |
| --- | --- |
| Creativity | Cascading recovery, force-majeure-aware compensation, replay |
| Feasibility | Working end-to-end demo, real weather, deterministic fallback |
| Relevance | Real DGCA rules, Indian airports, genuine operational problem |
| **Use of Internal Tools** | ❌ **Nothing** |
| Use of Open Source | FastAPI, React, Postgres, Redis, OurAirports, Open-Meteo |
| Engineering the Autonomous Enterprise | Event-driven orchestration, agents that decide and execute |

I have no way to research this — Coforge's internal tooling is not public. What would help:

- An internal developer platform, API gateway, or service catalogue?
- An internal LLM gateway or AI platform?
- Internal design system or component library?
- Internal observability, CI, or data platform?
- Anything the hackathon brief names explicitly?

**Even a shallow integration scores here.** Deploying behind an internal gateway, using an internal
component library for the dashboard, or pushing logs to an internal observability stack would each turn
a zero into a mark. Send me whatever the brief says, or a list of what your team has access to, and I
will work it into the architecture.

This is the highest-value outstanding item by a wide margin.

---

## 2. Crew scope — confirm the boundary

The cascade includes "9 crew changes". I have scoped this as **coordination and display only**:

- ✅ Show which crew rotations are affected
- ✅ Flag a rotation as at-risk against an indicative duty-hour limit
- ✅ Record reassignments
- ❌ **No duty-time legality validation**

Reasoning: crew legality is a genuinely hard regulated domain and would consume the sprint. It is also
the one area where getting it subtly wrong is worse than not doing it — an aviation-literate judge will
spot an incorrect legality claim immediately.

The schema in [`11-data-model.md`](11-data-model.md) reflects this, with `duty_hours_limit` as a display
flag rather than a compliance decision.

**Confirm this is what you meant**, or tell me if you want real duty-time rules — in which case
something else comes off the must-build list.

---

## 3. Verify the DGCA figures against the primary source

[`13-compensation-and-policy.md`](13-compensation-and-policy.md) has the real rule *structure*, sourced
from legal commentary. Three things still need checking against the actual CAR PDF from dgca.gov.in:

- Exact rupee values for cancellation compensation (₹5,000 / ₹7,500 / ₹10,000 banding)
- The definition of "nighttime" hours that triggers hotel entitlement
- Whether cancellation banding is by **block time** or **route distance** — sources differ

I could not fetch the PDF directly. Someone should download it and confirm. The force majeure finding —
that weather exempts cash compensation but not duty of care — is well corroborated and I am confident in
it; the specific numbers are what need verifying.

This matters because the demo cites a regulation by name. Citing it wrongly is worse than not citing it.

---

## 4. Two smaller confirmations

**Demo email inboxes.** [`DECISIONS.md`](DECISIONS.md) sets Mailtrap for development and real Gmail for
the demo. You need 2–3 addresses you control. Don't send them here — just put them in local config.

**Groq token budget.** The free tier gives roughly 100K tokens/day, about 25–50 planner calls. The
fixture/replay mode in [`16-folder-structure.md`](16-folder-structure.md) is a Day 1 task specifically
to protect this. Worth confirming your account's actual limits early rather than discovering them on
Day 3.

---

## Not recoverable

`TravelOps_AI_Startup_Blueprint.docx` arrived as raw ZIP binary and the byte stream was corrupted when
pasted as text. `.docx` is a compressed archive, so it cannot survive being pasted into chat.

`TravelOps_AI_Master_Blueprint.txt` **was** recovered and is preserved at
[`reference/master-blueprint.md`](reference/master-blueprint.md), with a table of deliberate divergences
from it.

Based on the original conversation, the `.docx` was the *first, shorter* version of the blueprint, so
the recovered Master Blueprint plus the decisions since very likely supersede it. If a specific section
of it matters, paste that section as plain text and I will reconcile it.
