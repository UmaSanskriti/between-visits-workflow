# Connector Specs

Customer-discovery system for the Launching Tech Ventures project. AB helps independent primary care and weight-loss practices give GLP-1 patients complete care. We are calling practices to book **30-minute research conversations** with the lead doctor.

| Component | Owner | Job |
|-----------|-------|-----|
| **Voice agent** | Voice / infra team | Places the calls (Vapi, backup Retell), logs outcomes and transcripts |
| **Brain** | Brain team | Playbook, base script, segment guidance; reviews transcripts and writes the next script version |
| **Connector** (this folder) | Me | Merges contact data with the Brain's script into a personalized per-call script; routes outcomes and transcripts back to the Brain |

## Sources of truth

When documents disagree, the higher one wins.

1. [`clinic-outreach-brain.md`](../clinic-outreach-brain.md): the Brain playbook (v1). LOCKED text can never be changed by the connector.
2. [`AB calling agent infra_ requirements specification.md`](<../AB calling agent infra_ requirements specification.md>): infra spec (requirement IDs such as CMP-004, BRN-001)
3. [`V0_SCOPE.md`](../V0_SCOPE.md): what v0 builds and the provisional interface decisions
4. This folder: the connector's contract, which should match the three above

## Data flow

```
Seed list -> Infra (verify, compliance gate) -> CONNECTOR <-- script + guidance -- Brain
                                                    |
                          personalized script + call queue
                                                    v
                                              Voice provider
                                                    |
                          outcomes + transcripts -> Infra DB -> redacted transcript package -> Brain
```

## Connector outputs

1. Personalized script per call, delivered as a `CallRequest` ([outputs/to-voice.md](outputs/to-voice.md))
2. Call queue, already filtered by the compliance gate
3. Redacted transcript package for the Brain ([outputs/to-brain.md](outputs/to-brain.md))
4. Outcome summary by script version and segment
5. Audit log, error report, dead-letter queue ([outputs/audit-log-and-errors.md](outputs/audit-log-and-errors.md))

## Docs

**Inputs**
- [inputs/from-voice.md](inputs/from-voice.md): contacts, call outcomes, transcripts
- [inputs/from-brain.md](inputs/from-brain.md): playbook, script template, segment guidance

**Outputs**
- [outputs/to-voice.md](outputs/to-voice.md): personalized scripts and call queue
- [outputs/to-brain.md](outputs/to-brain.md): transcript packages, outcome summary
- [outputs/audit-log-and-errors.md](outputs/audit-log-and-errors.md): audit log and error report

**Analysis and reference**
- [analysis-of-past-scripts.md](analysis-of-past-scripts.md): learning from past scripts and calls
- [script-variables.md](script-variables.md): every placeholder, source, fallback
- [data-schema.md](data-schema.md): contact, call attempt, script, transcript records
- [compliance.md](compliance.md): AI disclosure, consent, DNC, recording
- [open-questions.md](open-questions.md): unresolved items per team
- [test-plan.md](test-plan.md): sample contacts and edge cases

## Design principles

- The connector does more than mail-merge: it picks the segment and the pitch angle per contact.
- Every script carries a `script_version`, so each call traces to the script that produced it.
- The connector never edits or removes LOCKED text; the AI and recording disclosure is always prepended (BRN-001).
- Guard against feedback bias: the Brain sees every completed call, including no-answers and declines.
- The agent books the 30-minute conversation with a person at the practice; Anirudh approves each calendar invite and takes the meeting.
