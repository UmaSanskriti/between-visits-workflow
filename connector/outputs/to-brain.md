# Output: To Brain

Status: DRAFT. Items marked **[CONFIRM]** need an answer from the Brain team.

## 3. Transcript package (one per call)
- Cleaned transcript
- Doctor metadata (segment, specialty, practice size), PII handled per [../compliance.md](../compliance.md)
- Outcome, script_version, call_id

Shape: [../data-schema.md](../data-schema.md)

## 4. Outcome summary
Calls made, booked, declined, and conversion by script version and segment. Includes declines and no-answers so the Brain does not learn only from friendly doctors.

## 5. Analysis of past scripts
Findings from [../analysis-of-past-scripts.md](../analysis-of-past-scripts.md), sent as recommended template changes.

## To pin down **[CONFIRM]**
1. Transcript schema the Brain wants
2. Raw or redacted transcripts
3. Delivery format and frequency
