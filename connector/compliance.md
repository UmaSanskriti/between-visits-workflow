# Compliance

**Not legal advice.** Confirm with the course instructor or an attorney before any live calls.

## Key risks

- **TCPA / FCC**: the FCC treats AI-generated voices as "artificial voices", which can require prior express consent, especially for cell phones. Needs a decision before dialing.
- **Disclosure**: the agent must say upfront that it is an AI calling on behalf of our team. Doctors react badly to undisclosed bots.
- **Do-not-call**: check internal and national DNC status before queuing; honor opt-outs immediately.
- **Recording**: some states require all-party consent. Disclose recording at the start of the call.
- **PII in transcripts**: decide what the Brain stores (names, phone, emails). Default proposal: redact before sending.

## Connector responsibilities

- Insert the AI disclosure line into every script; block the call if absent.
- Exclude doctors with `dnc = true` or a prior opt-out.
- Only queue within permitted calling hours for the doctor's time zone.
- Log every script generated, sent, and received (audit log).

## Decisions needed

1. Who owns the disclosure and recording lines: Voice or Connector?
2. Consent approach (instructor/attorney guidance)
3. Transcript retention and redaction policy
4. Opt-out handling flow back to the DNC list
