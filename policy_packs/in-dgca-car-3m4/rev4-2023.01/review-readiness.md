# G1 limited project approval — DGCA CAR Section 3, Series 'M', Parts IV and II

This pack is **approved only as a project policy artifact with limitations**. It is not DGCA-approved, not regulator-endorsed, not external legal or aviation-SME advice, and not verified as current law. `review.yaml` is the authoritative approval and question record.

| Field | Value |
| --- | --- |
| `status` | `approved` |
| `verified_mode_eligible` | `false` |
| `review_status` | `approved_for_project_use_with_limitations` |
| `pack_hash` | `c0e80df6298080e6` |
| `source_document_verified` | `true` |
| `may_be_called_current_law` | `false` |
| Project approver | `Project owner (project-provided approver)` |
| Approval date and scope | `2026-08-21`; `project_policy_artifact` |

## 1. Archived-source verification

The real archived PDF bytes and provenance are unchanged:

| Source | SHA-256 | Bytes | Verification record |
| --- | --- | ---: | --- |
| Part IV `source.pdf` | `3b4b50844edc6a46099cce3b94626d29b03f90ccc61318aa1aa2db6d0fa3ff4a` | 285108 | text layer available |
| Part II `source-part-ii.pdf` | `558ecee1d535b63023dd8bd37f0de10d08e7eb82a37ec48817ec1d613ff09281` | 2754578 | scan; visual review required |

`clause-verification.yaml` covers all 44 rules exactly once: 30 Part IV rules are recorded as `text_layer_verified`; 14 Part II rules are `visual_review_required`; no computational rule lacks a clause reference. The new international look-in guard cites the same Part II Para 3(e) branch as the domestic rule and does not invent an entitlement.

Source integrity proves that the archived bytes match their recorded hashes. It does **not** prove currentness, absence of supersession, DGCA endorsement, or legal correctness of an interpretation.

## 2. RQ dispositions

| RQ | Affected rule(s) | Source clause | Project disposition | Runtime or scope consequence |
| --- | --- | --- | --- | --- |
| RQ-1 | `delay.care.hotel.long_or_night_window` | `3.4.3` | `resolved_project_decision` | Keep the encoded hotel rule without an advance-notice condition; the archived clause contains none. |
| RQ-2 | `delay.exemption.extraordinary_circumstances` | `3.4.4`, `3.8.1` | `resolved_project_decision` | Keep suppression of the Para 3.8 meals and hotel facilities when the exemption is fully evidenced. |
| RQ-3 | three `cancellation.compensation.*` cash rules | `3.3.2` | `operational_scope_required` | No alternate-versus-compensation interpretation is invented. Missing `cancellation.compensation_branch_confirmed_by_project_reviewer` returns `needs_human`. |
| RQ-4 | six denied-boarding/cancellation cash rules | `3.2.2`, `3.3.2` | `operational_scope_required` | No fare-component definition is invented. Missing `fare.component_definition_confirmed_by_project_reviewer` returns `needs_human`. |
| RQ-5 | cancellation and delay extraordinary-circumstance exemptions | `1.4`, `1.5`, `3.3.4`, `3.4.4` | `operational_scope_required` | No universal evidence standard is invented. Partial evidence across `external_to_carrier`, `unavoidable_despite_reasonable_measures`, and `evidence_refs` returns `needs_human`. |
| RQ-6 | `foreign_carrier.compensation_basis` | `3.6.1` | `resolved_project_decision` | Preserve fail-closed jurisdiction deferral; choose no precedence. |
| RQ-7 | `denied_boarding.connecting_flight_first_leg` | `3.2.3` | `resolved_project_decision` | Keep informational and outside MVP computation. |
| RQ-8 | `downgrading.involuntary.reimbursement` | `3.5.1` | `resolved_project_decision` | Keep informational and outside MVP computation; invent no ticket-cost formula or distance fact. |
| RQ-9 | every rule | `1.1`, `3.3.5` | `accepted_external_risk` | Charter-mode project use accepts the disclosed risk only. `currentness_asserted: false`; this item blocks verified mode. |
| RQ-11 | domestic and international `refund.look_in_option*` rules | Part II `3(e)` | `resolved_project_decision` | Domestic seven-day branch remains deterministic; international fifteen-day branch requires `request.international_look_in_decision_confirmed_by_project_reviewer`. |

RQ-10 remains retired because the original Part IV PDF is byte-archived and hash-verified; its identifier is not reused.

## 3. Exact verified-mode blockers

`status: approved` records the project owner's artifact approval. It does not make the pack verified-eligible. `verified_mode_eligible` remains `false` for these exact reasons:

1. **RQ-3 — unresolved external interpretation:** no external aviation/legal SME decision establishes when an accepted alternate suppresses or selects the Para 3.3.2 compensation branch. The project gate permits case-by-case charter-mode review but does not resolve the interpretation.
2. **RQ-4 — unresolved external definitions:** no external aviation/legal SME evidence defines the legally valid composition of “booked one-way basic fare” and “airline fuel charge.” The project gate validates case inputs operationally but does not create those definitions.
3. **RQ-5 — unresolved external evidentiary standard:** no external aviation/legal SME standard establishes what proves that an event could not have been avoided despite reasonable measures. The existing evidence facts fail closed but are not a legal standard.
4. **RQ-9 — authoritative currentness/supersession evidence absent:** no authoritative DGCA evidence establishes that Part IV Rev. 4 remains current or conclusively resolves the Para 3.3.5 cross-reference after later Part II Rev. 3. The project accepted this external risk only for charter-mode artifact use and explicitly did not assert currentness.
5. After those facts exist, a later explicit project decision and new pack hash must set `verified_mode_eligible: true`.

The loader therefore rejects this pack in `POLICY_MODE=verified` with `PACK_NOT_VERIFIED_ELIGIBLE`. The current-law standing check also remains false because it requires all three conditions: approved status, verified eligibility, and verified source bytes.

## 4. Promotion controls now satisfied

| Control | State |
| --- | --- |
| Project artifact approval recorded with non-regulatory scope | satisfied |
| `reviewer_name` and `approval` non-empty for approved status | satisfied |
| Rule sign-off IDs exactly cover all 44 rules | satisfied for project use with limitations; `regulatory_approval: false` |
| Archived source path, size, SHA-256 and byte integrity | satisfied for both PDFs |
| Every computational rule carries `source_clause_refs` | satisfied |
| No unexcluded `superseded_suspected` rule | satisfied |
| Verified eligibility | intentionally unsatisfied for the blockers in section 3 |

Any future verified promotion must add real evidence rather than relabel this approval. It must not replace the project approver with an invented regulator or SME identity and must not describe this project decision as DGCA endorsement or legal certainty.
