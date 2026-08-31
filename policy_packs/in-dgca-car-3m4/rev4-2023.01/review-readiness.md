# G1 review readiness — DGCA CAR Section 3, Series 'M', Parts IV and II

This pack is **ready to be reviewed**. It is not reviewed, not approved, and not current law.

`review.yaml` is the authoritative record of what is open. This file is a reviewer's map onto it:
it separates what the documents establish from what nobody has yet decided, so a reviewer never
has to guess which is which.

| Field | Value |
| --- | --- |
| `status` | `official_guidance_dated` |
| `verified_mode_eligible` | `false` |
| `review_status` | `pending` |
| `pack_hash` | `7c45e7b15ae54f6e` |
| `source_document_verified` | `true` |
| `may_be_called_current_law` | `false` |

## 1. Facts established by the archived DGCA documents

Machine-checked against the archived bytes in this directory. The per-rule record is
`clause-verification.yaml`; the counts below come from it.

| | Count |
| --- | --- |
| Rules total | 43 |
| Citing Part IV, clause and figures located in the archived text layer | 30 |
| Citing Part II, clause-mapped but **not** machine-verifiable | 13 |
| Computational rules lacking a clause reference | 0 |

Part IV (`source.pdf`) carries a real text layer, so every clause number and every literal figure
was confirmed by string match against the archived file. Part II (`source-part-ii.pdf`) is a scan
with no text layer, so its 13 rules are mapped to clauses but **must be read visually** by the
reviewer. That asymmetry is a property of the documents, not a gap in the work.

Figures confirmed verbatim from the archived Part IV bytes:

- **Denied boarding, Para 3.2.2** — no compensation if the alternate departs within one hour;
  otherwise 200% of booked one-way basic fare plus airline fuel charge capped at INR 10,000
  (alternate within 24 h), 400% capped at INR 20,000 (alternate beyond 24 h), and 400% capped at
  INR 20,000 plus full ticket refund where no alternate is taken.
- **Cancellation, Para 3.3.2** — the lesser of INR 5,000 / 7,500 / 10,000 and basic fare plus fuel
  charge, at block times up to 1 h, over 1 h to 2 h, and over 2 h.
- **Delay, Para 3.4.1** — facilities at 2 h for block time up to 2½ h, 3 h for over 2½ h to 5 h,
  4 h otherwise. **Para 3.4.2** — domestic delay over 6 h gives a choice of an alternate within
  6 hours or a full refund. **Para 3.4.3** — hotel under Para 3.8.1(b) when total delay exceeds
  24 h, or exceeds 6 h for a departure scheduled between 2000 and 0300 hrs.
- **Downgrading, Para 3.5.1** — 75% of ticket cost including taxes domestically; 30% / 50% / 75%
  internationally at the 1500 km and 3500 km bands.
- **Alternate airport, Para 3.10.1** — the airline bears the transfer cost unless it gave at least
  6 hours' notice.

Two findings worth the reviewer's attention, because both were verified as **negative** facts —
things the regulation does *not* say:

- **Para 3.4.3 contains no advance-notice condition at all.** The full clause was extracted and
  read; there is no communication precondition on hotel accommodation. The 2019 booklet the
  charter pack was built from described one. This is RQ-1.
- **The entire Para 3.4 delay section contains no INR figure, no percentage and not one
  occurrence of the word "compensation."** So when Para 3.4.4 excuses adherence to "Para 3.8", it
  cannot be excusing a cash payment — there is none in Para 3.4 to excuse. It is excusing the
  Para 3.8.1 facilities themselves: meals and refreshments, and hotel including transfers. This is
  RQ-2, and it is materially harsher than the charter encoding.

## 2. Unresolved supersession and currentness questions

Neither can be settled from inside this repository. Both are recorded as **unanswered**.

| Question | What is unresolved | Why the repository cannot close it |
| --- | --- | --- |
| RQ-9 | Whether Rev. 4 of 25 Jan 2023 is still the current revision of Part IV | The DGCA portal CAR listing is a JavaScript shell with no server-rendered index, and `digigov-portal/api/dgca/getCarList` returns 404, so revisions cannot be enumerated |
| RQ-9 | How Para 3.3.5's refund cross-reference should be read, given the archived Part II (Rev. 3, 24 Feb 2026) **postdates** Part IV Rev. 4 (25 Jan 2023) | A cross-instrument question about legislative intent, not a lookup |

Absence of evidence of a later revision is recorded in `source-metadata.yaml` under
`supersession_check`, alongside its own caveat. It is **not** treated as proof of currentness, and
that is why `may_be_called_current_law` is `false` regardless of source integrity being verified.

## 3. Human SME / legal decisions required

