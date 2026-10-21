# Connector Specs

Customer-discovery system for the Launching Tech Ventures project. AB helps independent primary care and weight-loss practices give GLP-1 patients complete care. We are emailing practices to book **30-minute research conversations** with the lead doctor.

> **Status: email-first draft.** Outreach moved from phone calls to email. The teammates' playbook, infra spec and `V0_SCOPE.md` still describe the call flow, and the code in `infra/` still implements it. These docs describe the email target, built on the assumptions in [email-pivot-assumptions.md](email-pivot-assumptions.md). When the teammates push their email version, reconcile against it; their documents win.

| Component | Owner | Job |
|-----------|-------|-----|
| **Mailer** | Infra team | Sends approved emails, tracks bounces and replies, logs outcomes and threads |
| **Brain** | Brain team | Playbook, base email template, segment guidance; reviews reply threads and writes the next template version |
| **Connector** (this folder) | Me | Merges contact data with the Brain's template into a personalized email per contact; routes outcomes and reply threads back to the Brain |

## Sources of truth

When documents disagree, the higher one wins.

1. [`clinic-outreach-brain.md`](../clinic-outreach-brain.md): the Brain playbook (v1). LOCKED text can never be changed by the connector. Section 8 (emails) is the base for this folder; the call sections (5 to 7) are superseded by the email pivot once the teammates confirm it.
2. [`AB calling agent infra_ requirements specification.md`](<../AB calling agent infra_ requirements specification.md>): infra spec (requirement IDs such as CMP-004, BRN-001). Call-specific requirements do not apply to email.
3. [`V0_SCOPE.md`](../V0_SCOPE.md): what v0 builds and the provisional interface decisions
4. This folder: the connector's contract, which should match the three above

## Data flow

```
Seed list -> Infra (verify, send gate) -> CONNECTOR <-- template + guidance -- Brain
                                              |
                       personalized email + send queue
                                              v
                                           Mailer
                                              |
                  delivery events + replies -> Infra DB -> redacted thread package -> Brain
```

## Connector outputs

1. Personalized email per contact, delivered as an `EmailRequest` ([outputs/to-mailer.md](outputs/to-mailer.md))
2. Send queue, already filtered by the send gate
3. Redacted thread package for the Brain ([outputs/to-brain.md](outputs/to-brain.md))
4. Outcome summary by template version and segment
5. Audit log, error report, dead-letter queue ([outputs/audit-log-and-errors.md](outputs/audit-log-and-errors.md))

## Docs

**Inputs**
- [inputs/from-infra.md](inputs/from-infra.md): contacts, delivery events, replies
- [inputs/from-brain.md](inputs/from-brain.md): playbook, email template, segment guidance

**Outputs**
- [outputs/to-mailer.md](outputs/to-mailer.md): personalized emails and send queue
- [outputs/to-brain.md](outputs/to-brain.md): thread packages, outcome summary
- [outputs/audit-log-and-errors.md](outputs/audit-log-and-errors.md): audit log and error report

**Analysis and reference**
- [email-pivot-assumptions.md](email-pivot-assumptions.md): every assumption made in the move to email, to confirm or reverse
- [analysis-of-past-scripts.md](analysis-of-past-scripts.md): learning from past templates and replies
- [script-variables.md](script-variables.md): every placeholder, source, fallback
- [data-schema.md](data-schema.md): contact, email attempt, template, reply thread records
- [compliance.md](compliance.md): sender identity, AI disclosure, opt-out, CAN-SPAM
- [open-questions.md](open-questions.md): unresolved items per team
- [test-plan.md](test-plan.md): sample contacts and edge cases

## Design principles

- The connector does more than mail-merge: it picks the segment and the pitch angle per contact.
- Every email carries a `script_version` (the template version), so each send traces to the template that produced it.
- The connector never edits or removes LOCKED text, and the opt-out line is always present.
- Guard against feedback bias: the Brain sees every sent email, including bounces, no-replies and declines.
- The email asks for a 30-minute conversation with the lead doctor. A person at the practice replies, and Anirudh approves each calendar invite and takes the meeting.
