# Analysis of Past Scripts

Status: DRAFT. This is where the connector learns from earlier calls. It supports the Brain's own loops: the per-call reviewer loop (playbook §11) and the monthly refresh (playbook §12).

## Who does what
- **Connector**: joins call outcomes, failure categories and transcripts to `script_version` and segment, then produces the numbers and flags.
- **Brain reviewer**: reads the transcripts, assigns categories, writes script v(n+1) with a changelog (playbook §11).
- **Anirudh**: approves every new script version before the next call uses it.

## Inputs
- Past scripts and version IDs, plus the playbook changelog (from Brain: [inputs/from-brain.md](inputs/from-brain.md))
- Call attempts, outcomes, `failure_category`, `lost_at`, and transcripts (from infra: [inputs/from-voice.md](inputs/from-voice.md))

## Measures, per script version and segment
These match the monthly measure step (playbook §12):
1. Calls placed
2. % reaching a person
3. % booked
4. Top 3 failure categories (playbook §10)

Also track:
- Where in the call it was lost (`lost_at`), especially the first 15 seconds at the AI disclosure
- Recurring objections and which responses worked
- Differences by segment (solo vs. group, specialty)
- Tone and intonation around the failure point, for example a rushed or annoyed reply right after the AI disclosure. With no audio kept, this comes only from the per-utterance tone data, so its quality limits this analysis.
- Whether the other party was a human, an IVR or a voicemail greeting, so tree and voicemail calls do not distort pitch results

## Method
- Join on `call_attempt_id` to `script_version` and the contact's segment.
- Count a change as effective only with at least 5 calls (the playbook's promote/retire threshold); flag smaller samples as inconclusive.
- **Bias guard:** include declines, voicemails and no-answers, not only booked calls.
- Every 10 calls, produce the inputs for a pattern note: most common categories, which fixes worked, which did not.

## Hard limit
Analysis may suggest changes to categories the playbook allows for each failure type (§10). It may **never** propose changes to LOCKED text (§5, the Open, the Close). Suggestions touching LOCKED text go to Anirudh and the lawyer as flags only. For "hung up at AI disclosure", only the *delivery* of the opener may change, never the disclosure.

## Outputs
- Recommended pitch angle per segment, feeding `{pitch_angle}` ([script-variables.md](script-variables.md))
- Joined data and flags sent to the Brain ([outputs/to-brain.md](outputs/to-brain.md))

## Open questions
- [ ] How many past calls exist, and who holds them? The playbook says "Learned so far" stays empty until about 20 real calls.
- [ ] Is categorization manual, or an LLM pass checked by the reviewer?
