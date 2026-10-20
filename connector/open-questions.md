# Open Questions

## Voice team
- [ ] Script delivery: API, webhook, or file? Format?
- [ ] Prompt and first-message length limits
- [ ] Supported variables; name-pronunciation handling
- [ ] Who owns AI disclosure, voicemail behavior, retries, calling hours, opt-outs
- [ ] How and how fast are failures reported
- [ ] Who generates call_id

## Brain team
- [ ] Transcript schema and delivery method
- [ ] Template update cadence and version signaling
- [ ] Who chooses the pitch angle per segment (Brain rules vs. Connector logic)
- [ ] Raw or redacted transcripts
- [ ] How variant feedback is returned

## Compliance / course
- [ ] Is AI-voice cold calling permitted for this project (TCPA consent)?
- [ ] Recording consent approach by state
- [ ] Retention and redaction policy

## Product
- [ ] Does the agent only book, or also run the interview? (Current assumption: book only; human interviews.)
- [ ] How to avoid survivorship bias in what the Brain learns
