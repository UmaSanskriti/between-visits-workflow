# Brain Interface

Status: DRAFT. Items marked **[CONFIRM]** need an answer from the Brain team.

## Receive from Brain

- **Base script template** with defined placeholders (e.g. `{doctor_name}`, `{specialty}`); see [script-variables.md](script-variables.md)
- **Core context**: who we are, the startup idea, why we're calling, the exact ask (20 minutes), objection handling, banned claims
- **Segment guidance**: e.g. solo practice vs. small group, specialty-specific angles
- **Version ID** on every template, so each call traces to a script version
- **Variant feedback**: which script variants worked, so personalization improves

## Send to Brain

**Transcript package**, one per call:
- Cleaned transcript
- Doctor metadata (segment, specialty, practice size), PII handled per [compliance.md](compliance.md)
- Outcome, script_version, call_id

**Outcome summary**: calls made, booked, declined, conversion by script version and segment. Include declines and no-answers so the Brain does not learn only from friendly doctors.

## To pin down **[CONFIRM]**

1. Transcript schema the Brain wants
2. How often the template is updated; how I learn a new version is live
3. Delivery format and mechanism for the template (file, API)
4. Who decides which pitch angle leads per segment: Brain (rules) or Connector (logic)?
5. Whether the Brain wants raw or redacted transcripts
