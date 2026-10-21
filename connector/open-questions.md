# Open Questions

Rewritten for email on 2026-10-21 against the infra spec §12, `V0_SCOPE.md`, playbook §15 and the earlier founder decisions. The teammates' email version is not pushed yet; the assumptions behind this list are in [email-pivot-assumptions.md](email-pivot-assumptions.md).

## Decided by the founders (still stands)
- [x] **Ask**: 30 minutes with the lead doctor; max 2 contacts per practice (playbook wins over the spec draft)
- [x] **Daily volume**: 10 per day. Decided for calls; carried over to emails as an assumption (A6)

## Decided for calls, now moot
- [x] AI-voice calls permitted, no audio kept, tone and speaker data in transcripts, voice provider, callback number. None applies to email. See [compliance.md](compliance.md).

## Resolved (provisionally, in v0)
- [x] Who generates the attempt id: infra (`email_attempt_id`)
- [x] Who owns the opt-out line and AI-assisted line: connector appends them to every email
- [x] Delivery to the Mailer: an `EmailRequest`
- [x] Brain template required fields: `ask`, `sender_name`, `subject`, `opt_out_line`, `script_version`
- [x] Raw or redacted threads: redacted to the Brain, raw kept in infra
- [x] Who picks the pitch angle: Brain guidance, connector picks the segment
- [x] Does email book the meeting: it proposes slots; Anirudh sends the invite and takes the meeting
- [x] Survivorship bias: no-replies and bounces produce a thread record with no inbound messages

## Still blocks live sending
- [ ] **Sending domain and reply mailbox** (SPF, DKIM, DMARC): assigned to eng
- [ ] **Mailer provider** (for example a transactional service or the Gmail API): eng
- [ ] **`{sender_postal_address}`**: required for CAN-SPAM; whose address?
- [ ] **Counsel review** of the email text, the AI-assisted line and sender identity (E1)
- [ ] **Code and config**: `config.toml` has `max_calls_per_day = 50`; the infra still implements the call flow. Both are the teammates' code.

## Email-specific
- [ ] E1: wording of the AI-assisted disclosure, and whether it is LOCKED
- [ ] Follow-up timing: 4 business days (assumption A5) or other?
- [ ] Do we send to a general `info@` address, or only to a named doctor's published address? It changes who replies
- [ ] Reply handling: does the Mailer classify replies, or does the Brain's reviewer (or an LLM pass checked by Anirudh)? How are forwards and auto-replies detected (A18)?
- [ ] Do we track opens? Assumed no (A14); weaker deliverability data as a result
- [ ] Web-form-only practices (SRC-006): in scope, or skipped?
- [ ] Warm-up plan for the new sending domain; is 10 per day enough to learn anything?

## Voice / infra team (now Mailer / infra)
- [ ] Mailer API limits, bounce and complaint webhooks, delivery latency
- [ ] Reply capture: webhook or mailbox polling; how threads are linked to an `email_attempt_id`
- [ ] Failure reporting channel
- [ ] Bulk export of past outreach

## Brain team
- [ ] Final `/v1/scripts/resolve` and `/v1/transcripts` schema (spec Q8): the transcript endpoint becomes a reply-thread endpoint
- [ ] Placeholder syntax: `{{double}}` vs `{snake_case}`
- [ ] How a new template version is announced; Anirudh approves each one
- [ ] Do `failure_category` and `lost_at` come from the connector or the Brain's reviewer?
- [ ] Email versions of playbook §5, §6 and §10 (call text there still stands until replaced)

## Founders / Anirudh
- [ ] Calendar: HBS Outlook or Google (spec Q4)
- [ ] Days he can visit in person, and travel distance
- [ ] Who reviews the first batch of drafts
- [ ] Contact data sources and budget (spec Q5); a published email address per practice is now essential
- [ ] Retention periods for contacts and reply threads (spec Q6)

## Analysis
- [ ] Who runs the analysis of past templates: connector joins the data, Brain reviewer interprets it (see [analysis-of-past-scripts.md](analysis-of-past-scripts.md))
