# Open Questions

Updated against the infra spec §12, `V0_SCOPE.md`, playbook §15 and the founder decisions of 2026-10-20.

## Decided by the founders
- [x] **AI-voice calls are permitted** (spec Q2). Recorded as a founder decision. The per-channel `call_clearance` gate stays in place, and the connector still needs an authorized process to set it ([compliance.md](compliance.md)).
- [x] **Recording policy** (spec Q3): no audio files are kept. The full transcript is the record. In its place the transcript must carry speaker type and tone/intonation data ([outputs/to-brain.md](outputs/to-brain.md)).
- [x] **Daily volume** (spec Q7): 10 calls per day.
- [x] **Voice provider** (spec Q1): left to the eng team. The playbook names Vapi, backup Retell.
- [x] **Callback number**: left to the eng team.

## Resolved (provisionally, in v0)
- [x] Who generates call_id: infra (`call_attempt_id`)
- [x] Who owns AI and recording disclosure: infra, prepended to every script
- [x] Script delivery to Voice: a `CallRequest`
- [x] Brain template required fields: `ask`, `caller_identity`, `script_version`
- [x] Raw or redacted transcripts: redacted to the Brain, raw kept in infra
- [x] Who picks the pitch angle: Brain guidance, connector picks the segment
- [x] Does the agent only book, or run the interview: books only; Anirudh takes the meeting
- [x] Survivorship bias: no-answers produce an empty transcript record
- [x] Ask length and attempts: 30 minutes, max 2 (playbook wins over the spec draft)

## Still blocks live calls
- [ ] **Callback number**: assigned to eng; required before any live call (an empty one blocks calls)
- [ ] **Voice provider**: assigned to eng; needed before a sandbox integration
- [ ] **Set `max_calls_per_day` to 10** in `config.toml` (it is 50 for the dry run)

## New: transcript must carry speaker and tone data
Because no audio is kept, the following must be captured at call time and stored with the transcript.
- [ ] Who produces tone/intonation data: the voice provider, or an analysis step run on the live audio and then discarded? **[CONFIRM]** with eng
- [ ] Speaker type per utterance: AI agent, human, IVR / phone tree, voicemail greeting. Is this reliable from the provider?
- [ ] Speaker man/woman: is it needed, and is it acceptable to infer it from voice? It is sensitive, so counsel should confirm. Consider recording only what the person says, such as how they are addressed.
- [ ] Does the LOCKED opening ("recorded for our notes") still describe what happens when audio is analysed but not stored? Only Anirudh and the lawyer can change it.
- [ ] How do we validate that tone labels are accurate enough for the Brain to rely on?

## Voice / infra team
- [ ] Vapi prompt and first-message length limits; name pronunciation
- [ ] Outcome and transcript latency; failure reporting channel
- [ ] Bulk export of past calls
- [ ] Caller ID registration for spam-label reputation

## Brain team
- [ ] Final `/v1/scripts/resolve` and `/v1/transcripts` schema and ownership (spec Q8), including the new speaker and tone fields
- [ ] Placeholder syntax: `{{double}}` vs `{snake_case}`
- [ ] How a new script version is announced; Anirudh approves each one
- [ ] Do `failure_category` and `lost_at` come from the connector or the Brain's reviewer?

## Founders / Anirudh
- [ ] Calendar: HBS Outlook or Google (spec Q4)
- [ ] Days he can visit in person, and travel distance
- [ ] Who listens to the first live call
- [ ] Contact data sources and budget (spec Q5); target list (playbook §3 defines it; seed-only for now)
- [ ] Retention periods for contacts and transcripts (spec Q6); audio retention no longer applies

## Analysis
- [ ] Who runs the analysis of past scripts: connector joins the data, Brain reviewer interprets it (see [analysis-of-past-scripts.md](analysis-of-past-scripts.md))