| RQ | Affected rule(s) | Source clause | Decision required | Status |
| --- | --- | --- | --- | --- |
| RQ-1 | `delay.care.hotel.long_or_night_window` | `3.4.3` | Is hotel accommodation genuinely independent of advance notice? If yes, the charter pack under-served passengers and should be corrected against this pack | unanswered |
| RQ-2 | `delay.exemption.extraordinary_circumstances` | `3.4.4`, `1.4`, `1.5` | Does the exemption suppress the Para 3.8 facilities themselves — meals *and* hotel — or only a cash payment? | unanswered |
| RQ-3 | `cancellation.compensation.block_upto_60`, `.block_60_to_120`, `.block_over_120` | `3.3.2` | Para 3.3.2 is disjunctive: alternate flight **or** compensation plus refund. Should an accepted alternate suppress the cash entitlement, and what evidence establishes acceptance? | unanswered |
| RQ-4 | all six `denied_boarding.*` and `cancellation.compensation.*` cash rules | `3.2.2`, `3.3.2` | Confirm the definitions of "booked one-way basic fare" and "airline fuel charge", and that no other fare component enters the lesser-of and percentage calculations | unanswered |
| RQ-5 | `cancellation.exemption.extraordinary_circumstances`, `delay.exemption.extraordinary_circumstances` | `1.4`, `1.5`, `3.3.4`, `3.4.4` | What evidence satisfies the "could not have been avoided even if all reasonable measures had been taken" limb? The engine refuses to treat a weather trigger as an automatic exemption | unanswered |
| RQ-6 | `foreign_carrier.compensation_basis` | `3.6.1` | Para 3.6.1 offers country-of-origin regulations **or** this CAR's figures without stating precedence. Who chooses, on what basis? | unanswered |
| RQ-7 | `denied_boarding.connecting_flight_first_leg` | `3.2.3` | Confirm the provision stays informational and uncomputed, given it needs an arrival-delay fact and a rule-to-rule cross-reference the DSL cannot express | unanswered |
| RQ-8 | `downgrading.involuntary.reimbursement` | `3.5.1` | Accept the omission, or require a ticket-cost formula and a sector-distance fact so the 75% / 30-50-75% bands can compute | unanswered |
| RQ-9 | every rule in the pack | `1.1`, `3.3.5` | Currentness and the Part IV → later Part II cross-reference (section 2 above) | unanswered |
| RQ-11 | `refund.look_in_option_48h` | `car:3m2:rev3:3(e)` | The rule encodes only the seven-day domestic threshold. Route international look-in claims to a human, or specify the fact that lets the fifteen-day threshold compute | unanswered |

RQ-10 is deliberately absent. It asked a reviewer to confirm the archived Part IV PDF matched the
document they read, and it was retired when the original PDF was committed and its SHA-256 became
test-verified. The number is not reused so the history stays auditable.

## 4. What an SME must supply to satisfy the loader

The loader enforces six preconditions before `POLICY_MODE=verified` will load this pack. Three are
already satisfied by the encoding; three require human evidence and nothing else.

| # | Precondition | Enforced by | State |
| --- | --- | --- | --- |
| 1 | `pack.yaml` `status: approved` | `_reject_for_mode` → `PACK_NOT_VERIFIED_ELIGIBLE` | **human decision** |
| 2 | `pack.yaml` `verified_mode_eligible: true` | `_reject_for_mode` → `PACK_NOT_VERIFIED_ELIGIBLE` | **human decision** |
| 3 | `review.yaml` `reviewer_name` **and** `approval` both non-empty | `_validate_rules` → `POLICY_PACK_UNAVAILABLE` | **human evidence** |
| 4 | Archived source verified: `archived: true`, `content_sha256` lowercase 64-hex, `local_path` present on disk, digest matches | `verify_source_document` → `SOURCE_DOCUMENT_UNVERIFIED` | satisfied |
| 5 | Every computational rule carries `source_clause_refs` | `_validate_rules` → `POLICY_PACK_UNAVAILABLE` | satisfied, 0 uncited |
| 6 | No rule marked `approved` inside a non-approved pack; no `superseded_suspected` rule left un-excluded | `_validate_rules` → `POLICY_PACK_UNAVAILABLE` | satisfied, 0 and 0 |

Precisely what the loader reads, and what it does not:

- It checks **only** `reviewer_name` and `approval`. `reviewer_role`, `reviewer_organisation`,
  `reviewed_at` and `rule_signoff` are this pack's own conventions, enforced by its tests rather
  than by the loader. A reviewer should still complete them — but nobody should believe the loader
  is checking them.
- `may_be_called_current_law` is `status == approved` **and** `verified_mode_eligible` **and**
  `source_document_verified`. The third is now true, so the property turns on items 1–3 alone.

Two consequences of promotion that are easy to miss:

- **Signing off changes the pack identity.** `review.yaml` is inside the pack hash, so recording a
  reviewer moves `pack_hash` away from `7c45e7b15ae54f6e`. Evaluations pinned to the current hash
  will refer to the pre-sign-off pack. Archiving the PDF did *not* have this effect, because
  `source-metadata.yaml` is deliberately outside the hash.
- **Some tests are meant to fail at that moment.**
  `tests/unit/policy/test_dgca_car_pack.py::TestApprovalIsGenuinelyPending` asserts that
  `review_status` is `pending`, that `reviewer_name` and `approval` are null, and that
  `rule_signoff` is empty. They exist to stop an accidental or unauthorised sign-off. When a real
  SME signs off, those assertions must be updated deliberately, in the same change, by someone who
  can point at the approval.

Answering RQ-1 through RQ-11 is not the same as satisfying items 1–3. A reviewer could answer every
question and still decline to approve.
