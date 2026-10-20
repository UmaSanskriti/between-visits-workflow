# Seeds

Inputs for the v0 dry run. **Every record here is fictional.** Phone numbers use the
555-01xx range reserved for fiction; emails use the `.test` domain.

| File | Spec | What it is |
|---|---|---|
| `test_doctors.csv` | SRC-001 seed input | The sample doctors from `connector/test-plan.md` (t_01 to t_08) plus outcome edge cases (t_09 to t_20). The `scenario` column drives the **mock** voice provider only and is ignored by real sourcing. |
| `clearances.csv` | CMP-003 | The stand-in for the authorized clearance process. Only phone numbers listed here can be dialed. t_16 is deliberately missing so the gate has something to block. |
| `suppressions.csv` | VER-006 / CMP-006 | Seeded do-not-contact entries. t_05 is suppressed here and must never be called. |

Scenario values: `booked`, `declined`, `no_answer`, `voicemail`, `callback`, `gatekeeper`,
`wrong_number`, `technical`, `stop_calling`, `malformed_transcript`, `patient_data`,
`delivery_fails`, `not_a_fit`, `declined_recording`. Blank picks a deterministic default.

The `hook` column is playbook section 4: one specific line from the practice website, woven into the pitch when present.
