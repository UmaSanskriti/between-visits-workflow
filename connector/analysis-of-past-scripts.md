# Analysis of Past Scripts

Status: DRAFT, rewritten for email. This is where the connector learns from earlier emails and replies. It supports the Brain's own loops: the per-contact reviewer loop (playbook §11) and the monthly refresh (playbook §12). "Script" here means the email template; `script_version` is its version.

## Who does what
- **Connector**: joins outcomes, failure categories and reply threads to `script_version` and segment, then produces the numbers and flags.
- **Brain reviewer**: reads the threads, assigns categories, writes template v(n+1) with a changelog (playbook §11).
- **Anirudh**: approves every new template version before the next batch uses it.

## Inputs
- Past templates and version IDs, plus the playbook changelog (from Brain: [inputs/from-brain.md](inputs/from-brain.md))
- Email attempts, outcomes, `failure_category`, `lost_at`, and reply threads (from infra: [inputs/from-infra.md](inputs/from-infra.md))
- Any past call transcripts from before the move to email, kept as history only and reported separately from email results

## Measures, per template version and segment
These match the monthly measure step (playbook §12), with "reply" in place of "reaching a person":
1. Emails sent
2. % delivered (not bounced)
3. % with a human reply (auto-replies excluded)
4. % booked
5. Top 3 failure categories ([data-schema.md](data-schema.md))

Also track:
- Where it was lost (`lost_at`): delivery, no reply, declined in reply, or scheduling
- Reply time, to set the follow-up delay (assumption A5)
- Subject lines and opening lines that earned replies, and objections raised in replies and which answers worked
- Differences by segment (solo vs. group, specialty) and by address type (named doctor vs. `info@`)
- Replies from someone other than the doctor, since a forward changes who we are persuading
- Send day and hour

## Method
- Join on `email_attempt_id` to `script_version` and the contact's segment.
- Count a change as effective only with at least 5 emails (the playbook's promote/retire threshold); flag smaller samples as inconclusive.
- **Bias guard:** include bounces, no-replies and declines, not only booked threads. Report bounces separately so deliverability problems do not look like pitch failures.
- No open or click tracking (assumption A14), so "not opened" and "ignored" cannot be told apart.
- Every 10 emails, produce the inputs for a pattern note: most common categories, which fixes worked, which did not.

## Hard limit
Analysis may suggest changes to categories the playbook allows for each failure type. It may **never** propose changes to LOCKED text (§5 and the opt-out line). Suggestions touching LOCKED text go to Anirudh and the lawyer as flags only.

## Outputs
- Recommended pitch angle per segment, feeding `{pitch_angle}` ([script-variables.md](script-variables.md))
- Joined data and flags sent to the Brain ([outputs/to-brain.md](outputs/to-brain.md))

## Open questions
- [ ] How many past outreach attempts exist, and who holds them? The playbook says "Learned so far" stays empty until about 20 real contacts.
- [ ] Is categorization manual, or an LLM pass checked by the reviewer?
- [ ] With 10 emails a day, how many weeks until the 5-email threshold has any power per segment?
