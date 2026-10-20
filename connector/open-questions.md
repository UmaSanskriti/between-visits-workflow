# Open Questions

Updated against the infra spec §12, `V0_SCOPE.md` and playbook §15.

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

## Blocks live calls
- [ ] **Legal position on AI-voice calls** (counsel): TCPA consent and clearance process (spec Q2)
- [ ] **Voice provider** decision: build vs. buy; the playbook names Vapi (spec Q1)
- [ ] **Recording policy**: record, or transcript only (spec Q3)
- [ ] **Volume and spend cap** (spec Q7); the free Vapi number allows 10 calls/day
- [ ] **Callback number** (playbook §15), required before any live call

## Voice / infra team
- [ ] Vapi prompt and first-message length limits; name pronunciation
- [ ] Outcome and transcript latency; failure reporting channel
- [ ] Bulk export of past calls
- [ ] Caller ID registration for spam-label reputation

## Brain team
- [ ] Final `/v1/scripts/resolve` and `/v1/transcripts` schema and ownership (spec Q8)
- [ ] Placeholder syntax: `{{double}}` vs `{snake_case}`
- [ ] How a new script version is announced; Anirudh approves each one
- [ ] Do `failure_category` and `lost_at` come from the connector or the Brain's reviewer?

## Founders / Anirudh
- [ ] Calendar: HBS Outlook or Google (spec Q4)
- [ ] Days he can visit in person, and travel distance
- [ ] Who listens to the first live call
- [ ] Contact data sources and budget (spec Q5); target list (playbook §3 defines it; seed-only for now)
- [ ] Retention periods (spec Q6)

## Analysis
- [ ] Who runs the analysis of past scripts: connector joins the data, Brain reviewer interprets it (see [analysis-of-past-scripts.md](analysis-of-past-scripts.md))
